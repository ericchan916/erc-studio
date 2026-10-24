import json,pathlib,sys,tempfile,threading,unittest,urllib.error,urllib.request,uuid
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from server import handler_for,ThreadingHTTPServer,browser_index
from analytics_gateway import AnalyticsGateway
from core import Problem
class Dummy:
 def __init__(self,root):
  self.config={'allowed_hosts':[],'allowed_origins':[],'workspace_state_dir':str(root),'state_dir':str(root),'members':[{'handle':'owner','principal_id':'owner-id','open_id':'ou_owner'},{'handle':'staff','principal_id':'staff-id','open_id':'ou_staff'}]};self.calls=[]
  def run(args,**kw):
   self.calls.append(args);days=int(args[4]);snapshot={'phase':'ok','stale':False,'refreshing':False,'days':days,'readAt':'2026-10-06T00:00:00Z','layers':[{'id':'rum','current':{'pv':10},'updatedAt':'latest'}]}
   return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'snapshot':snapshot}),stderr='')
  self.analytics=AnalyticsGateway(self.config,run=run)
 def authenticate(self,token):
  if token not in ('owner','staff'):raise Problem(401,'authentication_required')
  return token+'-id'
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.service=Dummy(pathlib.Path(self.tmp.name));self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.service));self.base='http://127.0.0.1:'+str(self.server.server_address[1]);self.service.config['allowed_hosts']=[self.base[7:]];self.service.config['allowed_origins']=[self.base];threading.Thread(target=self.server.serve_forever,daemon=True).start();self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
 def call(self,path,method='GET',token='owner',body=None,key=None,origin=None):
  headers={'Cookie':'studio_gateway_session='+token}
  if method=='POST':headers.update({'Content-Type':'application/json','Origin':origin or self.base,'Idempotency-Key':key or str(uuid.uuid4())})
  request=urllib.request.Request(self.base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None,method=method)
  try:
   with urllib.request.urlopen(request) as response:return response.status,json.load(response)
  except urllib.error.HTTPError as error:
   with error:return error.code,json.load(error)
 def test_refresh_calls_real_read_contract_for_all_windows_and_staff(self):
  for days in (7,14,30,90):
   status,data=self.call('/v1/workspace/snapshot?kind=analytics&days='+str(days)+'&refresh=1',token='staff');self.assertEqual(status,200);self.assertEqual(data['days'],days);self.assertFalse(data['stale']);self.assertFalse(data['canSync'])
  self.assertTrue(all(args[3]=='read' for args in self.service.calls))
 def test_unauthenticated_refresh_and_sync_have_no_worker_calls(self):
  for path,method,body in [('/v1/workspace/snapshot?kind=analytics&days=14&refresh=1','GET',None),('/v1/analytics/sync','POST',{'days':14}),('/v1/analytics/state','GET',None)]:self.assertEqual(self.call(path,method,'invalid',body)[0],401)
  self.assertEqual(self.service.calls,[])
 def test_member_and_origin_boundaries_precede_mutation(self):
  self.assertEqual(self.call('/v1/analytics/sync','POST','staff',{'days':14})[0],403)
  self.assertEqual(self.call('/v1/analytics/sync','POST','owner',{'days':14},origin='https://evil.invalid')[0],403);self.assertEqual(self.service.calls,[])
 def test_accepted_job_readback_and_duplicate_key_use_original_receipt(self):
  key=str(uuid.uuid4());status,job=self.call('/v1/analytics/sync','POST',body={'days':14},key=key);self.assertEqual(status,202);thread=self.service.analytics.threads.get(job['id'])
  if thread:thread.join(2)
  status,done=self.call('/v1/analytics/jobs/'+job['id']);self.assertEqual(status,200);self.assertEqual(done['state'],'succeeded');self.assertEqual(done['snapshot']['layers'][0]['current']['pv'],10)
  self.assertEqual(self.call('/v1/analytics/sync','POST',body={'days':14},key=key)[1]['id'],job['id']);self.assertEqual(len(self.service.calls),1)
  self.assertEqual(self.call('/v1/analytics/jobs/'+job['id'],token='staff')[0],404)
 def test_invalid_window_extra_fields_and_queries_do_not_dispatch(self):
  for body in ({},{'days':123},{'days':'14'},{'days':14,'sourceRoot':'arbitrary'}):self.assertEqual(self.call('/v1/analytics/sync','POST',body=body)[0],400)
  self.assertEqual(self.call('/v1/analytics/state?arbitrary=x')[0],400);self.assertEqual(self.service.calls,[])
 def test_renderer_loads_transport_before_adapter_without_native_changes(self):
  value=browser_index(b'<body>connect-src \'self\'; object-src \'none\'<script src="browser-adapter.js"></script></body>');self.assertLess(value.index(b'analytics-transport.js'),value.index(b'browser-adapter.js'))

if __name__=='__main__':unittest.main()
