import hashlib,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from scientist_metrics import exact_map,status,ScientistSupplement,ScientistError,merge_overview
from account_metrics import project,apply_display_policy

def core(sid='one',platform='douyin',id='ac_first'):
 return {'id':id,'name':'same name','platform':platform,'enabled':True,'bindings':[{'source':'yxer','source_id':sid}],'auth':{'status':'healthy','checked_at':'2026-10-03T14:22:44+0800'},'identity':{'status':'unknown'}}
class Tests(unittest.TestCase):
 def test_join_by_exact_id_platform_not_nickname(self):
  rows=[core(),core('two',id='ac_second'),core('one','xiaohongshu','ac_third')];m=exact_map(rows);self.assertEqual(len(m),3);self.assertEqual(m[('douyin',hashlib.sha256(b'one').hexdigest())]['id'],'ac_first')
 def test_ambiguous_bindings_fail_closed(self):
  with self.assertRaises(ScientistError):exact_map([core(),core(id='ac_other')])
 def test_healthy_is_not_verified_identity(self):
  a=core();self.assertEqual(status(a)['state'],'identity_pending');a['identity']['status']='verified';self.assertEqual(status(a)['state'],'identity_pending');a['platform_account_id']='uid';self.assertEqual(status(a)['state'],'metrics_contract_pending')
 def test_valid_yxer_values_never_trigger_fallback(self):
  a=[{'id':'local','platform':'xiaohongshu','accountMetrics':{'totalFans':{'value':42},'overview':[]}}];s=ScientistSupplement(read=lambda:(_ for _ in ()).throw(AssertionError('must not call core')));self.assertEqual(s.apply(a,{'accounts':[]}),a)
 def test_other_missing_account_fields_route_without_overwriting_valid_yxer_total(self):
  c=core();c['identity']={'status':'matched'};c['platform_account_id']='uid'
  local={'accounts':[{'id':'local','platform':'douyin','sourceKey':hashlib.sha256(b'one').hexdigest()}]}
  a=[{'id':'local','platform':'douyin','fans':99,'accountMetrics':{'totalFans':{'value':99,'source':'yxer.accounts.list'},'overview':[]}}]
  packet={'ok':True,'schema_version':1,'scope':'latest_successful_account_overviews','accounts':[{'account_id':c['id'],'platform':'douyin','platform_account_id':'uid','identity':{'status':'matched'},'state':'valid','collected_at':'2026-10-03T15:00:00+0800','metrics':{'following_count':{'value':352,'status':'valid','raw_field':'user_profile.following_count','source_route':'/aweme/v1/creator/user/info/','unit':'人'},'total_fans':{'value':88,'status':'valid','raw_field':'user_profile.follower_count','source_route':'/aweme/v1/creator/user/info/','unit':'人'}}}]}
  r=ScientistSupplement(read=lambda:[c],read_overviews=lambda selection:packet).apply(a,local)
  self.assertEqual(r[0]['fans'],99);self.assertEqual(r[0]['accountMetrics']['overview'][0]['value'],352)
 def test_pending_core_metadata_never_manufactures_total(self):
  local={'accounts':[{'id':'local','platform':'douyin','sourceKey':hashlib.sha256(b'one').hexdigest()}]};a=project(local,[{'id':'one','platformName':'抖音','fansTotal':0}],{},'at');s=ScientistSupplement(read=lambda:[core()]);r=s.apply(a,local);self.assertIsNone(r[0]['fans']);self.assertEqual(r[0]['accountMetrics']['scientistBackend']['state'],'identity_pending');self.assertNotIn('bindings',r[0]['accountMetrics']['scientistBackend'])
 def test_missing_registry_remains_explicit_and_no_sensitive_errors(self):
  def read():raise ScientistError('scientist_registry_unavailable')
  a=[{'id':'local','platform':'douyin','accountMetrics':{'totalFans':{'value':None}}}];r=ScientistSupplement(read=read).apply(a,{'accounts':[]});self.assertEqual(r[0]['accountMetrics']['scientistBackend']['state'],'scientist_registry_unavailable')
class OverviewTests(unittest.TestCase):
 def fixtures(self):
  c=core();c['identity']={'status':'matched'};c['platform_account_id']='UID_ONE'
  a={'id':'local','fans':None,'accountMetrics':{'totalFans':{'value':None,'status':'unverified'},'overview':[],'scientistBackend':status(c)}}
  r={'account_id':c['id'],'platform':'douyin','platform_account_id':'UID_ONE','identity':{'status':'matched'},'state':'valid','collected_at':'2026-10-03T14:50:00+0800','metrics':{'total_fans':{'value':None,'status':'unsupported'},'fans_index':{'value':71200,'status':'valid','raw_field':'user_profile.follower_count','source_route':'/aweme/v1/creator/user/info/','unit':'指数'}}}
  return a,c,r
 def test_fans_index_is_not_total(self):
  a,c,r=self.fixtures();merge_overview(a,c,r);self.assertIsNone(a['fans']);self.assertEqual(a['accountMetrics']['totalFans']['status'],'unsupported');self.assertEqual(a['accountMetrics']['overview'][0]['key'],'fansIndex');self.assertEqual(a['accountMetrics']['overview'][0]['value'],71200)
 def test_snapshot_owner_mismatch_never_fills(self):
  a,c,r=self.fixtures();r['platform_account_id']='OTHER';merge_overview(a,c,r);self.assertEqual(a['accountMetrics']['scientistBackend']['state'],'identity_mismatch');self.assertEqual(a['accountMetrics']['overview'],[])
 def test_display_alias_keeps_canonical_values_and_non_douyin_unchanged(self):
  a,c,r=self.fixtures();a['platform']='douyin';merge_overview(a,c,r);apply_display_policy([a]);self.assertEqual(a['accountMetrics']['displayTotalFans']['value'],71200);self.assertIsNone(a['fans']);self.assertIsNone(a['accountMetrics']['totalFans']['value']);self.assertEqual(a['accountMetrics']['displayTotalFans']['originalMetricKey'],'fans_index')
  a['platform']='xiaohongshu';apply_display_policy([a]);self.assertNotIn('displayTotalFans',a['accountMetrics'])
 def test_true_total_zero_is_retained(self):
  a,c,r=self.fixtures();r['metrics']['total_fans']={'value':0,'status':'valid','raw_field':'user_profile.follower_count','source_route':'/aweme/v1/creator/user/info/','unit':'人'};r['metrics']['fans_index']['status']='unsupported';merge_overview(a,c,r);self.assertEqual(a['fans'],0)
 def test_unsafe_numeric_and_boolean_rejected(self):
  for bad in [True,9007199254740992,-1,'71200']:
   a,c,r=self.fixtures();r['metrics']['fans_index']['value']=bad;merge_overview(a,c,r);self.assertEqual(a['accountMetrics']['overview'],[])
 def test_stale_snapshot_marked_not_current(self):
  a,c,r=self.fixtures();r['state']='stale';merge_overview(a,c,r);self.assertTrue(a['accountMetrics']['overview'][0]['stale'])
if __name__=='__main__':unittest.main()
