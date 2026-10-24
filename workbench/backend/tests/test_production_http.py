import json,pathlib,sys,tempfile,threading,unittest,urllib.request,urllib.error,uuid,socket
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]));from server import handler_for
from core import Problem
class Producer:
 def __init__(self,file):self.file=file;self.upload_calls=0
 def engine(self):return {'ready':True,'version':'4.0.531','checkedAt':123}
 def get(self,owner,id):return {'id':id,'state':'succeeded'}
 def video(self,owner,id):return self.file
 def upload(self,owner,id,offset,chunk,checksum):self.upload_calls+=1;return {'received':offset+len(chunk)}
class Dummy:
 def __init__(self,file):self.config={'allowed_hosts':[],'allowed_origins':[]};self.production=Producer(file)
 def authenticate(self,token):
  if token!='valid':raise Problem(401,'authentication_required')
  return 'owner'
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);p=pathlib.Path(self.tmp.name)/'output.mp4';p.write_bytes(b'0123456789');self.service=Dummy(p);self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.service));host='127.0.0.1:'+str(self.server.server_address[1]);self.service.config['allowed_hosts']=[host];self.service.config['allowed_origins']=['http://'+host];self.base='http://'+host;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
 def req(self,path,method='GET',token='valid',headers=None,body=None):
  h={'Host':self.base[7:],'Cookie':'studio_gateway_session='+token,**(headers or {})};return urllib.request.Request(self.base+path,method=method,headers=h,data=body)
 def test_video_serves_authenticated_range_only(self):
  id=str(uuid.uuid4());
  with urllib.request.urlopen(self.req('/v1/production/jobs/'+id+'/video',headers={'Range':'bytes=2-5'})) as r:self.assertEqual(r.status,206);self.assertEqual(r.read(),b'2345');self.assertEqual(r.headers['Content-Range'],'bytes 2-5/10');self.assertEqual(r.headers['Cache-Control'],'private, no-store')
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.req('/v1/production/jobs/'+id+'/video',token='invalid'))
  self.assertEqual(e.exception.code,401);e.exception.close()
 def test_upload_requires_cookie_and_matching_origin_before_binary_read(self):
  id=str(uuid.uuid4())
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.req('/v1/production/jobs/'+id+'/source','POST','invalid',{'Content-Type':'application/octet-stream','X-Upload-Offset':'0','X-Chunk-SHA256':'0'*64},b'x'))
  self.assertEqual(e.exception.code,401);e.exception.close()
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.req('/v1/production/jobs/'+id+'/source','POST','valid',{'Content-Type':'application/octet-stream','Origin':'https://unrelated.invalid','X-Upload-Offset':'0','X-Chunk-SHA256':'0'*64},b'x'))
  self.assertEqual(e.exception.code,403);e.exception.close()
 def test_rejects_invalid_video_range(self):
  id=str(uuid.uuid4())
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.req('/v1/production/jobs/'+id+'/video',headers={'Range':'bytes=11-12'}))
  self.assertEqual(e.exception.code,416);e.exception.close()
 def test_engine_status_requires_auth_and_reports_backend_probe(self):
  with urllib.request.urlopen(self.req('/v1/production/engine')) as r:self.assertEqual(json.load(r)['version'],'4.0.531')
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.req('/v1/production/engine',token='invalid'))
  self.assertEqual(e.exception.code,401);e.exception.close()
 def test_large_rejected_upload_returns_json_without_tcp_reset_or_production_call(self):
  for origin,status in [('',401),('https://unrelated.invalid',403)]:
   body=b'x'*(512*1024);headers='POST /v1/production/jobs/'+str(uuid.uuid4())+'/source HTTP/1.1\r\nHost: '+self.base[7:]+'\r\nContent-Type: application/octet-stream\r\nContent-Length: '+str(len(body))+'\r\nCookie: studio_gateway_session=invalid\r\n'+('Origin: '+origin+'\r\n' if origin else '')+'Connection: close\r\n\r\n'
   with socket.create_connection(self.server.server_address,timeout=5) as s:
    s.sendall(headers.encode()+body);answer=b''
    while True:
     data=s.recv(65536)
     if not data:break
     answer+=data
   self.assertIn(('HTTP/1.0 '+str(status)).encode(),answer.split(b'\r\n')[0]);self.assertIn(b'application/json',answer);self.assertEqual(self.service.production.upload_calls,0)
 def test_authenticated_large_upload_is_consumed_once(self):
  body=b'x'*(512*1024);id=str(uuid.uuid4())
  with urllib.request.urlopen(self.req('/v1/production/jobs/'+id+'/source','POST','valid',{'Content-Type':'application/octet-stream','X-Upload-Offset':'0','X-Chunk-SHA256':'0'*64},body)) as r:self.assertEqual(r.status,200);self.assertEqual(json.load(r)['received'],len(body))
  self.assertEqual(self.service.production.upload_calls,1)
if __name__=='__main__':unittest.main()
