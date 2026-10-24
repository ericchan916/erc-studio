import json,pathlib,sys,tempfile,threading,unittest,urllib.error,urllib.request
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from server import handler_for,ThreadingHTTPServer
from core import Problem

class Dummy:
    def __init__(self):
        self.config={'allowed_hosts':[],'allowed_origins':[]}
        self.calls=[]
        self.radar=SimpleNamespace(article=self.article,cached=lambda:{'version':1,'items':[]},can_read=lambda owner:owner=='owner')
    def authenticate(self,token):
        if token not in ('valid','staff'):raise Problem(401,'authentication_required')
        return 'owner' if token=='valid' else 'staff'
    def article(self,key,kind):
        self.calls.append((key,kind));return {'markdown':'# 标题\n\n正文。'}

class Tests(unittest.TestCase):
    def setUp(self):
        self.service=Dummy();self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.service));self.base='http://127.0.0.1:'+str(self.server.server_address[1]);self.service.config['allowed_hosts']=[self.base[7:]];self.service.config['allowed_origins']=[self.base];threading.Thread(target=self.server.serve_forever,daemon=True).start();self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
    def call(self,body,token='valid',origin=None):
        req=urllib.request.Request(self.base+'/v1/radar/article',data=json.dumps(body).encode(),method='POST',headers={'Cookie':'studio_gateway_session='+token,'Origin':origin or self.base,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:
            with e:return e.code,json.load(e)
    def test_auth_and_origin_precede_document_read(self):
        self.assertEqual(self.call({'key':'k','kind':'rewrite'},token='invalid')[0],401)
        self.assertEqual(self.call({'key':'k','kind':'rewrite'},origin='https://unrelated.invalid')[0],403)
        self.assertEqual(self.service.calls,[])
    def test_staff_cannot_borrow_owner_cli_to_read_archived_body(self):
        status,data=self.call({'key':'k','kind':'rewrite'},token='staff')
        self.assertEqual(status,403);self.assertEqual(data['code'],'radar_draft_owner_required');self.assertEqual(self.service.calls,[])
    def test_fixed_shape_only_and_document_is_returned(self):
        self.assertEqual(self.call({'key':'k','kind':'rewrite','url':'https://example.com'})[0],400)
        status,data=self.call({'key':'k','kind':'rewrite'});self.assertEqual(status,200);self.assertEqual(data['markdown'],'# 标题\n\n正文。');self.assertEqual(self.service.calls,[('k','rewrite')])

if __name__=='__main__':unittest.main()
