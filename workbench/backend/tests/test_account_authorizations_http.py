import unittest,tempfile,pathlib,threading,json,urllib.request,urllib.error,sys
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from server import handler_for
from core import Problem
import test_account_authorizations as domain
LOCAL=domain.LOCAL
class Service:
 def __init__(self,auth):self.config={'allowed_hosts':[],'allowed_origins':[]};self.account_authorizations=auth
 def authenticate(self,token):
  if token=='valid':return'owner'
  if token=='other':return'other'
  raise Problem(401,'authentication_required')
class HTTPTests(unittest.TestCase):
 def setUp(self):
  self.fixture=domain.Tests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups);self.s=Service(self.fixture.auth);self.http=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.s));host='127.0.0.1:'+str(self.http.server_port);self.base='http://'+host;self.s.config.update(allowed_hosts=[host],allowed_origins=[self.base]);threading.Thread(target=self.http.serve_forever,daemon=True).start();self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
 def req(self,path,body=None,token='valid',origin=None):
  h={'Cookie':'studio_gateway_session='+token,'Content-Type':'application/json','Idempotency-Key':'fixture-key-http'}
  if origin:h['Origin']=origin
  try:
   with urllib.request.urlopen(urllib.request.Request(self.base+path,data=json.dumps(body).encode()if body is not None else None,headers=h),timeout=5)as r:return r.status,json.load(r)
  except urllib.error.HTTPError as e:return e.code,json.load(e)
 def test_auth_origin_and_owner_gates(self):
  self.assertEqual(self.req('/v1/account-authorizations',token='invalid')[0],401);self.assertEqual(self.req('/v1/account-authorizations',token='other')[0],403);self.assertEqual(self.req('/v1/account-authorizations',origin='https://evil.example')[0],403)
 def test_preview_and_explicit_submit(self):
  status,plan=self.req('/v1/account-authorizations/preview',{'accountId':LOCAL});self.assertEqual(status,200);self.assertEqual(len(self.fixture.calls),1)
  self.assertEqual(self.req('/v1/account-authorizations/'+plan['id']+'/submit',{'approved':False,'confirmation':plan['confirmation']})[0],400)
  status,job=self.req('/v1/account-authorizations/'+plan['id']+'/submit',{'approved':True,'confirmation':plan['confirmation']});self.assertEqual(status,202);self.assertEqual(job['state'],'accepted');self.assertEqual(len(self.fixture.calls),2)
 def test_catalog_does_not_start_login_or_collect(self):
  status,catalog=self.req('/v1/account-authorizations');self.assertEqual(status,200);self.assertEqual(catalog['items'][0]['accountId'],LOCAL);self.assertEqual(self.fixture.calls,[])
