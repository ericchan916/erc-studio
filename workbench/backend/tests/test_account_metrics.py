import hashlib,json,pathlib,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from account_metrics import project,AccountsMetrics,overview_pages,MetricsError,numeric,parsed_number
AT='2026-10-03T00:00:00+00:00'
def account(sid='one',platform='douyin'):
 return {'id':sid,'name':'same name','platform':platform,'sourceKey':hashlib.sha256(sid.encode()).hexdigest(),'fans':999,'partition':'secret partition','status':'unverified'}
def row(sid='one',fans=0,platform='抖音'):
 return {'id':sid,'fansTotal':fans,'platformName':platform,'status':1}
def overview(sid='one',fields=None):
 return {'platformAccountId':sid,'overviewData':json.dumps({'updateTime':1790760421966,'value':{'video':fields or [{'key':'netFansCount','name':'净增粉丝','value':'715'}]}})}
class Tests(unittest.TestCase):
 def setUp(self):
  from unittest.mock import patch
  from scientist_metrics import ScientistSupplement
  self.core_patch=patch('account_metrics.ScientistSupplement',return_value=ScientistSupplement(read=lambda:[]));self.core_patch.start();self.addCleanup(self.core_patch.stop)

 def test_zero_not_inferred_real_no_net_substitution(self):
  a=project({'accounts':[account()]},[row()],{'douyin':[overview()]},AT)[0]
  self.assertIsNone(a['fans']);self.assertEqual(a['accountMetrics']['totalFans']['status'],'unverified');self.assertEqual(a['accountMetrics']['overview'][0]['value'],715);self.assertNotIn('partition',a);self.assertNotIn('sourceKey',a)
 def test_thousands_are_exact_and_grouping_is_validated(self):
  self.assertEqual(numeric('4,860'),4860);self.assertEqual(numeric('7,145'),7145);self.assertEqual(numeric('1,234.50'),1234.5)
  for raw in ['4,86','1,2,345','1,234,','1 234','<script>3</script>',True,None,'—','暂无数据']:
   self.assertIsNone(numeric(raw))
 def test_compact_counts_retain_source_and_approximation(self):
  x=parsed_number('54.15万');self.assertEqual(x['value'],541500);self.assertEqual(x['rawValue'],'54.15万');self.assertTrue(x['approximate']);self.assertEqual(x['displayValue'],'54.15万')
  self.assertEqual(numeric('1.2亿'),120000000);self.assertEqual(numeric('7.14千'),7140)
 def test_explicit_units_preserved_not_inferred_or_scaled(self):
  percent=parsed_number('7.5%');self.assertEqual(percent['value'],7.5);self.assertEqual(percent['unit'],'%');self.assertEqual(percent['displayValue'],'7.5%')
  plain=parsed_number('0.075');self.assertEqual(plain['value'],.075);self.assertIsNone(plain['unit']);self.assertFalse(plain['approximate'])
  self.assertEqual(parsed_number('5.3秒')['unit'],'秒');self.assertEqual(numeric('0'),0);self.assertEqual(numeric(0.00001),0.00001)
 def test_signed_net_fans_and_safe_range(self):
  self.assertIsNone(numeric('-1,234'));self.assertEqual(numeric('-1,234',signed=True),-1234);self.assertEqual(numeric('-0.1万',signed=True),-1000)
  for raw in [float('nan'),float('inf'),'9999999999999999999']:
   self.assertIsNone(numeric(raw))
 def test_real_bilibili_shapes_are_all_projected(self):
  fields=[{'key':'playCount','name':'播放量','value':'4,860'},{'key':'totalPlaysCount','name':'总播放数','value':'54.15万'},{'key':'totalGreatsCount','name':'总获赞数','value':'21,593'},{'key':'totalFansCount','name':'总粉丝数','value':'7,145'}]
  a=project({'accounts':[account(platform='bilibili')]},[row(fans=7145,platform='哔哩哔哩')],{'bilibili':[overview(fields=fields)]},AT)[0]
  metrics={m['key']:m for m in a['accountMetrics']['overview']};self.assertEqual(metrics['playCount']['value'],4860);self.assertEqual(metrics['playCount']['numberFormat'],'thousands');self.assertEqual(metrics['totalGreatsCount']['value'],21593);self.assertEqual(a['fans'],7145)
  self.assertTrue(metrics['totalPlaysCount']['approximate']);self.assertEqual(metrics['totalPlaysCount']['rawValue'],'54.15万')
 def test_abbreviated_total_does_not_replace_known_exact_total_or_conflict(self):
  a=project({'accounts':[account()]},[row(fans=7145)],{'douyin':[overview(fields=[{'key':'totalFansCount','name':'总粉丝数','value':'7.14千'}])]},AT)[0]
  self.assertEqual(a['fans'],7145);self.assertEqual(a['accountMetrics']['totalFans']['status'],'available');self.assertEqual(a['accountMetrics']['totalFans']['overviewReportedValue'],'7.14千')
 def test_nonzero_list_fans_and_source_times(self):
  a=project({'accounts':[account()]},[row(fans=1879)],{},AT)[0]
  self.assertEqual(a['fans'],1879);self.assertIsNone(a['accountMetrics']['totalFans']['updatedAt']);self.assertEqual(a['accountMetrics']['totalFans']['fetchedAt'],AT)
 def test_explicit_total_zero_is_real_zero(self):
  a=project({'accounts':[account()]},[row()],{'douyin':[overview(fields=[{'key':'totalFansCount','name':'总粉丝数','value':'0'}])]},AT)[0]
  self.assertEqual(a['fans'],0);self.assertEqual(a['accountMetrics']['totalFans']['status'],'available')
 def test_same_name_different_platform_and_id_never_join(self):
  values=project({'accounts':[account('one'),account('two'),account('one','xiaohongshu')]},[row(),row('two',42)],{'douyin':[overview('two',fields=[{'key':'totalFansCount','name':'总粉丝数','value':'99'}])]},AT)
  self.assertIsNone(values[0]['fans']);self.assertEqual(values[1]['fans'],99);self.assertEqual(values[1]['accountMetrics']['totalFans']['status'],'conflict');self.assertIsNone(values[2]['fans'])
 def test_bad_partial_overview_drops_all_metrics(self):
  bad=overview();bad['overviewData']='invalid'
  a=project({'accounts':[account()]},[row(fans=10)],{'douyin':[bad]},AT)[0]
  self.assertEqual(a['fans'],10);self.assertEqual(a['accountMetrics']['overviewStatus'],'error')
 def test_negative_net_fans_is_preserved_not_missing(self):
  a=project({'accounts':[account()]},[row()],{'douyin':[overview(fields=[{'key':'netFansCount','name':'净增粉丝','value':'-7'}])]},AT)[0]
  self.assertEqual(a['accountMetrics']['overview'][0]['value'],-7)
 def test_overview_failure_preserves_known_total_and_old_time(self):
  with tempfile.TemporaryDirectory() as root:
   t=[100];fail=[False]
   def run(args):
    if args[0]=='accounts':return [row()]
    if fail[0]:raise MetricsError('source_timeout')
    return {'data':[overview(fields=[{'key':'totalFansCount','name':'总粉丝数','value':'42'}])],'totalPage':1}
   reader=AccountsMetrics(root,run=run,clock=lambda:t[0]);local={'accounts':[account()]};first=reader.read(local)['accounts'][0];t[0]+=61;fail[0]=True;last=reader.read(local)['accounts'][0]
   self.assertEqual(last['fans'],42);self.assertEqual(last['fansUpdatedAt'],first['fansUpdatedAt']);self.assertTrue(last['accountMetrics']['totalFans']['cached']);self.assertTrue(last['accountMetrics']['overviewCached'])
 def test_duplicates_rejected_and_unknown_platform_skipped(self):
  with self.assertRaises(MetricsError):project({'accounts':[]},[row(),row()],{},AT)
  with self.assertRaises(MetricsError):project({'accounts':[]},[],{'douyin':[overview(),overview()]},AT)
 def test_pagination_explicit(self):
  calls=[]
  def run(args):
   calls.append(args);return {'data':[overview(str(len(calls)))],'totalPage':2}
  self.assertEqual(len(overview_pages('抖音',run)),2);self.assertEqual(len(calls),2)
  with self.assertRaises(MetricsError):overview_pages('抖音',lambda args:{'data':[]})
 def test_failure_keeps_old_cache_and_redacts_error(self):
  with tempfile.TemporaryDirectory() as root:
   t=[100];calls=[]
   def run(args):
    calls.append(args)
    return [row(fans=42)] if args[0]=='accounts' else {'data':[overview()],'totalPage':1}
   reader=AccountsMetrics(root,run=run,clock=lambda:t[0]);local={'accounts':[account()]}
   first=reader.read(local);self.assertEqual(first['accounts'][0]['fans'],42)
   cached=reader.read(local);self.assertEqual(len(calls),2);self.assertTrue(cached['accounts'][0]['accountMetrics']['cached'])
   def fail(args):raise Exception('credential secret')
   reader.run=fail;t[0]+=61;last=reader.read(local);self.assertEqual(last['accounts'][0]['fans'],42);self.assertEqual(last['metadataRefresh']['phase'],'error');self.assertNotIn('credential secret',json.dumps(last))
   restarted=AccountsMetrics(root,run=fail);self.assertEqual(restarted.read(local)['accounts'][0]['fans'],42)
 def test_changed_source_identity_does_not_reuse_old_cache(self):
  with tempfile.TemporaryDirectory() as root:
   reader=AccountsMetrics(root,run=lambda args:[row(fans=42)] if args[0]=='accounts' else {'data':[],'totalPage':0},clock=lambda:100)
   reader.read({'accounts':[account()]});changed=account();changed['sourceKey']='different';a=reader.read({'accounts':[changed]})['accounts'][0];self.assertNotIn('accountMetrics',a)
 def test_platform_failure_is_partial_not_success_or_account_failure(self):
  with tempfile.TemporaryDirectory() as root:
   def run(args):
    if args[0]=='accounts':return [row(fans=1)]
    raise MetricsError('source_timeout')
   reader=AccountsMetrics(root,run=run);a=reader.read({'accounts':[account()]});self.assertEqual(a['metadataRefresh']['phase'],'partial');self.assertEqual(a['accounts'][0]['fans'],1)
if __name__=='__main__':unittest.main()
