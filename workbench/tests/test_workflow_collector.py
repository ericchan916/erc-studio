import datetime as dt, hashlib, importlib.util, json, pathlib, plistlib, sqlite3, tempfile, unittest, sys
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'src/workflow'))
spec=importlib.util.spec_from_file_location('collector',pathlib.Path(__file__).parents[1]/'src/workflow/collector.py'); c=importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
NOW=dt.datetime(2026,10,3,3,0,tzinfo=c.UTC)
class CollectorTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.home=pathlib.Path(self.tmp.name); self.base=self.home/'Library/Application Support'; self.agents=self.home/'Library/LaunchAgents'; self.base.mkdir(parents=True);self.agents.mkdir(parents=True)
  self.catalog={'schemaVersion':1,'version':'test','registryVersion':'test','discoveryPrefixes':['com.test.'],'supervisor':'supervisor/health.json','routes':[],'components':[{'id':'test','name':'Test','kind':'service','group':'test','label':'com.test.worker','healthPath':'test/health.json','timeField':'checked_at','maxAge':60,'statuses':['ok'],'source':'src','dependencies':[]}]}
 def tearDown(self): self.tmp.cleanup()
 def health(self,**kw):
  p=self.base/'test/health.json'; p.parent.mkdir(exist_ok=True); p.write_text(json.dumps({'status':'ok','checked_at':NOW.isoformat(),**kw}))
 def collect(self,**kw): return c.collect(self.home,self.catalog,launch=lambda *_:{'loaded':True,'state':'running','lastExitCode':'(never exited)'},now=NOW,**kw)
 def test_fresh_health_and_never_exited_running(self):
  self.health();i=self.collect()['items'][0];self.assertEqual(i['status'],'healthy');self.assertEqual(i['business']['status'],'unverified')
 def test_missing_health_is_unknown_not_success(self): self.assertEqual(self.collect()['items'][0]['status'],'unknown')
 def test_stale_and_future_timestamps(self):
  for seconds,code in [(-100,'HEALTH_STALE'),(100,'HEALTH_FROM_FUTURE')]:
   self.health(checked_at=(NOW+dt.timedelta(seconds=seconds)).isoformat());self.assertIn(code,self.collect()['items'][0]['codes'])
 def test_unknown_results_and_dead_letters_are_attention(self):
  self.health(execution_unknown_count=2,dead_letter_count=1);i=self.collect()['items'][0];self.assertEqual(i['status'],'attention');self.assertIn('DEAD_LETTER_COUNT',i['codes'])
 def test_secret_projection(self):
  self.health(error='TOKEN_SECRET',account_name='PRIVATE_PERSON',token='PRIVATE_KEY',issues=[{'name':'PRIVATE_PERSON'}]);s=json.dumps(self.collect());self.assertNotIn('PRIVATE',s);self.assertNotIn('TOKEN_SECRET',s);self.assertIn('HEALTH_ERROR_PRESENT',s)
 def test_live_discovery_is_unverified_and_never_changes_plist(self):
  p=self.agents/'worker.plist'; p.write_bytes(plistlib.dumps({'Label':'com.test.new','StartInterval':60,'EnvironmentVariables':{'KEY':'SECRET'}}));before=p.read_bytes();r=self.collect();self.assertEqual(r['coverage']['discoveredServices'],1);self.assertNotIn('SECRET',json.dumps(r));self.assertEqual(p.read_bytes(),before)
 def test_continuous_stopped_vs_periodic_idle(self):
  self.health();p=self.agents/'worker.plist';p.write_bytes(plistlib.dumps({'Label':'com.test.worker','KeepAlive':True}));launch=lambda *_:{'loaded':True,'state':'not running','lastExitCode':0}
  self.assertEqual(c.collect(self.home,self.catalog,launch,NOW)['items'][0]['status'],'attention');p.write_bytes(plistlib.dumps({'Label':'com.test.worker','StartInterval':60}));self.assertEqual(c.collect(self.home,self.catalog,launch,NOW)['items'][0]['status'],'healthy')
 def test_queue_is_read_only_aggregate_and_unknown_status_hidden(self):
  p=self.base/'queue.db';con=sqlite3.connect(p);con.execute('create table notifications(state text, card_json text, updated_at text)');con.execute('insert into notifications values(?,?,?)',('secret-state','PRIVATE_BODY',NOW.isoformat()));con.commit();con.close();before=p.read_bytes();r=c.queue_probe(self.base,'queue.db','notifications');self.assertEqual(r['counts'],{'other':1});self.assertNotIn('PRIVATE',json.dumps(r));self.assertEqual(p.read_bytes(),before)
 def test_schema_missing_and_corrupt_db_distinct(self):
  p=self.base/'queue.db';p.write_bytes(b'broken');self.assertEqual(c.queue_probe(self.base,'queue.db','notifications')['state'],'unavailable');self.assertEqual(c.queue_probe(self.base,'missing.db','notifications')['state'],'missing')
 def test_source_traversal_and_symlink_denied(self):
  with self.assertRaises(ValueError): c.allowed_path(self.base,'../../secret')
  (self.base/'link').symlink_to(self.home);self.assertRaises(ValueError,c.allowed_path,self.base,'link/file')
 def test_calendar_cross_day_and_no_fake_interval_next_time(self):
  r=c.schedule({'StartCalendarInterval':{'Hour':9,'Minute':0}},NOW);self.assertEqual(r['nextAt'],'2026-10-04T09:00:00+08:00');self.assertIsNone(c.schedule({'StartInterval':60},NOW)['nextAt'])
 def test_route_dependency_failure_and_business_result_unverified(self):
  self.catalog['routes']=[{'id':'route:test','name':'Route','kind':'workflow','dependencies':['test'],'verification':'unverified'}];self.health(status='failed');r=self.collect()['items'][-1];self.assertEqual(r['status'],'attention');self.assertEqual(r['business']['status'],'unverified')
 def test_fresh_supervisor_cannot_mask_live_launchd_failure(self):
  self.health();p=self.base/'supervisor/health.json';p.parent.mkdir();p.write_text(json.dumps({'checked_at':NOW.isoformat(),'probes':[{'id':'test','healthy':True}]}));self.catalog['components'][0]['supervisorId']='test';r=c.collect(self.home,self.catalog,lambda *_:{'loaded':False,'state':'not_loaded'},NOW);self.assertEqual(r['items'][0]['status'],'attention')
 def test_optional_entry_hash_permission_error_isolated_from_workflow_health(self):
  from unittest.mock import patch
  self.health();script=self.home/'run.py';script.write_text('pass');p=self.agents/'worker.plist';p.write_bytes(plistlib.dumps({'Label':'com.test.worker','ProgramArguments':['/usr/bin/python3',str(script)]}));original=pathlib.Path.read_bytes
  def read(file):
   if file==script:raise PermissionError('fixture source denied')
   return original(file)
  with patch.object(pathlib.Path,'read_bytes',read):value=self.collect()
  self.assertEqual(value['items'][0]['status'],'healthy');self.assertEqual(value['items'][0]['runtimeCode'][0]['state'],'unavailable');self.assertIsNone(value['items'][0]['runtimeCode'][0]['sha256'])
if __name__=='__main__': unittest.main()
