import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class WorkbenchMigrationTests(unittest.TestCase):
    def test_registry_has_implemented_modules_and_explicit_contracts(self):
        entries=json.loads((ROOT/'workbench/module-registry.json').read_text())['modules']
        self.assertEqual(len({x['id'] for x in entries}),len(entries))
        self.assertEqual(sum(x['implementation']=='ported_original_source' for x in entries),12)
        self.assertEqual(sum(x['implementation']=='contract_only' for x in entries),22)
        for x in entries:
            self.assertTrue((ROOT/x['spec']).is_file())
            for source in x['source_files']:self.assertTrue((ROOT/source).is_file())

    def test_selected_module_reader_is_offline_and_explicit(self):
        result=subprocess.run(['python3','-B','scripts/module_docs.py','publishing'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertIn('发布',result.stdout)
        self.assertIn('taskSetId',result.stdout)
        result=subprocess.run(['python3','-B','scripts/module_docs.py','not-a-module'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,2)

    def test_missing_optional_skills_never_resolve_to_source_cwd(self):
        text=(ROOT/'workbench/backend/local_codex.py').read_text()
        self.assertNotIn("pathlib.Path(os.environ.get('CONTENTBASE_RESOURCE_",text)
        self.assertIn('self.skills',text)
        self.assertIn("pathlib.Path(value).is_absolute()",text)

    def test_original_navigation_and_module_sources_are_retained(self):
        app=(ROOT/'workbench/web/app.js').read_text()
        for text in ['主页','任务','内容','账号','发布','数据','官网']:self.assertIn(text,app)
        for folder in ['src','web','backend','shared/notification-center']:self.assertTrue((ROOT/'workbench'/folder).is_dir())

    def test_source_inspection_does_not_claim_identity_or_call_providers(self):
        text=(ROOT/'workbench/web/source-inspection.js').read_text()
        self.assertIn('authenticated:false',text)
        self.assertIn('isSignedIn:()=>false',text)
        self.assertNotIn('fetch(',text)
        self.assertNotIn('execFile',text)
        self.assertIn('source_inspection_readonly',text)
        html=(ROOT/'workbench/web/inspection.html').read_text()
        self.assertNotIn('src="home-chat.js"',html)
        self.assertIn('src="source-inspection.js"',html)

    def test_personal_assets_and_original_credential_autodiscovery_removed(self):
        self.assertFalse((ROOT/'workbench/web/assets/model.glb').exists())
        self.assertNotIn('CherryFeishuBridge/config.json',(ROOT/'workbench/src/content/agent.cjs').read_text())
        self.assertIn('CONTENTBASE_MODEL_CONFIG',(ROOT/'workbench/src/content/agent.cjs').read_text())
        self.assertIn('CONTENTBASE_RUNTIME_ROOT',(ROOT/'workbench/src/workflow/collector.py').read_text())

    def test_node_config_generator_stays_private_and_preserves_existing(self):
        import shutil
        if not shutil.which('node'):self.fail('complete migration tests require Node')
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'state'
            cmd=['node','workbench/scripts/init-config.cjs','--state',str(out)]
            first=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(first.returncode,0,first.stderr)
            before=(out/'setup.json').read_bytes()
            self.assertEqual((out/'setup.json').stat().st_mode&0o777,0o600)
            self.assertFalse(json.loads(first.stdout)['configured'])
            again=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True)
            self.assertNotEqual(again.returncode,0)
            self.assertEqual((out/'setup.json').read_bytes(),before)

class OriginalWorkbenchHttpTests(unittest.TestCase):
    def setUp(self):
        import threading
        from contentbase.server import make_server
        self.server,_=make_server(None,0,'YOUR_TEST_HTTP_TOKEN')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:%d'%self.server.server_port
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
    def request(self,path):
        from urllib.request import urlopen
        from urllib.error import HTTPError
        try:response=urlopen(self.url+path)
        except HTTPError as error:response=error
        with response:return response.status,response.read()
    def test_root_is_original_source_view_not_old_controller(self):
        code,body=self.request('/')
        self.assertEqual(code,200)
        self.assertIn(b'data-source-inspection="true"',body)
        self.assertIn(b'/workbench/web/source-inspection.js',body)
        self.assertNotIn(b'CONTENT<br>COMPANY BASE',body)
    def test_compatibility_controller_is_explicit(self):
        code,body=self.request('/control')
        self.assertEqual(code,200)
        self.assertIn(b'CLI',body)
    def test_static_assets_are_exactly_allowlisted_no_backend_sources(self):
        self.assertEqual(self.request('/workbench/web/source-inspection.js')[0],200)
        self.assertNotEqual(self.request('/workbench/backend/server.py')[0],200)
        self.assertNotEqual(self.request('/workbench/web/../../config.example.json')[0],200)
        self.assertNotEqual(self.request('/workbench/config.example.json')[0],200)
    def test_inspection_preserves_business_authentication_gate(self):
        code,body=self.request('/api/operations')
        self.assertEqual(code,401)
        self.assertIn(b'authentication_required',body)
