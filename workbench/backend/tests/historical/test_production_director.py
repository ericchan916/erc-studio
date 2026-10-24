import copy,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from production_director import build,prompt
class Tests(unittest.TestCase):
 def fixture(self):
  text=['原本手动调整需要一到两个小时','交给AI描述我的需求','它一下子就操作好了，只花15分钟']
  segments=[{'startMs':i*3000,'endMs':(i+1)*3000,'text':x} for i,x in enumerate(text)]
  plan={'version':2,'keeps':[{'startMs':i*3000,'endMs':(i+1)*3000,'caption':x} for i,x in enumerate(text)],'intent':'清晰','summary':'总结','unmet':[],'scenes':[{'kind':'time','fromClip':0,'toClip':2,'resultClip':2,'title':'用时','label':'比较','headline':'同一项工作','labels':['手动','AI'],'note':'本人案例','rationale':'比较时间范围','values':[60,120,15]}]};return plan,segments
 def test_bound_scene_has_real_metrics_and_result_cue(self):
  plan,seg=self.fixture();data,unmet=build(plan,seg,9);self.assertEqual(data['totalFrames'],270);self.assertEqual(data['scenes'][0]['doneAt'],180);self.assertEqual(data['scenes'][0]['from'],0)
 def test_invented_metric_is_rejected(self):
  plan,seg=self.fixture();plan['scenes'][0]['values']=[60,120,5]
  with self.assertRaisesRegex(RuntimeError,'unsupported_by_source'):build(plan,seg,9)
 def test_overlap_and_unknown_kind_are_rejected(self):
  plan,seg=self.fixture();plan['scenes'].append(copy.deepcopy(plan['scenes'][0]))
  with self.assertRaisesRegex(RuntimeError,'invalid_binding'):build(plan,seg,9)
 def test_request_to_complete_is_not_completed(self):
  plan,seg=self.fixture();seg[1]['text']='请你帮我完成改动';plan['scenes'][0].update(kind='ai',labels=['来源','WPS','原稿','Keynote','帮我同步','已完成'],values=[0,0,0],resultClip=1)
  with self.assertRaisesRegex(RuntimeError,'not_supported'):build(plan,seg,9)
 def test_caption_rewrite_surfaces_human_review(self):
  plan,seg=self.fixture();plan['keeps'][0]['caption']='手动修改需要一到两小时';_,unmet=build(plan,seg,9);self.assertTrue(unmet)
 def test_prompt_names_all_skills_and_autonomous_mode(self):
  _,seg=self.fixture();value=prompt(None,'标题','需求',seg,9);self.assertIn('motion-art-direction',value);self.assertIn('video-shotcraft',value);self.assertIn('remotion-markup',value);self.assertIn('不向用户提选择题',value)
if __name__=='__main__':unittest.main()

class RegressionTests(Tests):
 def test_time_units_cannot_turn_hours_into_minutes(self):
  plan,seg=self.fixture();seg[2]['text']='AI已经操作好了，只花15小时'
  with self.assertRaisesRegex(RuntimeError,'source_units'):build(plan,seg,9)
 def test_short_ai_labels_fail_with_repairable_error(self):
  plan,seg=self.fixture();plan['scenes'][0].update(kind='ai',labels=['a','b'],values=[0,0,0])
  with self.assertRaisesRegex(RuntimeError,'insufficient_labels'):build(plan,seg,9)
 def test_internal_note_is_not_audience_copy(self):
  plan,seg=self.fixture();plan['scenes'][0]['note']='先保留真人呼吸，随后头像收圆'
  with self.assertRaisesRegex(RuntimeError,'production_instruction'):build(plan,seg,9)
