import json,pathlib,subprocess,sys,tempfile,threading,time,unittest,uuid
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from analytics_gateway import AnalyticsGateway,public_snapshot
from core import Problem

def snapshot(days=14):return {'days':days,'phase':'ok','stale':False,'refreshing':False,'readAt':'2026-10-06T10:00:00Z','_actorId':'private','_cacheScope':'private','layers':[{'id':'rum','updatedAt':'snapshot-time','current':{'pv':9,'uv':2},'pages':[],'channels':[],'goals':[]}],'syncCounts':{'overview':{'verified':1}}}
def response(value,code=0):return SimpleNamespace(returncode=code,stdout=json.dumps(value),stderr='')
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.config={'workspace_state_dir':self.tmp.name,'state_dir':self.tmp.name,'members':[{'handle':'owner','principal_id':'owner-id','open_id':'ou_owner'},{'handle':'staff','principal_id':'staff-id','open_id':'ou_staff'}]}
 def gateway(self,run=None):return AnalyticsGateway(self.config,run=run or (lambda *a,**k:response({'ok':True,'snapshot':snapshot()})))
 def finish(self,gateway,job):
  for _ in range(100):
   value=gateway.get('owner-id',job['id'])
   if value['state'] not in ('accepted','running'):return value
   time.sleep(.01)
  self.fail('worker not terminal')
 def test_live_refresh_uses_fixed_worker_read_and_sanitizes_snapshot(self):
  calls=[]
  def run(args,**kw):calls.append((args,kw));return response({'ok':True,'snapshot':snapshot()})
  value=self.gateway(run).read('staff-id',14);self.assertEqual(calls[0][0][0],'/usr/local/bin/node');self.assertEqual(calls[0][0][-3:],['read','14','']);self.assertFalse(value['canSync']);self.assertFalse(value['stale']);self.assertNotIn('_actorId',value)
 def test_concurrent_live_reads_merge_one_real_call(self):
  release=threading.Event();entered=threading.Event();calls=[];values=[]
  def run(*a,**k):calls.append(1);entered.set();release.wait(2);return response({'ok':True,'snapshot':snapshot()})
  gateway=self.gateway(run);a=threading.Thread(target=lambda:values.append(gateway.read('owner-id',14)));b=threading.Thread(target=lambda:values.append(gateway.read('staff-id',14)));a.start();entered.wait(1);b.start();time.sleep(.03);release.set();a.join();b.join();self.assertEqual(len(calls),1);self.assertEqual({v['canSync'] for v in values},{True,False})
 def test_refresh_error_or_stale_response_is_never_success(self):
  for result in [response({'ok':False,'error':'analytics_lark_authorization_required'},1),response({'ok':True,'snapshot':{**snapshot(),'stale':True}}),SimpleNamespace(returncode=0,stdout='bad',stderr='private token')]:
   with self.assertRaises(Problem):self.gateway(lambda *a,**k:result).read('owner-id',14)
 def test_sync_role_bound_idempotent_and_cross_owner_private(self):
  calls=[]
  def run(args,**kw):calls.append(args);return response({'ok':True,'snapshot':snapshot()})
  g=self.gateway(run)
  with self.assertRaises(Problem) as error:g.start('staff-id',{'days':14},str(uuid.uuid4()))
  self.assertEqual(error.exception.status,403);self.assertEqual(calls,[])
  key=str(uuid.uuid4());job=g.start('owner-id',{'days':14},key);done=self.finish(g,job);self.assertEqual(done['state'],'succeeded');self.assertEqual(g.start('owner-id',{'days':14},key)['id'],job['id']);self.assertEqual(len(calls),1);self.assertEqual(calls[0][-1],'ou_owner');self.assertNotIn('owner',done);self.assertNotIn('actor',done)
  with self.assertRaises(Problem):g.get('staff-id',job['id'])
  with self.assertRaises(Problem):g.start('owner-id',{'days':7},key)
 def test_busy_different_key_or_window_does_not_start_second_worker(self):
  release=threading.Event();calls=[]
  def run(*a,**k):calls.append(1);release.wait(2);return response({'ok':True,'snapshot':snapshot()})
  g=self.gateway(run);job=g.start('owner-id',{'days':14},str(uuid.uuid4()))
  with self.assertRaises(Problem):g.start('owner-id',{'days':7},str(uuid.uuid4()))
  release.set();self.finish(g,job);self.assertEqual(len(calls),1)
 def test_prewrite_failure_is_failed_but_postwrite_failure_blocks_new_sync(self):
  for unknown,state in [(False,'failed'),(True,'execution_unknown')]:
   with tempfile.TemporaryDirectory() as directory:
    g=AnalyticsGateway({**self.config,'state_dir':directory},run=lambda *a,**k:response({'ok':False,'error':'analytics_readback_failed','mayHaveWritten':unknown},1));job=g.start('owner-id',{'days':14},str(uuid.uuid4()));self.assertEqual(self.finish(g,job)['state'],state)
    if unknown:
     with self.assertRaises(Problem):g.start('owner-id',{'days':14},str(uuid.uuid4()))
 def test_restart_retains_unknown_original_job_without_replay(self):
  g=self.gateway();jid=str(uuid.uuid4());(g.jobs/(jid+'.json')).write_text(json.dumps({'id':jid,'owner':'owner-id','key':'existing','days':14,'state':'running'}));calls=[];restarted=self.gateway(lambda *a,**k:calls.append(a));self.assertEqual(restarted.state('owner-id')['activeJob']['state'],'execution_unknown');self.assertEqual(calls,[])
 def test_unknown_verification_requires_complete_matching_dimensions(self):
  g=self.gateway();jid=str(uuid.uuid4());layer=snapshot()['layers'][0];expected={**layer,'days':14};path=g.jobs/(jid+'.json');job={'id':jid,'owner':'owner-id','key':'known','days':14,'state':'execution_unknown','expected':{**expected,'pages':[{'path':'/missing'}]}};path.write_text(json.dumps(job))
  with self.assertRaises(Problem):g.verify('owner-id',jid)
  job['expected']=expected;path.write_text(json.dumps(job));self.assertEqual(g.verify('owner-id',jid)['state'],'succeeded')
 def test_parameter_validation_before_any_worker(self):
  calls=[];g=self.gateway(lambda *a,**k:calls.append(a))
  for days in [True,1,123,'14',None]:
   with self.assertRaises(Problem):g.start('owner-id',{'days':days},str(uuid.uuid4()))
  self.assertEqual(calls,[])

if __name__=='__main__':unittest.main()
