# -*- coding: utf-8 -*-
import hashlib,http.client,json,os,sys,tempfile,threading,time,unittest,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Service,Problem
from server import handler_for,ThreadingHTTPServer,load_config
class Sender:
 def __init__(self):self.codes={};self.active=True
 def send(self,member,code,login,device):self.codes[login]=code;return {'message_id':'om_fixture'}
 def ensure_active(self,member):
  if not self.active:raise Problem(401,'member_inactive')
class Model:
 def __init__(self):self.calls=[];self.release=threading.Event();self.release.set()
 def complete(self,history,name):self.calls.append((history,name));self.release.wait(3);return '回复：'+history[-1]['content']
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.sender=Sender();self.model=Model();self.a=str(uuid.uuid4());self.b=str(uuid.uuid4());self.config={'state_dir':self.temp.name,'auth_pepper':'f'*48,'feishu_profile':'fixture','workspace_links':{'tasks':'https://TENANT.feishu.cn/base/BASE','website':'https://example.invalid','platforms':[{'id':'douyin','name':'抖音','url':'https://creator.douyin.com/'}]},'members':[{'handle':'a','principal_id':self.a,'name':'验收A','tenant_key':'tenant-fixture','open_id':'ou_a'},{'handle':'b','principal_id':self.b,'name':'验收B','tenant_key':'tenant-fixture','open_id':'ou_b'}]};self.service=Service(self.config,self.model,self.sender)
 def tearDown(self):self.model.release.set();self.service.close();self.temp.cleanup()
 def login(self,handle):
  verifier='v'*64;flow=self.service.start_login({'member_handle':handle,'client_challenge':hashlib.sha256(verifier.encode()).hexdigest(),'device_id':str(uuid.uuid4())});return self.service.poll_login({'login_id':flow['login_id'],'client_verifier':verifier,'code':self.sender.codes[flow['login_id']]})
 def conversation(self,owner):return self.service.create_conversation(owner,{'title':'测试会话'},str(uuid.uuid4()))['conversation_id']
 def wait(self,owner,jid):
  for _ in range(100):
   j=self.service.job(owner,jid)
   if j['state'] in ('succeeded','failed','cancelled'):return j
   time.sleep(.02)
  self.fail('job_timeout')
 def submit(self,owner,cid,text='真实原文'):
  return self.service.submit(owner,cid,{'text':text,'client_message_id':str(uuid.uuid4())},str(uuid.uuid4()))
 def test_login_proof_otp_and_single_use(self):
  result=self.login('a');self.assertEqual(self.service.authenticate(result['session_credential']),self.a);self.assertFalse(result['member']['capabilities']['feishu_write']);self.assertFalse(any(result['session_credential'] in str(dict(x)) for x in self.service.db.execute('SELECT * FROM sessions')))
 def test_wrong_challenge_and_code_attempts_persist(self):
  f=self.service.start_login({'member_handle':'a','client_challenge':hashlib.sha256(('x'*64).encode()).hexdigest(),'device_id':str(uuid.uuid4())})
  with self.assertRaises(Problem):self.service.poll_login({'login_id':f['login_id'],'client_verifier':'y'*64,'code':self.sender.codes[f['login_id']]})
  for _ in range(5):
   with self.assertRaises(Problem):self.service.poll_login({'login_id':f['login_id'],'client_verifier':'x'*64,'code':'BAD'})
  self.assertEqual(self.service.one('SELECT attempts FROM logins WHERE id=?',(f['login_id'],))['attempts'],5)
  with self.assertRaises(Problem):self.service.poll_login({'login_id':f['login_id'],'client_verifier':'x'*64,'code':self.sender.codes[f['login_id']]})
 def test_profile_actor_fields_and_arbitrary_member_fail(self):
  with self.assertRaises(Problem):self.service.start_login({'member_handle':'other','client_challenge':'a'*64,'device_id':str(uuid.uuid4())})
  with self.assertRaises(Problem):self.service.create_conversation(self.a,{'title':'t','owner_id':self.b},str(uuid.uuid4()))
 def test_two_members_have_separate_histories_and_model_context(self):
  self.login('a');self.login('b');a=self.conversation(self.a);b=self.conversation(self.b);j=self.submit(self.a,a,'A私有标记');self.assertEqual(self.wait(self.a,j['job_id'])['state'],'succeeded');j=self.submit(self.b,b,'B的问题');self.wait(self.b,j['job_id']);self.assertNotIn('A私有标记',str(self.model.calls[-1]));self.assertEqual(len(self.service.conversations(self.b)['items']),1)
  for action in [lambda:self.service.messages(self.b,a),lambda:self.service.submit(self.b,a,{'text':'伪造','client_message_id':str(uuid.uuid4())},str(uuid.uuid4()))]:
   with self.assertRaises(Problem) as e:action()
   self.assertEqual(e.exception.status,404)
 def test_job_and_event_cross_member_access_is_not_found(self):
  c=self.conversation(self.a);j=self.submit(self.a,c)
  for action in [lambda:self.service.job(self.b,j['job_id']),lambda:self.service.events(self.b,j['job_id']),lambda:self.service.cancel(self.b,j['job_id'],str(uuid.uuid4()))]:
   with self.assertRaises(Problem) as e:action()
   self.assertEqual(e.exception.status,404)
 def test_idempotency_reuses_job_and_rejects_changed_input(self):
  c=self.conversation(self.a);body={'text':'原文','client_message_id':str(uuid.uuid4())};key=str(uuid.uuid4());a=self.service.submit(self.a,c,body,key);b=self.service.submit(self.a,c,body,key);self.assertEqual(a,b)
  with self.assertRaises(Problem) as e:self.service.submit(self.a,c,{**body,'text':'修改'},key)
  self.assertEqual(e.exception.status,409);self.wait(self.a,a['job_id']);self.assertEqual(len(self.model.calls),1)
 def test_raw_chinese_text_is_preserved(self):
  c=self.conversation(self.a);j=self.submit(self.a,c,'  中文原文\n带空白  ');self.wait(self.a,j['job_id']);self.assertEqual(self.service.messages(self.a,c)['items'][0]['text'],'  中文原文\n带空白  ')
 def test_logout_and_member_revocation(self):
  token=self.login('a')['session_credential'];self.sender.active=False
  with self.assertRaises(Problem):self.service.authenticate(token)
  self.sender.active=True;self.service.logout(token)
  with self.assertRaises(Problem):self.service.authenticate(token)
 def test_expired_session_and_login_do_not_authenticate(self):
  token=self.login('a')['session_credential'];self.service.db.execute('UPDATE sessions SET expires=0')
  with self.assertRaises(Problem):self.service.authenticate(token)
 def test_running_cancel_waits_for_actual_model_end(self):
  self.model.release.clear();c=self.conversation(self.a);j=self.submit(self.a,c)
  for _ in range(80):
   if self.service.job(self.a,j['job_id'])['state']=='running':break
   time.sleep(.01)
  result=self.service.cancel(self.a,j['job_id'],str(uuid.uuid4()));self.assertEqual(result['state'],'cancel_requested');self.model.release.set();self.assertEqual(self.wait(self.a,j['job_id'])['state'],'cancelled');self.assertEqual(len(self.service.messages(self.a,c)['items']),1)
 def test_restart_preserves_history_and_auth(self):
  token=self.login('a')['session_credential'];c=self.conversation(self.a);j=self.submit(self.a,c);self.wait(self.a,j['job_id']);self.service.close();self.service=Service(self.config,self.model,self.sender);self.assertEqual(self.service.authenticate(token),self.a);self.assertEqual(len(self.service.messages(self.a,c)['items']),2)
 def test_unknown_interrupted_execution_is_not_replayed(self):
  c=self.conversation(self.a);self.model.release.clear();j=self.submit(self.a,c)
  for _ in range(50):
   if self.service.job(self.a,j['job_id'])['state']=='running':break
   time.sleep(.01)
  self.model.release.set();self.wait(self.a,j['job_id']);self.service.db.execute("UPDATE jobs SET state='running' WHERE id=?",(j['job_id'],));self.service.close();self.service=Service(self.config,self.model,self.sender);self.assertEqual(self.service.job(self.a,j['job_id'])['state'],'execution_unknown')
  with self.assertRaises(Problem):self.submit(self.a,c)
 def test_http_unauth_origin_input_and_cookie(self):
  class Refresher:
   def refresh(inner):return {'phase':'ok','stale':False,'refreshing':False,'readAt':'2026-10-06T00:00:00Z','topics':[],'projects':[{'id':'recFixture1234','title':'刚更新的视频'}],'jobs':[],'forms':[],'stages':[],'accounts':[],'platforms':{},'actor':{'name':'fixture','canWrite':True,'canApprove':True}}
  self.service.content_refresher=Refresher()
  self.config['allowed_hosts']=['127.0.0.1:0'];self.config['allowed_origins']=['http://127.0.0.1:0'];server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.service));port=server.server_port;self.config['allowed_hosts']=[f'127.0.0.1:{port}'];self.config['allowed_origins']=[f'http://127.0.0.1:{port}'];thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  def req(path,body=None,headers={}):
   h=http.client.HTTPConnection('127.0.0.1',port,timeout=5);h.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,headers={**({'Content-Type':'application/json'} if body is not None else {}),**headers});r=h.getresponse();raw=r.read();x=(r.status,json.loads(raw));h.close();return x
  try:
    self.assertEqual(req('/v1/conversations')[0],401);self.assertEqual(req('/v1/workspace/links')[0],401);self.assertEqual(req('/v1/workspace/snapshot?kind=analytics&days=14')[0],401);self.assertEqual(req('/health',headers={'Origin':'https://evil.test'})[0],403);self.assertEqual(req('/../../config.json')[0],401);token=self.login('a')['session_credential'];cookie={'Cookie':'studio_gateway_session='+token};self.assertEqual(req('/v1/me',headers=cookie)[1]['principal_id'],self.a);links=req('/v1/workspace/links',headers=cookie);self.assertEqual(links[0],200);self.assertEqual(links[1]['tasks'],'https://TENANT.feishu.cn/base/BASE');self.assertNotIn('auth_pepper',links[1]);fresh=req('/v1/workspace/snapshot?kind=content&refresh=1',headers=cookie);self.assertEqual(fresh[0],200);self.assertFalse(fresh[1]['stale']);self.assertEqual(fresh[1]['projects'][0]['title'],'刚更新的视频');self.assertFalse(fresh[1]['actor']['canWrite']);self.assertEqual(req('/v1/workspace/snapshot?kind=content&refresh=2',headers=cookie)[0],400);self.assertEqual(req('/v1/conversations',{'title':'X','profile':'admin'},headers={'Authorization':'Bearer '+token,'Idempotency-Key':str(uuid.uuid4())})[0],400)
  finally:server.shutdown();server.server_close();thread.join()
 def test_identity_rebinding_is_blocked(self):
  self.service.close();config={**self.config,'members':[{**m,'open_id':'ou_rebound'} if m['handle']=='a' else m for m in self.config['members']]}
  with self.assertRaises(ValueError):Service(config,self.model,self.sender)
  self.service=Service(self.config,self.model,self.sender)
if __name__=='__main__':unittest.main()
