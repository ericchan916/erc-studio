import concurrent.futures
import copy
import datetime as dt
import http.client
import json
from pathlib import Path
import sys
import importlib.util
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Problem
from server import handler_for,ThreadingHTTPServer
from workflow_web import WorkflowWebMonitor,browser_projection,workflow_snapshot

def snapshot():
    return {'schemaVersion':1,'observedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'items':[{'id':'test','status':'healthy','runtimeCode':[{'path':'/Users/USER/runtime/run.py','sha256':'abc'}],'receipts':[{'source':'/Users/USER/content-jobs.json','counts':{}}]}],'summary':{'healthy':1},'coverage':{}}

class FakeService:
    def __init__(self,root):
        self.config={'state_dir':root,'allowed_hosts':['127.0.0.1'],'allowed_origins':['https://studio.example.invalid']}
        self.members={'owner':{'handle':'owner','principal_id':'owner-id','active':True},'member':{'handle':'member','principal_id':'member-id','active':True},'inactive':{'handle':'inactive','principal_id':'inactive-id','active':False}}
        self.workflow_monitor=WorkflowWebMonitor(self.config,run=lambda:browser_projection(snapshot()))
    def member(self,owner):
        for m in self.members.values():
            if m['principal_id']==owner and m['active']:return m
        raise Problem(401,'authentication_required')
    def authenticate(self,token):
        if token not in ('owner-token','member-token','inactive-token'):raise Problem(401,'authentication_required')
        owner=token.replace('-token','-id');self.member(owner);return owner

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.service=FakeService(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_active_members_use_existing_canonical_binding(self):
        for owner in ['owner-id','member-id']:
            value=workflow_snapshot(self.service,owner,{})
            self.assertTrue(value['ok']);self.assertTrue(value['readOnly'])
        for owner in ['inactive-id','attacker-id']:
            with self.assertRaises(Problem):workflow_snapshot(self.service,owner,{})
    def test_query_cannot_select_paths_owners_commands_or_write_operations(self):
        for q in [{'refresh':['2']},{'refresh':['1','1']},{'owner':['owner-id']},{'path':['/secret']},{'command':['restart']}]:
            with self.assertRaises(Problem):workflow_snapshot(self.service,'owner-id',q)
    def test_projection_strips_host_paths_without_mutating_input(self):
        value=snapshot();original=copy.deepcopy(value);result=browser_projection(value)
        self.assertEqual(value,original);self.assertNotIn('/Users/USER',json.dumps(result));self.assertEqual(result['items'][0]['runtimeCode'][0]['sha256'],'abc')
    def test_cache_coalesces_threads_and_limits_forced_refresh(self):
        count=[0];clock=[0]
        def run():count[0]+=1;return snapshot()
        m=WorkflowWebMonitor({'state_dir':self.tmp.name},run=run,clock=lambda:clock[0])
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(lambda _:m.snapshot(True),range(6)))
        self.assertEqual(count[0],1);clock[0]=6;m.snapshot(True);self.assertEqual(count[0],2)
        self.assertEqual((Path(self.tmp.name)/'workflow-observation.json').stat().st_mode&0o777,0o600)
    def test_failure_preserves_old_observation_and_never_uses_secret_error_string(self):
        m=self.service.workflow_monitor;m.snapshot();m.run=mock.Mock(side_effect=ValueError('SECRET_TOKEN'));m.last=-float('inf');value=m.snapshot(True)
        self.assertEqual(value['phase'],'stale');self.assertFalse(value['ok']);self.assertNotIn('SECRET_TOKEN',json.dumps(value))
    def test_empty_failure_is_explicit_and_bad_cache_is_preserved(self):
        p=Path(self.tmp.name)/'workflow-observation.json';p.write_text('{broken');m=WorkflowWebMonitor({'state_dir':self.tmp.name},run=mock.Mock(side_effect=ValueError()))
        self.assertEqual(m.snapshot()['phase'],'error');self.assertEqual(p.read_text(),'{broken')
    def test_http_auth_origin_method_and_member_boundaries(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.service));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            def get(path,token=None,method='GET',origin=None):
                c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);headers={'Host':'127.0.0.1'}
                if token:headers['Authorization']='Bearer '+token
                if origin:headers['Origin']=origin
                body=None
                if method=='POST':body='{}';headers['Content-Type']='application/json'
                c.request(method,path,body=body,headers=headers);response=c.getresponse();status=response.status;data=json.loads(response.read());c.close();return status,data
            self.assertEqual(get('/v1/workflows/snapshot')[0],401)
            status,data=get('/v1/workflows/snapshot','member-token');self.assertEqual(status,200);self.assertTrue(data['readOnly']);self.assertNotIn('/Users/USER',json.dumps(data))
            self.assertEqual(get('/v1/workflows/snapshot','inactive-token')[0],401)
            self.assertEqual(get('/v1/workflows/snapshot','unknown-token')[0],401)
            status,data=get('/v1/workflows/snapshot?refresh=1','owner-token');self.assertEqual(status,200);self.assertTrue(data['readOnly']);self.assertNotIn('/Users/USER',json.dumps(data))
            self.assertEqual(get('/v1/workflows/snapshot?path=/secret','owner-token')[0],400)
            self.assertEqual(get('/v1/workflows/snapshot?owner=owner-id','member-token')[0],400)
            self.assertEqual(get('/v1/workflows/snapshot','owner-token',origin='https://evil.test')[0],403)
            self.assertEqual(get('/v1/workflows/snapshot','owner-token',method='POST')[0],404)
        finally:server.shutdown();server.server_close();thread.join()
    def test_browser_adapter_exposes_only_semantic_same_origin_transport(self):
        script=(Path(__file__).parents[1]/'web/browser-adapter.js').read_text()
        self.assertIn("request('/v1/workflows/snapshot'",script)
        self.assertIn("['operation','id']",script)
        self.assertNotIn('collector.py',script)
        self.assertNotIn('workflowStateRoot',script)
    def test_web_installer_rotates_css_content_url_without_duplicate_links(self):
        script=Path(__file__).parents[2]/'scripts/install-workflow-web.py'
        spec=importlib.util.spec_from_file_location('workflow_web_install',script);installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        root=Path(self.tmp.name);(root/'web').mkdir();css=root/'web/workflow.css';css.write_text('first')
        with mock.patch.object(installer,'ROOT',root):
            first=installer.index_hooks('<head><link href="workflow.css"></head><body></body>')
            self.assertIn('href="workflow-'+installer.sha(b'first')[:12]+'.css"',first)
            self.assertEqual(installer.index_hooks(first),first)
            css.write_text('second');second=installer.index_hooks(first)
            self.assertIn('href="workflow-'+installer.sha(b'second')[:12]+'.css"',second)
            self.assertEqual(second.count('href="workflow-'),1);self.assertEqual(second.count('src="workflow-ui.js"'),1)
            with self.assertRaises(RuntimeError):installer.index_hooks(first.replace('</head>','<link href="workflow.css"></head>'))

if __name__=='__main__':unittest.main()
