import hashlib,json,sys,tempfile,threading,time,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Problem
from oauth import DeviceOAuth

class OAuthTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();r=Path(self.tmp.name);home=r/'home';(home/'.lark-cli').mkdir(parents=True);(home/'Library/Keychains').mkdir(parents=True);store=home/'Library/Application Support/lark-cli';store.mkdir(parents=True)
  app={'appId':'cli_exampleWorkspace','appSecret':{'source':'keychain','id':'appsecret:cli_exampleWorkspace'},'brand':'feishu','users':[{'userOpenId':'original'}]};(home/'.lark-cli/config.json').write_text(json.dumps({'apps':[app]}))
  for name in ['appsecret_cli_exampleWorkspace.enc','master.key.file','cli_exampleWorkspace_original.enc']:(store/name).write_bytes(b'protected');(store/name).chmod(0o600)
  self.home=home;self.config={'state_dir':str(r/'state'),'lark_home':str(home),'lark_cli':'/fixture/cli','feishu_profile':app['appId']};self.calls=[];self.now=1000;self.release=threading.Event()
  self.oauth=DeviceOAuth(self.config,runner=self.runner,clock=lambda:self.now);self.verifier='v'*64;self.body={'client_challenge':hashlib.sha256(self.verifier.encode()).hexdigest(),'device_id':str(uuid.uuid4())}
 def tearDown(self):self.release.set();self.tmp.cleanup()
 def runner(self,argv,**kwargs):
  self.calls.append((argv,kwargs));args=argv[3:]
  if args[:2]==['auth','login'] and '--no-wait' in args:j={'verification_url':'https://accounts.feishu.cn/fixture','device_code':'server_only','expires_in':240}
  elif args[:2]==['auth','qrcode']:(Path(kwargs['cwd'])/'qrcode.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture');j={'ok':True}
  elif args[:2]==['auth','login']:self.release.wait(2);j={'ok':True}
  elif args[:2]==['api','GET']:j={'ok':True,'data':{'open_id':'ou_actual','tenant_key':'tenant_actual','name':'unused'}}
  else:j={'ok':True}
  return SimpleNamespace(returncode=0,stdout=json.dumps(j),stderr='')
 def poll(self,start,**changes):return self.oauth.poll({'login_id':start['login_id'],'client_verifier':self.verifier,**changes},lambda i:{'status':'granted','actual':i})
 def test_isolates_home_config_and_never_copies_user_tokens(self):
  s=self.oauth.start(self.body);d=self.oauth.flows[s['login_id']]['directory'];c=json.loads((d/'config/config.json').read_text());self.assertEqual(c['apps'][0]['users'],[])
  self.assertTrue((d/'Library/Keychains').is_symlink());self.assertTrue((d/'Library/Application Support/lark-cli/appsecret_cli_exampleWorkspace.enc').is_symlink());self.assertFalse((d/'Library/Application Support/lark-cli/cli_exampleWorkspace_original.enc').exists())
  self.assertNotIn('device_code',s);self.assertTrue(s['qrcode_data_url'].startswith('data:image/png;base64,'));self.assertEqual(self.calls[0][1]['env']['HOME'],str(d));self.assertEqual(json.loads((self.home/'.lark-cli/config.json').read_text())['apps'][0]['users'][0]['userOpenId'],'original')
 def test_challenge_required_before_poll_or_grant(self):
  s=self.oauth.start(self.body)
  with self.assertRaises(Problem):self.poll(s,client_verifier='wrong'*13)
  self.assertEqual(len(self.calls),2)
 def test_actual_identity_single_use_and_no_start_polling(self):
  s=self.oauth.start(self.body);self.assertEqual(len(self.calls),2);self.assertEqual(self.poll(s)['status'],'pending');self.release.set()
  for _ in range(100):
   if self.oauth.flows[s['login_id']]['state']=='verified':break
   time.sleep(.01)
  j=self.poll(s);self.assertEqual(j['actual'],{'open_id':'ou_actual','tenant_key':'tenant_actual'})
  with self.assertRaises(Problem):self.poll(s)
 def test_expiry_throttle_and_client_identity_denied(self):
  s=self.oauth.start(self.body)
  with self.assertRaises(Problem):self.oauth.start(self.body)
  with self.assertRaises(Problem):self.oauth.start({**self.body,'open_id':'spoof'})
  self.now=1300
  with self.assertRaises(Problem):self.poll(s)
 def test_official_host_only(self):
  def bad(argv,**kwargs):return SimpleNamespace(returncode=0,stdout=json.dumps({'verification_url':'https://evil.test/','device_code':'x','expires_in':240}),stderr='')
  self.oauth.runner=bad
  with self.assertRaises(Problem):self.oauth.start(self.body)
 def test_denied_member_consumes_verified_proof(self):
  s=self.oauth.start(self.body);f=self.oauth.flows[s['login_id']];f['state']='verified';f['identity']={'open_id':'outside','tenant_key':'other'}
  def deny(i):raise Problem(403,'oauth_member_not_allowed')
  with self.assertRaises(Problem):self.oauth.poll({'login_id':s['login_id'],'client_verifier':self.verifier},deny)
  self.assertEqual(f['state'],'consumed')
if __name__=='__main__':unittest.main()
