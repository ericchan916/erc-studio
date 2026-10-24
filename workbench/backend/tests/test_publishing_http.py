import hashlib,json,pathlib,sys,tempfile,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from server import handler_for
from publishing import Publishing
from test_publishing import OWNER,FakeCLI,IMAGE
from core import Problem

class Service:
    def __init__(self,root):
        self.config={'allowed_hosts':[],'allowed_origins':[],'state_dir':root,'members':[{'principal_id':OWNER,'handle':'owner'}]};self.publishing=Publishing(self.config,FakeCLI())
    def authenticate(self,token):
        if token!='valid':raise Problem(401,'authentication_required')
        return OWNER

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=Service(self.tmp.name);self.http=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.s));host='127.0.0.1:'+str(self.http.server_address[1]);self.base='http://'+host;self.s.config['allowed_hosts']=[host];self.s.config['allowed_origins']=[self.base];threading.Thread(target=self.http.serve_forever,daemon=True).start();self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
    def req(self,path,body=None,token='valid',headers=None):
        raw=json.dumps(body).encode() if body is not None and not isinstance(body,bytes)else body;h={'Cookie':'studio_gateway_session='+token,**({'Content-Type':'application/octet-stream'if isinstance(body,bytes)else'application/json'}if body is not None else{}),**(headers or{})}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base+path,data=raw,headers=h),timeout=5)as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:
            with e:return e.code,json.load(e)
    def create(self):
        b={'kind':'imageText','channel':'cloud','title':'测试','description':'测试正文','targets':[{'accountId':'a','fields':{}}],'files':[{'name':'photo.png','size':len(IMAGE),'role':'image'}]};status,j=self.req('/v1/publish/jobs',b,headers={'Idempotency-Key':'http-test-key'});self.assertEqual(status,201,j);return j
    def test_no_auth_or_cross_origin_cannot_access_catalog_or_create(self):
        self.assertEqual(self.req('/v1/publish/catalog',token='invalid')[0],401);self.assertEqual(self.req('/v1/publish/catalog',headers={'Origin':'https://evil.test'})[0],403)
    def test_authenticated_form_create_and_binary_upload(self):
        j=self.create();h={'X-Upload-Offset':'0','X-Chunk-SHA256':hashlib.sha256(IMAGE).hexdigest()};status,v=self.req('/v1/publish/jobs/'+j['id']+'/files/'+j['files'][0]['id'],IMAGE,headers=h);self.assertEqual(status,200,v);self.assertEqual(v['files'][0]['received'],len(IMAGE))
    def test_binary_rejects_bad_identity_before_persistence_and_retains_json_response(self):
        j=self.create();status,v=self.req('/v1/publish/jobs/'+j['id']+'/files/'+j['files'][0]['id'],IMAGE,token='invalid',headers={'X-Upload-Offset':'0','X-Chunk-SHA256':hashlib.sha256(IMAGE).hexdigest()});self.assertEqual(status,401);self.assertEqual(self.s.publishing.get(OWNER,j['id'])['files'][0]['received'],0)
    def test_submit_endpoint_never_implicitly_approves(self):
        j=self.create();self.assertEqual(self.req('/v1/publish/jobs/'+j['id']+'/submit',{})[0],400)
    def test_whole_media_is_not_sent_to_text_json_parser(self):
        j=self.create();self.assertEqual(self.req('/v1/publish/jobs/'+j['id']+'/files/'+j['files'][0]['id'],b'x'*65537,headers={'X-Upload-Offset':'0'})[0],413)

if __name__=='__main__':unittest.main()
