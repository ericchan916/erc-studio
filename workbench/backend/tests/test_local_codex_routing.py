import json,tempfile,time,uuid,unittest
from pathlib import Path
from unittest import mock
from test_execution_adapter import GatewayExecutionTests
from local_codex import LocalCodex,CodexError
class LocalRoutes(unittest.TestCase):
 def setUp(self):
  fixture=GatewayExecutionTests('test_gateway_worker_executes_native_once_without_plain_model_fallback');fixture.setUp();self.fixture=fixture
  for k,v in vars(fixture).items():
   if not k.startswith('_'):setattr(self,k,v)
  
  skill_files={}
  for name in ('video-use','ffmpeg'):
   source=Path(self.tmp.name)/'fixture-skills'/name;source.mkdir(parents=True);(source/'SKILL.md').write_text('# Synthetic local Skill input');skill_files[name]=str(source)
  self.local=LocalCodex(Path(self.tmp.name)/'local',{'skill_files':skill_files});self.service.execution.local=self.local
 def tearDown(self):self.fixture.tearDown()
 def send(self,*a,**kw):return self.fixture.send(*a,**kw)
 def wait(self,*a,**kw):return self.fixture.wait(*a,**kw)
 def legacy(self):
  # Seed a pre-correction execution; only persisted old jobs retain Codex.
  adapter=self.service.execution;original=adapter.is_local
  def prior_route(job):
   row=self.service.one('SELECT request_json FROM conversation_execution WHERE job=?',(job['id'],))
   return original(job) if row else True
  with mock.patch.object(adapter,'is_local',side_effect=prior_route):
   r=self.send();self.wait(r);return r
 def test_web_calls_bridge_even_with_video_codex_enabled(self):
  with mock.patch.object(self.local,'run') as run:
   r=self.send('你好');j=self.wait(r);self.assertEqual(j['state'],'succeeded');run.assert_not_called();self.assertEqual(self.client.tool_turn.call_count,1);self.assertEqual(self.model.calls,[])
   row=self.service.one('SELECT request_json FROM conversation_execution WHERE job=?',(r['job_id'],));self.assertNotEqual(json.loads(row['request_json']).get('engine'),'codex-cli')
 def test_app_calls_bridge_even_with_video_codex_enabled(self):
  with mock.patch.object(self.local,'run') as run:
   r=self.service.start_message(self.owner,{'title':'App','text':'普通对话','client_message_id':str(uuid.uuid4())},str(uuid.uuid4()),'app');self.assertEqual(self.wait(r)['state'],'succeeded');run.assert_not_called();self.assertEqual(self.client.tool_turn.call_count,1)
 def test_health_reports_chat_model_not_video_model(self):
  import urllib.request
  self.config['model']='zhipu:glm-5.3-flash'
  with urllib.request.urlopen(self.gateway_origin+'/health') as response:value=json.load(response)
  self.assertEqual(value['model'],'zhipu:glm-5.3-flash');self.assertEqual(value['execution_engine'],'bridge');self.assertNotIn('video-use',value['skills'])
  self.assertEqual(self.service.info(self.owner)['capabilities']['engine'],'bridge')
 def test_waiting_reply_pins_engine_and_original_job(self):
  with mock.patch.object(self.local,'run',side_effect=[{'text':'need footage','state':'waiting_input','artifacts':[]},{'text':'finished same job','state':'succeeded','artifacts':[]}]) as run:
   r=self.legacy();self.assertEqual(self.wait(r)['state'],'waiting_input');old=self.service.one('SELECT request_json FROM conversation_execution WHERE job=?',(r['job_id'],));self.assertEqual(json.loads(old['request_json'])['engine'],'codex-cli')
   control={'text':'same footage','client_message_id':str(uuid.uuid4())};reply=self.service.respond_job(self.owner,r['job_id'],control,str(uuid.uuid4()),'web');self.assertEqual(reply['job_id'],r['job_id']);self.assertEqual(self.wait(r,('succeeded',))['state'],'succeeded');self.assertEqual(run.call_count,2);self.assertEqual(self.client.tool_turn.call_count,0)
 def test_feishu_original_route_stays_bridge(self):
  with mock.patch.object(self.local,'run',return_value={'text':'web marker','state':'succeeded','artifacts':[]}):
   first=self.send('web marker');self.wait(first)
  event={**self.event,'chat_type':'p2p','message_type':'text','message_id':'om_fixture','content':'feishu follows'};self.bridge.store.add_event(event)
  from conversation_protocol import request
  body={'open_id':'ou_A','tenant_key':'fixture','chat_id':'oc_A','source_id':'om_fixture','source_event_id':event['event_id'],'text':'feishu follows'}
  receipt=request(self.gateway_origin,'/internal/studio/ingest',body,self.key_file);self.assertEqual(self.wait(receipt)['state'],'succeeded');self.assertEqual(self.client.tool_turn.call_count,2)
 def test_local_transport_error_is_unknown_without_model_fallback(self):
  with mock.patch.object(self.local,'run',side_effect=CodexError('fixture_failure',True)):
   r=self.legacy();self.assertEqual(self.wait(r)['state'],'execution_unknown');self.assertEqual(self.client.tool_turn.call_count,0);self.assertEqual(self.model.calls,[])
 def test_workspaces_and_skill_copies_are_member_conversation_scoped(self):
  a=self.local.workspace(self.owner,str(uuid.uuid4()));b=self.local.workspace(self.other,str(uuid.uuid4()));self.assertNotEqual(a,b);self.assertTrue((a/'.agents/skills/video-use/SKILL.md').is_file());self.assertFalse((a/'.agents/skills/video-use').is_symlink());(a/'own.txt').write_text('owned');self.assertFalse((b/'own.txt').exists())
 def test_recover_local_without_receipt_never_executes(self):
  with mock.patch.object(self.local,'run',side_effect=CodexError('lost',True)) as run:
   r=self.legacy();self.assertEqual(self.wait(r)['state'],'execution_unknown');result=self.service.recover_job(self.owner,r['job_id'],str(uuid.uuid4()));self.assertFalse(result['recovered']);self.assertEqual(run.call_count,1)
 def test_http_artifact_is_owned_and_cannot_escape_or_follow_symlink(self):
  import hashlib,urllib.request,urllib.error
  class LoginSender:
   def __init__(inner):inner.codes={}
   def send(inner,member,code,login,device):inner.codes[login]=code;return {"message_id":"om_fixture"}
   def ensure_active(inner,member):pass
  self.service.notifier=LoginSender()
  def token(handle):
   verifier='x'*64;f=self.service.start_login({'member_handle':handle,'client_challenge':hashlib.sha256(verifier.encode()).hexdigest(),'device_id':str(uuid.uuid4())});return self.service.poll_login({'login_id':f['login_id'],'client_verifier':verifier,'code':self.service.notifier.codes[f['login_id']]})['session_credential']
  a=token('a');b=token('b');cid=self.service.create_conversation(self.owner,{'title':'owned'},str(uuid.uuid4()))['conversation_id'];work=self.local.workspace(self.owner,cid);(work/'edit/artifacts/proof.json').write_text('{"owned":true}');(work/'edit/artifacts/symlink.json').symlink_to(work/'edit/artifacts/proof.json')
  url=self.gateway_origin+'/v1/codex/'+cid+'/files/'
  with urllib.request.urlopen(urllib.request.Request(url+'proof.json',headers={'Authorization':'Bearer '+a})) as response:self.assertEqual(json.load(response),{'owned':True})
  for user,path in [(b,'proof.json'),(a,'symlink.json'),(a,'%2e%2e%2f.control%2frequest.json')]:
   with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(url+path,headers={'Authorization':'Bearer '+user}))
   self.assertIn(error.exception.code,(400,404))
