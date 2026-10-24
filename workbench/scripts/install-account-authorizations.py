#!/usr/bin/env python3
"""Narrow account-authorization overlay; preserve concurrent production code/config and browser state."""
import argparse,hashlib,json,os,pathlib,sqlite3,subprocess,time,urllib.request,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
LIVE=pathlib.Path.home()/'Library/Application Support/ContentBaseStudioGateway'
ASSETS=('account_authorizations.py','web/account-authorizations.js','web/account-authorizations.css')
SHA=lambda b:hashlib.sha256(b).hexdigest()
def server_overlay(text):
 if 'from account_authorizations import AccountAuthorizations' in text:return text
 needle='from publishing import Publishing, CLIError, CHUNK as PUBLISH_CHUNK'
 assert text.count(needle)==1;text=text.replace(needle,needle+'\nfrom account_authorizations import AccountAuthorizations')
 needle='token=self.token();owner=service.authenticate(token);key=self.headers.get(\'Idempotency-Key\')'
 assert text.count(needle)==1
 route="""
            if path.startswith('/v1/account-authorizations'):
                if not hasattr(service,'account_authorizations'):service.account_authorizations=AccountAuthorizations(service.config)
                auth=service.account_authorizations;auth.permit(owner)
                if path=='/v1/account-authorizations':
                    if method=='GET':self.reply(200,auth.catalog(owner));return
                if path=='/v1/account-authorizations/preview' and method=='POST':self.reply(200,auth.preview(owner,body,key));return
                auth_match=re.fullmatch(r'/v1/account-authorizations/([a-f0-9-]{36})(?:/(submit))?',path)
                if auth_match:
                    aid,action=auth_match.groups()
                    if method=='GET' and not action:self.reply(200,auth.status(owner,aid));return
                    if method=='POST' and action=='submit':self.reply(202,auth.submit(owner,aid,body));return
                raise Problem(404,'not_found')"""
 text=text.replace(needle,needle+route)
 text=text.replace("'/publish-console.css')","'/publish-console.css','/account-authorizations.js','/account-authorizations.css')")
 needle='<script src="/publish-console.js"></script></body>'
 assert text.count(needle)==1;text=text.replace(needle,'<script src="/publish-console.js"></script><link rel="stylesheet" href="/account-authorizations.css"><script src="/account-authorizations.js"></script></body>')
 text=text.replace("target.name=='publish-console.css'","target.name in ('publish-console.css','account-authorizations.css')")
 return text

def idle():
 config=json.loads((LIVE/'config.json').read_text());root=pathlib.Path(config['state_dir'])
 with sqlite3.connect((root/'chat.sqlite3').as_uri()+'?mode=ro',uri=True)as db:
  assert not db.execute("select count(*) from jobs where state in ('queued','running','cancel_requested')").fetchone()[0],'chat_jobs_in_flight'
 for folder,states in [('production-runs',{'accepted','transcribing','planning','rendering','verifying'}),('publish-runs',{'preparing','submitting'}),('account-authorization-jobs',{'submitting','accepted','running'})]:
  for p in(root/folder).glob('*.json'if folder=='account-authorization-jobs'else'*/status.json'):
   try:v=json.loads(p.read_text())
   except Exception:continue
   assert v.get('state')not in states,'jobs_in_flight'
def prepare(change):
 idle()
 change.mkdir(parents=True,exist_ok=True,mode=0o700);assert not(change/'runtime-manifest.json').exists()
 values={LIVE/'src/server.py':server_overlay((LIVE/'src/server.py').read_text()).encode()}
 for name in ASSETS:values[LIVE/'src'/name]=(ROOT/'backend'/name).read_bytes()
 files=[]
 for i,(target,raw)in enumerate(values.items()):
  before=target.read_bytes()if target.exists()else None
  for label,data in [('original',before),('modified',raw)]:
   if data is None:continue
   p=change/('runtime-'+label)/str(i);p.parent.mkdir(exist_ok=True);p.write_bytes(data);p.chmod(0o600)
  files.append({'path':str(target),'slot':str(i),'original':SHA(before)if before is not None else None,'modified':SHA(raw)})
 protected={str(p):SHA(p.read_bytes())for p in(LIVE/'src').rglob('*')if p.is_file()and'__pycache__'not in p.parts and p not in values};protected[str(LIVE/'config.json')]=SHA((LIVE/'config.json').read_bytes())
 (change/'runtime-manifest.json').write_text(json.dumps({'files':files,'protected':protected},indent=2));print('PREPARED',len(files),'resources')
def apply(change,rollback=False):
 idle()
 m=json.loads((change/'runtime-manifest.json').read_text());old='modified'if rollback else'original';new='original'if rollback else'modified'
 for n,h in m['protected'].items():assert SHA(pathlib.Path(n).read_bytes())==h,n
 for item in m['files']:
  p=pathlib.Path(item['path']);assert(SHA(p.read_bytes())if p.exists()else None)==item[old],str(p)
 for item in m['files']:
  p=pathlib.Path(item['path'])
  if item[new]is None:p.unlink();continue
  raw=(change/('runtime-'+new)/item['slot']).read_bytes();assert SHA(raw)==item[new];temp=p.with_suffix('.account-auth-tmp');temp.write_bytes(raw);temp.chmod(0o600);temp.replace(p)
 subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/com.contentbase.studio-conversation-gateway'],check=True)
 for _ in range(50):
  try:
   with urllib.request.urlopen('http://127.0.0.1:8844/health',timeout=2)as r:h=json.load(r)
   if h.get('worker_alive'):break
  except Exception:pass
  time.sleep(.2)
 else:raise RuntimeError('gateway_health_failed')
 for n,h in m['protected'].items():assert SHA(pathlib.Path(n).read_bytes())==h,n
 for item in m['files']:
  p=pathlib.Path(item['path']);assert(SHA(p.read_bytes())if p.exists()else None)==item[new]
 print('ROLLBACK_OK'if rollback else'DEPLOY_OK',len(m['files']),'resources;',len(m['protected']),'protected; worker_alive=true')
def rehearse(change):
 m=json.loads((change/'runtime-manifest.json').read_text())
 for item in m['files']:
  if item['original']is not None:assert SHA((change/'runtime-original'/item['slot']).read_bytes())==item['original']
  assert SHA((change/'runtime-modified'/item['slot']).read_bytes())==item['modified']
 print('ROLLBACK_HASH_REHEARSAL_OK')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','deploy','rollback','rehearse']);p.add_argument('--change',required=True,type=pathlib.Path);a=p.parse_args()
 if a.action=='prepare':prepare(a.change)
 elif a.action=='rehearse':rehearse(a.change)
 else:apply(a.change,a.action=='rollback')
