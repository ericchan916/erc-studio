import copy,json,pathlib,sys,tempfile,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from production_talkcraft import build,prompt,transcript,verify_bundle,VERSION
TEMPLATE=pathlib.Path(__file__).resolve().parents[2]/'production-remotion'
class Tests(unittest.TestCase):
 def fixture(self):
  seg=[{'startMs':0,'endMs':3000,'text':'缺少独特的认知','words':[{'startMs':0,'endMs':450,'text':'缺少'},{'startMs':450,'endMs':900,'text':'独特'},{'startMs':900,'endMs':1300,'text':'的'},{'startMs':1300,'endMs':1800,'text':'认知'}]}, {'startMs':3000,'endMs':6000,'text':'还有实践经历','words':[{'startMs':3000,'endMs':3500,'text':'还有'},{'startMs':3500,'endMs':4000,'text':'实践'},{'startMs':4000,'endMs':4500,'text':'经历'}]}]
  plan={'version':3,'keeps':[{'startMs':s['startMs'],'endMs':s['endMs'],'caption':s['text']} for s in seg],'intent':'解释两类缺口','summary':'两个缺口','unmet':[],'scenes':[{'card':'gaps','fromClip':0,'toClip':1,'headline':'两个缺口','labels':['独特认知','实践经历'],'note':'','rationale':'把口播的两个概念分开','anchors':[{'clip':0,'text':'独特'},{'clip':1,'text':'实践'}]}]}
  return plan,seg
 def test_actual_upstream_snapshot_and_authorization_notice(self):
  meta=verify_bundle(TEMPLATE);self.assertEqual(len(meta['trustedCards']),5)
 def test_full_skill_and_card_experience_enter_real_prompt(self):
  _,seg=self.fixture();s=prompt(TEMPLATE,'原片','保留原声',seg,6)
  self.assertIn('video-talkcraft',s);self.assertIn('soft-blur-in',s);self.assertIn('上游流程里任意写代码',s);self.assertIn('可信',s);self.assertIn((TEMPLATE/'talkcraft/SKILL.md').read_text(),s)
 def test_kept_word_anchors_are_derived_not_model_time(self):
  p,s=self.fixture();d,u=build(p,s,6);self.assertEqual(d['workflowVersion'],VERSION);self.assertEqual(d['totalFrames'],180);self.assertEqual(d['scenes'][0]['cues'][0]['frame'],14);self.assertEqual(d['scenes'][0]['cues'][1]['frame'],105);self.assertFalse(u)
 def test_deleted_prefix_rebases_source_tokens_on_edit_axis(self):
  p,s=self.fixture();p['keeps'][0]['startMs']=400;p['keeps'][0]['caption']='独特的认知';d,_=build(p,s,6);self.assertEqual(d['scenes'][0]['cues'][0]['frame'],2);self.assertEqual(d['scenes'][0]['cues'][1]['frame'],93)
 def test_no_token_anchor_surfaces_partial_not_fake_word_accuracy(self):
  p,s=self.fixture();s[0]['words']=[];d,u=build(p,s,6);self.assertEqual(d['scenes'][0]['cues'][0]['quality'],'segment');self.assertTrue(u)
 def test_missing_scene_coverage_and_wrong_source_are_rejected(self):
  for change,error in [(lambda p:p['scenes'][0].update(toClip=0),'anchor_not_in_source'),(lambda p:p['scenes'][0]['anchors'][0].update(text='从没说过'),'anchor_not_in_source'),(lambda p:p['keeps'][0].update(caption='模型编的新原话'),'caption_not_in_speech')]:
   p,s=self.fixture();change(p)
   with self.assertRaisesRegex(RuntimeError,error):build(p,s,6)
 def test_unsupported_card_and_invented_numbers_are_rejected(self):
  for change,error in [(lambda p:p['scenes'][0].update(card='PAYLOAD.tsx'),'invalid_binding'),(lambda p:p['scenes'][0].update(headline='99%的焦虑'),'number_not_in_source')]:
   p,s=self.fixture();change(p)
   with self.assertRaisesRegex(RuntimeError,error):build(p,s,6)
 def test_zero_duration_and_out_of_order_anchors_never_render(self):
  p,s=self.fixture();p['scenes'][0]['anchors'].reverse()
  with self.assertRaisesRegex(RuntimeError,'order_invalid'):build(p,s,6)
  p,s=self.fixture();p['keeps'][0]['endMs']=0
  with self.assertRaisesRegex(RuntimeError,'invalid_range'):build(p,s,6)
 def test_full_json_preserves_real_whisper_tokens(self):
  raw={'transcription':[{'offsets':{'from':0,'to':1000},'text':'你好','tokens':[{'text':'[BEG]','offsets':{'from':0,'to':1}},{'text':'你','offsets':{'from':100,'to':400}},{'text':'好','offsets':{'from':400,'to':750}},{'text':'!','offsets':{'from':750,'to':800}}]}]}
  rows=transcript(raw,1);self.assertEqual(rows[0]['words'][0]['startMs'],100);self.assertEqual(len(rows[0]['words']),2)
if __name__=='__main__':unittest.main()

class ExecutorContractTests(unittest.TestCase):
 def test_caption_color_changes_only_after_real_geometry_lands(self):
  from production_talkcraft_executor import subtitle_spec
  data={'totalFrames':120,'captions':[{'startFrame':0,'endFrame':120,'text':'真实字幕'}],'scenes':[{'card':'soft-blur-in','from':0,'to':90,'cues':[{'localFrame':30}]},{'card':'full','from':90,'to':120,'cues':[]}]}
  ranges=subtitle_spec(data)['ranges'];self.assertEqual(ranges[0]['file'],'0000-white.png');self.assertEqual(ranges[0]['endFrame'],30);self.assertEqual(ranges[1]['file'],'0000-ink.png');self.assertEqual(ranges[-1]['file'],'0000-white.png')
 def test_new_job_defaults_to_talkcraft_old_job_dispatches_original_path(self):
  import uuid,tempfile
  from unittest.mock import patch
  from production_backend import Production
  with tempfile.TemporaryDirectory() as folder:
   owner=str(uuid.uuid4());p=Production({'state_dir':folder,'workspace_state_dir':folder,'members':[{'handle':'owner','principal_id':owner}]},template=TEMPLATE);p._project=lambda *_:None
   body={'projectId':'rec12345678','title':'测试口播','account':'示例内容账号','requirement':'保留原声','size':1,'fileName':'test.mp4'}
   job=p.start(owner,body,'talkcraft-pinned-key');self.assertEqual(job['workflowVersion'],VERSION)
   with patch('production_talkcraft_executor.execute',return_value='TALKCRAFT_RUN') as run:self.assertEqual(p._produce(job['id']),'TALKCRAFT_RUN');run.assert_called_once_with(p,job['id'])
   v=p._read(job['id']);p._update(v,workflowVersion=None)
   with patch('production_talkcraft_executor.execute') as run,patch.object(p,'_run',side_effect=RuntimeError('original-path-entered')):
    with self.assertRaisesRegex(RuntimeError,'original-path-entered'):p._produce(job['id'])
    run.assert_not_called()
 def test_engine_exposes_real_default_workflow_and_fails_closed_on_provenance(self):
  import tempfile
  from unittest.mock import patch
  from production_backend import Production
  with tempfile.TemporaryDirectory() as folder:
   p=Production({'state_dir':folder,'workspace_state_dir':folder},template=TEMPLATE);e=p.engine();self.assertEqual(e['name'],'video-talkcraft');self.assertEqual(e['workflowVersion'],VERSION);self.assertTrue(e['ready'])
   with patch('production_backend.verify_bundle',side_effect=RuntimeError('tampered')):self.assertFalse(p.engine()['ready'])

class CameraTests(unittest.TestCase):
 fixture=Tests.fixture
 def test_short_conclusion_can_retain_full_person(self):
  p,s=self.fixture();p['scenes'][0].update(card='soft-blur-in',camera='full');d,_=build(p,s,6);self.assertEqual(d['scenes'][0]['camera'],'full')
 def test_large_diagram_cannot_cover_full_person(self):
  p,s=self.fixture();p['scenes'][0]['camera']='full'
  with self.assertRaisesRegex(RuntimeError,'camera_invalid'):build(p,s,6)

class CaptionRegressionTests(unittest.TestCase):
 def test_missing_early_token_does_not_retime_later_matched_phrase(self):
  text='当我们面对一个巨大的系统和任务的时候';tokens=[{'text':t,'startMs':ms,'endMs':ms+100} for t,ms in [('当',0),('我们',100),('面对',300),('一个',500),('巨大',700),('的',900),('系统',1100),('和',1300),('任务',1500),('的时候',2200)]]
  tokens=tokens[1:]
  seg=[{'startMs':0,'endMs':3000,'text':text,'words':tokens}];p={'version':3,'keeps':[{'startMs':0,'endMs':3000,'caption':text}],'intent':'真实字幕','summary':'测试','unmet':[],'scenes':[{'card':'full','camera':'full','fromClip':0,'toClip':0,'headline':'','labels':[],'note':'','rationale':'保留原片','anchors':[]}]}
  d,u=build(p,seg,3);self.assertEqual(d['captions'][1]['text'],'时候');self.assertEqual(d['captions'][1]['startFrame'],66);self.assertTrue(u)
