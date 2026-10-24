import tempfile,uuid,unittest,json
from pathlib import Path
from unittest.mock import patch
from production_backend import Production
from production_skill import VERSION
from local_codex import CodexError
from domain import Problem

class NativeProduction(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.owner=str(uuid.uuid4());self.other=str(uuid.uuid4());self.p=Production({'production_accounts':['示例内容账号'],'state_dir':self.tmp.name,'workspace_state_dir':self.tmp.name,'members':[{'handle':'owner','principal_id':self.owner}],'local_codex':{'enabled':True}});self.p._project=lambda *_:None
  self.body={'projectId':'rec12345678','title':'口播测试','account':'示例内容账号','requirement':'保持真人主体，关键句有画面变化','size':1,'fileName':'source.mp4'}
 def tearDown(self):self.tmp.cleanup()
 def new(self):return self.p.start(self.owner,self.body,'native-job-test-key')
 def test_native_default_is_not_fixed_planner(self):
  job=self.new();self.assertEqual(job['workflowVersion'],VERSION);e=self.p.engine();self.assertEqual(e['engine'],'codex-cli');self.assertFalse(e['fixedSceneSchema']);self.assertEqual(e['defaultSkill'],'video-use')
  with patch('production_skill.execute',return_value='NATIVE') as run:self.assertEqual(self.p._produce(job['id']),'NATIVE');run.assert_called_once_with(self.p,job['id'])
 def test_legacy_version_stays_original_executor(self):
  from production_talkcraft import VERSION as old
  j=self.new();v=self.p._read(j['id']);self.p._update(v,workflowVersion=old)
  with patch('production_talkcraft_executor.execute',return_value='OLD') as run:self.assertEqual(self.p._produce(j['id']),'OLD');run.assert_called_once_with(self.p,j['id'])
 def test_waiting_reply_uses_same_job_and_pins_idempotency(self):
  j=self.new();v=self.p._read(j['id']);self.p._update(v,state='waiting_input')
  with patch('production_backend.threading.Thread') as thread:
   r=self.p.respond(self.owner,j['id'],{'text':'继续原素材'},'native-control-key');self.assertEqual(r['id'],j['id']);self.assertEqual(r['state'],'accepted');self.assertEqual(thread.call_count,1)
   repeat=self.p.respond(self.owner,j['id'],{'text':'继续原素材'},'native-control-key');self.assertEqual(repeat['id'],j['id']);self.assertEqual(thread.call_count,1)
   with self.assertRaises(Problem) as err:self.p.respond(self.owner,j['id'],{'text':'改动请求'},'native-control-key')
   self.assertEqual(err.exception.status,409)
   with self.assertRaises(Problem) as err:self.p.respond(self.other,j['id'],{'text':'同任务'},'other-control-key')
   self.assertEqual(err.exception.status,404)
  self.assertEqual(len(list(self.p.root.glob('*/status.json'))),1)
 def test_uncertain_cli_keeps_unknown_not_retryable_failure(self):
  j=self.new()
  with patch.object(self.p,'_produce',side_effect=CodexError('transport_unknown',True)):self.p._work(j['id'])
  self.assertEqual(self.p.get(self.owner,j['id'])['state'],'execution_unknown')
  with self.assertRaises(Problem) as err:self.p.start(self.owner,self.body,'another-native-key')
  self.assertEqual(err.exception.status,409)
