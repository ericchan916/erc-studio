import contextlib
import hashlib
import json
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND=Path(__file__).resolve().parents[1]
ROOT=BACKEND.parent/'shared'
sys.path.insert(0,str(ROOT/'bridge/tests'))
sys.path.insert(0,str(ROOT/'bridge'))
sys.path.insert(0,str(BACKEND))
from core import Service
from domain import Problem
from server import handler_for,ThreadingHTTPServer
from bridge_native_feishu import NativeFeishuAgent,NativeAuthorizationRequired
from bridge_state import StateStore
from bridge_studio_execution import StudioExecution,server_for
from conversation_protocol import request,ProtocolError
from test_native_feishu import NativeFixture


class Sender:
    def ensure_active(self, member):pass

class ForbiddenTextFallback:
    def __init__(self):self.calls=[]
    def complete(self,*args):self.calls.append(args);raise AssertionError('plain text fallback forbidden')


class GatewayExecutionTests(NativeFixture):
    def setUp(self):
        super().setUp()
        self.owner=str(uuid.uuid4());self.other=str(uuid.uuid4())
        self.key_file=Path(self.tmp.name)/'ipc.key';self.key_file.write_text('2'*64);self.key_file.chmod(0o600)
        root=Path(self.tmp.name)/'contexts';directory=root/hashlib.sha256(b'ou_A').hexdigest();self.prepare(directory,'ou_A')
        self.client=mock.Mock();self.client.tool_turn.return_value={'content':'shared native answer'}
        self.native=NativeFeishuAgent(['ou_A'],root,self.client,self.runner);self.cli=self.native.contexts['ou_A']
        self.member={'principal_id':self.owner,'handle':'a','name':'A','open_id':'ou_A','chat_id':'oc_A','tenant_key':'fixture'}
        self.bridge=SimpleNamespace(store=StateStore(Path(self.tmp.name)/'bridge.db'),isolated_users={'ou_A'},
            isolated_private_chats={'oc_A':'ou_A'},isolated_handler=SimpleNamespace(native=self.native,locks={'ou_A':threading.RLock()}),
            allowed_senders=set(),allowed_chats=set(),config={},native_interactions=None,native_channel_ok=True,
            _execution_allowed=lambda force=False:True,_idle_sleep_assertion=lambda:contextlib.nullcontext(True),
            _send_native_authorization=mock.Mock(),sender=mock.Mock(),agent_runtime=None)
        self.execution=StudioExecution(self.bridge,[self.member],lambda body:self.service.execution.lease({k:body[k] for k in ('owner','job_id','conversation_id','attempt_id')})['valid'])
        self.peer=server_for(self.execution,self.key_file,0);threading.Thread(target=self.peer.serve_forever,daemon=True).start()
        self.model=ForbiddenTextFallback()
        self.config={'state_dir':str(Path(self.tmp.name)/'gateway'),'auth_pepper':'fixture-pepper','feishu_profile':'fixture',
            'members':[self.member,{**self.member,'principal_id':self.other,'handle':'b','name':'B','open_id':'ou_B','chat_id':'oc_B'}],
            'unified_conversations':{'enabled':True,'execution':{'bridge_origin':'http://127.0.0.1:'+str(self.peer.server_port),
                'key_file':str(self.key_file),'timeout_seconds':30}}}
        self.service=Service(self.config,self.model,Sender())
        self.gateway=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.service));host='127.0.0.1:'+str(self.gateway.server_port)
        self.config.update(allowed_hosts=[host],allowed_origins=['http://'+host])
        self.gateway_origin='http://'+host;threading.Thread(target=self.gateway.serve_forever,daemon=True).start()

    def tearDown(self):
        self.gateway.shutdown();self.gateway.server_close();self.peer.shutdown();self.peer.server_close();self.service.close();super().tearDown()

    def wait(self, receipt, states=('succeeded','failed','execution_unknown','waiting_input','waiting_confirmation','waiting_authorization','paused','cancelled')):
        for _ in range(200):
            job=self.service.job(self.owner,receipt['job_id'])
            if job['state'] in states:return job
            time.sleep(.01)
        self.fail('job_did_not_reach_expected_state')

    def send(self, text='native question'):
        return self.service.start_message(self.owner,{'title':'QA','text':text,'client_message_id':str(uuid.uuid4())},str(uuid.uuid4()),'web')

    def test_gateway_worker_executes_native_once_without_plain_model_fallback(self):
        first=self.send();job=self.wait(first)
        self.assertEqual(job['state'],'succeeded');self.assertEqual(job['effect_state'],'none')
        self.assertEqual(self.client.tool_turn.call_count,1);self.assertEqual(self.model.calls,[])
        self.assertEqual([r['text'] for r in self.service.messages(self.owner,first['conversation_id'])['items']],['native question','shared native answer'])

    def test_clarification_reply_resumes_original_job_and_native_task(self):
        self.client.tool_turn.side_effect=[{'tool_calls':[{'id':'q','function':{'name':'ask_user',
            'arguments':json.dumps({'question':'which?','options':['A','B']})}}]},{'content':'based on A'}]
        first=self.send();self.assertEqual(self.wait(first)['state'],'waiting_input')
        body={'text':'A','client_message_id':str(uuid.uuid4())};key=str(uuid.uuid4())
        reply=self.service.respond_job(self.owner,first['job_id'],body,key,'web')
        self.assertEqual(reply['job_id'],first['job_id']);self.assertEqual(self.wait(first,('succeeded','failed'))['state'],'succeeded')
        again=self.service.respond_job(self.owner,first['job_id'],body,key,'web');self.assertEqual(again['job_id'],first['job_id'])
        with self.cli.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM native_tasks').fetchone()[0],1)
        self.assertEqual(self.client.tool_turn.call_count,2);self.assertEqual(self.model.calls,[])

    def test_signed_feishu_ingress_joins_existing_web_context(self):
        first=self.send('web marker');self.wait(first)
        event={**self.event,'chat_type':'p2p','message_type':'text','message_id':'om_fixture','content':'feishu follows'}
        self.bridge.store.add_event(event)
        body={'open_id':'ou_A','tenant_key':'fixture','chat_id':'oc_A','source_id':'om_fixture',
              'source_event_id':event['event_id'],'text':'feishu follows'}
        receipt=request(self.gateway_origin,'/internal/studio/ingest',body,self.key_file)
        self.assertEqual(receipt['conversation_id'],first['conversation_id']);self.assertEqual(self.wait(receipt)['state'],'succeeded')
        actual=self.client.tool_turn.call_args[0][0];self.assertIn('web marker',json.dumps(actual))
        again=request(self.gateway_origin,'/internal/studio/ingest',body,self.key_file)
        self.assertEqual(again['job_id'],receipt['job_id']);self.assertEqual(self.client.tool_turn.call_count,2)

    def test_signed_wrong_tenant_or_private_chat_is_denied_without_model(self):
        body={'open_id':'ou_A','tenant_key':'wrong-tenant','chat_id':'oc_A','source_id':'om_fake','source_event_id':'ev_fake','text':'no'}
        with self.assertRaises(ProtocolError) as e:request(self.gateway_origin,'/internal/studio/ingest',body,self.key_file)
        self.assertEqual(e.exception.status,403);self.assertFalse(e.exception.uncertain)
        with self.assertRaises(ProtocolError):request(self.gateway_origin,'/internal/studio/ingest',{**body,'tenant_key':'fixture','chat_id':'oc_OTHER'},self.key_file)
        self.client.tool_turn.assert_not_called();self.assertEqual(self.service.one('SELECT count(*) FROM jobs')[0],0)

    def test_other_member_cannot_control_or_lease_original_job(self):
        self.client.tool_turn.return_value={'tool_calls':[{'id':'q','function':{'name':'ask_user','arguments':json.dumps({'question':'which?','options':['A']})}}]}
        first=self.send();self.wait(first)
        with self.assertRaises(Problem):self.service.respond_job(self.other,first['job_id'],{'text':'A','client_message_id':str(uuid.uuid4())},str(uuid.uuid4()))
        with self.assertRaises(Problem):self.service.execution.lease({'owner':self.other,'job_id':first['job_id'],'conversation_id':first['conversation_id'],'attempt_id':str(uuid.uuid4())})
        self.assertEqual(self.client.tool_turn.call_count,1)

    def test_waiting_question_blocks_later_queued_turn_until_original_reply(self):
        self.client.tool_turn.side_effect=[{'tool_calls':[{'id':'q','function':{'name':'ask_user','arguments':json.dumps({'question':'which?','options':['A']})}}]},
            {'content':'answer to first'},{'content':'answer to later'}]
        first=self.send('first');self.wait(first)
        later=self.service.submit(self.owner,first['conversation_id'],{'text':'later','client_message_id':str(uuid.uuid4())},str(uuid.uuid4()))
        time.sleep(.25);self.assertEqual(self.service.job(self.owner,later['job_id'])['state'],'queued');self.assertEqual(self.client.tool_turn.call_count,1)
        self.service.respond_job(self.owner,first['job_id'],{'text':'A','client_message_id':str(uuid.uuid4())},str(uuid.uuid4()))
        self.wait(later,('succeeded','failed'));self.assertEqual(self.client.tool_turn.call_count,3)

    def test_authorization_resume_keeps_original_pending_tool_and_native_task(self):
        self.risk_level='read'
        self.client.tool_turn.side_effect=[{'tool_calls':[{'id':'read','function':{'name':'feishu_cli',
            'arguments':json.dumps({'args':['docs','+get','--doc','OWN_RESOURCE']})}}]},{'content':'read after authorization'}]
        original=self.cli.execute;blocked=[]
        def execute(args,event):
            if not blocked:
                blocked.append(True);raise NativeAuthorizationRequired('ou_A',{'id':'fixture-flow'})
            return original(args,event)
        self.cli.execute=execute
        first=self.send();self.assertEqual(self.wait(first)['state'],'waiting_authorization')
        self.assertEqual(self.client.tool_turn.call_count,1);self.assertEqual(self.actual_business_calls(),[])
        with self.assertRaises(Problem):self.service.respond_job(self.owner,first['job_id'],{'text':'continue','client_message_id':str(uuid.uuid4())},str(uuid.uuid4()))
        body={'open_id':'ou_A','tenant_key':'fixture','chat_id':'oc_A','job_id':first['job_id']}
        reply=request(self.gateway_origin,'/internal/studio/resume-auth',body,self.key_file)
        self.assertEqual(reply['job_id'],first['job_id']);self.assertEqual(self.wait(first,('succeeded','failed'))['state'],'succeeded')
        with self.cli.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM native_tasks').fetchone()[0],1)
        self.assertEqual(len(self.actual_business_calls()),1);self.assertEqual(self.client.tool_turn.call_count,2)


if __name__=='__main__':unittest.main()
