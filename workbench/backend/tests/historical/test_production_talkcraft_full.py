import json,pathlib,sys,tempfile,unittest,uuid
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from production_talkcraft_full import VERSION,request,bundle_ready
from production_backend import Production
from production_skill import VERSION as NATIVE_VERSION
class Tests(unittest.TestCase):
 def test_complete_upstream_has_all_cards_scripts_and_workbench(self):
  meta=bundle_ready();self.assertGreaterEqual(len(meta['files']),900)
 def test_prompt_uses_skill_as_author_and_no_scene_schema(self):
  text=request(None,{'fileName':'video.mp4'});self.assertIn('编写制作工程',text);self.assertIn('108张',text);self.assertNotIn('talkcraft.schema.json',text);self.assertIn('不发布',text)
 def test_new_job_selects_full_executor_but_old_adapted_job_keeps_original_dispatch(self):
  with tempfile.TemporaryDirectory() as folder:
   owner=str(uuid.uuid4());p=Production({'state_dir':folder,'workspace_state_dir':folder,'members':[{'handle':'owner','principal_id':owner}],'local_codex':{'enabled':True}});p._project=lambda *_:None
   body={'projectId':'rec12345678','title':'口播','account':'示例内容账号','requirement':'用完整Skill','size':1,'fileName':'source.mp4'};j=p.start(owner,body,'full-skill-job-123');self.assertEqual(j['workflowVersion'],NATIVE_VERSION)
   with patch('production_skill.execute',return_value='full') as run:self.assertEqual(p._produce(j['id']),'full');run.assert_called_once_with(p,j['id'])
   row=p._read(j['id']);p._update(row,workflowVersion='video-talkcraft-studio-v1')
   with patch('production_talkcraft_executor.execute',return_value='legacy') as old:self.assertEqual(p._produce(j['id']),'legacy');old.assert_called_once_with(p,j['id'])
 def test_engine_truthfully_reports_code_generation_not_adapter(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Production({'state_dir':folder,'workspace_state_dir':folder,'local_codex':{'enabled':True}});j=p.engine();self.assertTrue(j['ready']);self.assertTrue(j['codeGeneration']);self.assertFalse(j['fixedSceneSchema']);self.assertEqual(j['defaultSkill'],'video-use');self.assertEqual(j['engine'],'codex-cli')
if __name__=='__main__':unittest.main()
