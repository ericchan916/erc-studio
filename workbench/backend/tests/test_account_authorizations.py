import unittest,tempfile,pathlib,hashlib,sys,time,datetime
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from account_authorizations import AccountAuthorizations
from core import Problem
LOCAL='11111111-1111-4111-8111-111111111111';SCI='ac_'+'a'*32
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.calls=[]
  self.local=[{'id':LOCAL,'name':'相同昵称','platform':'douyin','sourceKey':hashlib.sha256(b'yxer-one').hexdigest()}]
  self.science=[{'id':SCI,'platform':'douyin','name':'相同昵称','enabled':True,'version':3,'platform_account_id':'stable-uid','auth':{'status':'healthy','next_check_at':'2099-01-01T00:00:00+00:00'},'identity':{'status':'matched'},'bindings':[{'source':'yxer','source_id':'yxer-one'}]}]
  self.auth=AccountAuthorizations({'state_dir':self.tmp.name,'members':[{'principal_id':'owner','handle':'owner'}]},read_local=lambda:self.local,read_science=lambda:self.science,read_yxer=lambda:[{'id':'yxer-one','platformName':'抖音','status':2}],run=self.run_cli)
 def run_cli(self,args):
  self.calls.append(args)
  if '--dry-run'in args:return 0,{'dry_run':True,'request':{'account_ids':[SCI]}}
  if args[:3]==['accounts','jobs','show']:
   self.science[0]['auth']['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();return 0,{'job':{'id':'job-one','state':'completed','account_ids':[SCI]}}
  return 0,{'http_status':202,'job_id':'job-one'}
 def plan(self):return self.auth.preview('owner',{'accountId':LOCAL},'fixture-key-one')
 def test_independent_status_exact_identity(self):
  row=self.auth.catalog('owner')['items'][0];self.assertEqual(row['publish']['state'],'expired');self.assertEqual(row['collect']['state'],'healthy');self.assertEqual(row['collect']['scienceId'],SCI)
 def test_same_nickname_wrong_binding_is_not_used(self):
  self.science[0]['bindings'][0]['source_id']='other';self.assertEqual(self.auth.catalog('owner')['items'][0]['collect']['state'],'unbound')
  with self.assertRaises(Problem):self.plan()
 def test_stale_health_is_unknown(self):
  self.science[0]['auth']['next_check_at']='2020-01-01T00:00:00+00:00';self.assertEqual(self.auth.catalog('owner')['items'][0]['collect']['state'],'unknown')
 def test_disabled_has_no_login(self):
  self.science[0]['enabled']=False;self.assertFalse(self.auth.catalog('owner')['items'][0]['collect']['canLogin'])
  with self.assertRaises(Problem):self.plan()
 def test_preview_is_dry_run_and_idempotent(self):
  p=self.plan();self.assertEqual(p,self.plan());self.assertEqual(len(self.calls),1);self.assertIn('--dry-run',self.calls[0]);self.assertNotIn('--confirm',self.calls[0])
 def test_submit_requires_confirmation(self):
  p=self.plan()
  with self.assertRaises(Problem):self.auth.submit('owner',p['id'],{'approved':False,'confirmation':p['confirmation']})
  self.assertEqual(len(self.calls),1)
 def test_double_submit_one_real_command(self):
  p=self.plan();body={'approved':True,'confirmation':p['confirmation']};j=self.auth.submit('owner',p['id'],body);self.assertEqual(j['state'],'accepted');self.auth.submit('owner',p['id'],body);self.assertEqual(len(self.calls),2);self.assertIn('--confirm',self.calls[1]);self.assertEqual(self.calls[0][-1],self.calls[1][-1]);self.assertEqual(self.auth.status('owner',p['id'])['state'],'succeeded')
 def test_mapping_drift_blocks_execution(self):
  p=self.plan();self.science[0]['version']=4
  with self.assertRaises(Problem):self.auth.submit('owner',p['id'],{'approved':True,'confirmation':p['confirmation']})
  self.assertEqual(len(self.calls),1)
 def test_owner_boundary(self):
  with self.assertRaises(Problem):self.auth.catalog('other')
  p=self.plan()
  with self.assertRaises(Problem):self.auth.status('other',p['id'])
 def test_unknown_execution_is_not_replayed_and_restores(self):
  p=self.plan();self.auth.run=lambda args:(_ for _ in()).throw(Problem(504,'authorization_execution_unknown'));body={'approved':True,'confirmation':p['confirmation']};j=self.auth.submit('owner',p['id'],body);self.assertEqual(j['state'],'execution_unknown');self.assertEqual(self.auth.submit('owner',p['id'],body)['state'],'execution_unknown');row=self.auth.catalog('owner')['items'][0];self.assertEqual(row['collect']['operation']['id'],p['id'])

 def test_failed_registry_is_unknown_not_unbound(self):
  self.auth.read_science=lambda:(_ for _ in()).throw(RuntimeError('fixture'));row=self.auth.catalog('owner')['items'][0];self.assertEqual(row['collect']['state'],'unknown');self.assertFalse(row['collect']['canLogin'])
