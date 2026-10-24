#!/usr/bin/env python3
"""Narrow website analytics rollout; preserve live CSP, credentials and sessions."""
import argparse,difflib,hashlib,json,os,sqlite3,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LIVE=Path.home()/'Library/Application Support/ContentBaseStudioGateway'
LABEL='com.contentbase.studio-conversation-gateway'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def fingerprint(root):return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
def idle():
 config=json.loads((LIVE/'config.json').read_text());root=Path(config['state_dir']);db=sqlite3.connect((root/'chat.sqlite3').as_uri()+'?mode=ro',uri=True)
 try:count=db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running','cancel_requested')").fetchone()[0]
 finally:db.close()
 if count:raise RuntimeError('active_chat_jobs')
 for folder,states in [('production-runs',{'accepted','transcribing','planning','rendering','verifying'}),('publish-runs',{'preparing','submitting'}),('analytics-runs',{'accepted','running'})]:
  for path in (root/folder).glob('*.json' if folder=='analytics-runs' else '*/status.json'):
   if json.loads(path.read_text()).get('state') in states:raise RuntimeError('active_'+folder)
def record(change,event):
 with (change/'deployment.jsonl').open('a') as file:file.write(json.dumps(event,ensure_ascii=False)+'\n')
def prepare(change):
 idle();change.mkdir(parents=True,exist_ok=True,mode=0o700)
 if (change/'manifest.json').exists():raise RuntimeError('already_prepared')
 src=LIVE/'src';before=fingerprint(src);mapping={'analytics_gateway.py':ROOT/'backend/analytics_gateway.py','analytics_refresh.cjs':ROOT/'backend/analytics_refresh.cjs','web/browser-adapter.js':ROOT/'backend/web/browser-adapter.js','office-app/web/analytics-ui.js':ROOT/'web/analytics-ui.js','office-app/web/analytics-transport.js':ROOT/'web/analytics-transport.js'}
 for name in ['analytics.cjs','analytics-model.cjs','analytics-schema.cjs','analytics-source.cjs','view-cache.cjs','content/client.cjs','content/model.cjs']:mapping[name]=ROOT/'src'/name
 for path in (ROOT/'src/tencent-rum').glob('*'):mapping['tencent-rum/'+path.name]=path
 original=change/'source-original/backend/server.py'
 if not original.is_file():raise RuntimeError('source_baseline_required')
 patch=''.join(difflib.unified_diff(original.read_text().splitlines(True),(ROOT/'backend/server.py').read_text().splitlines(True),fromfile='server.py',tofile='server.py',n=1))
 (change/'server-source.patch').write_text(patch);staging=change/'server-staging';staging.mkdir();(staging/'server.py').write_bytes((src/'server.py').read_bytes())
 command=['/usr/bin/patch','-p0','-i',str(change/'server-source.patch')];p=subprocess.run(command,cwd=staging,capture_output=True,text=True);record(change,{'command':command,'stdout':p.stdout,'stderr':p.stderr,'exit':p.returncode});p.check_returncode();mapping['server.py']=staging/'server.py'
 files=[]
 for name,source in mapping.items():
  target=src/name
  if target.is_symlink():raise RuntimeError('runtime_symlink')
  old=target.read_bytes() if target.exists() else None;new=source.read_bytes()
  if name in ('view-cache.cjs','content/client.cjs','content/model.cjs') and old is not None and old!=new:raise RuntimeError('shared_dependency_drift:'+name)
  for folder,raw in [('original',old),('modified',new)]:
   if raw is None:continue
   path=change/folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o600)
  files.append({'path':name,'before':sha(old) if old is not None else None,'after':sha(new),'mode':target.stat().st_mode&0o777 if old is not None else 0o600})
 protected={k:v for k,v in before.items() if k not in mapping};state={str(p):sha(p.read_bytes()) for p in [LIVE/'config.json',Path.home()/'Library/Application Support/ERC Studio/analytics-space.json']}
 manifest={'runtime':str(src),'files':files,'protected':protected,'protectedState':state};(change/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));print(json.dumps({'prepared':len(files),'preserved':len(protected),'configPreserved':True}))
def check(change):
 m=json.loads((change/'manifest.json').read_text())
 for item in m['files']:
  p=change/'modified'/item['path'];assert sha(p.read_bytes())==item['after']
  if p.suffix=='.py':compile(p.read_text(),str(p),'exec')
  if p.suffix in ('.js','.cjs','.mjs'):subprocess.run(['/usr/local/bin/node','--check',str(p)],check=True,capture_output=True)
 return m
def preserved(m):
 src=Path(m['runtime'])
 for path,value in m['protected'].items():assert sha((src/path).read_bytes())==value,path
 for path,value in m['protectedState'].items():assert sha(Path(path).read_bytes())==value,path
def apply(change,rollback=False):
 idle();m=check(change);preserved(m);src=Path(m['runtime']);expected='after' if rollback else 'before';desired='before' if rollback else 'after'
 for item in m['files']:
  p=src/item['path'];assert (sha(p.read_bytes()) if p.exists() else None)==item[expected],item['path']
 for item in m['files']:
  p=src/item['path']
  if item[desired] is None:p.unlink();continue
  raw=(change/('original' if rollback else 'modified')/item['path']).read_bytes();assert sha(raw)==item[desired];p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.analytics-deploy');tmp.write_bytes(raw);tmp.chmod(item['mode']);tmp.replace(p)
 command=['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'];p=subprocess.run(command,capture_output=True,text=True);record(change,{'command':command,'stdout':p.stdout,'stderr':p.stderr,'exit':p.returncode,'rollback':rollback});p.check_returncode()
 for _ in range(60):
  try:
   with urllib.request.urlopen('http://127.0.0.1:8844/health',timeout=2) as response:health=json.load(response)
   if health.get('worker_alive'):break
  except Exception:pass
  time.sleep(.2)
 else:raise RuntimeError('health_unverified')
 for item in m['files']:
  path=src/item['path'];assert (sha(path.read_bytes()) if path.exists() else None)==item[desired],item['path']
 preserved(m);result={'rollback' if rollback else 'deployed':True,'health':'ok','worker_alive':True,'preserved':len(m['protected']),'files':len(m['files'])};record(change,result);print(json.dumps(result))
def rehearse(change):
 m=check(change);root=change/'rollback-rehearsal';root.mkdir(exist_ok=True)
 for item in m['files']:
  p=root/item['path'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((change/'modified'/item['path']).read_bytes());assert sha(p.read_bytes())==item['after']
  if item['before'] is None:p.unlink();assert not p.exists()
  else:p.write_bytes((change/'original'/item['path']).read_bytes());assert sha(p.read_bytes())==item['before']
 result={'rollbackRehearsed':True,'files':len(m['files']),'businessAndSessionsUntouched':True};record(change,result);print(json.dumps(result))
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','deploy','rollback','rehearse']);parser.add_argument('--change',type=Path,required=True);args=parser.parse_args()
 if args.action=='prepare':prepare(args.change)
 elif args.action=='rehearse':rehearse(args.change)
 else:apply(args.change,args.action=='rollback')
