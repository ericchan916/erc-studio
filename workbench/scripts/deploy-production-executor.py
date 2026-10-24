#!/usr/bin/env python3
"""Deploy only the authenticated production pipeline; preserve all data and peer modules."""
import argparse,hashlib,json,os,pathlib,sqlite3,subprocess,time,urllib.request
S=pathlib.Path(__file__).resolve().parents[1];R=pathlib.Path.home()/'Library/Application Support/ContentBaseStudioGateway';LABEL='com.contentbase.studio-conversation-gateway';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
FILES={S/'backend/production_director.py':R/'src/production_director.py',S/'backend/server.py':R/'src/server.py',S/'backend/production_backend.py':R/'src/production_backend.py',S/'backend/web/browser-adapter.js':R/'src/web/browser-adapter.js',S/'web/production-model.js':R/'src/office-app/web/production-model.js',S/'web/production-ui.js':R/'src/office-app/web/production-ui.js',S/'web/production.css':R/'src/office-app/web/production.css'}
for p in (S/'production-remotion').rglob('*'):
 if p.is_file():FILES[p]=R/'src/production-remotion'/p.relative_to(S/'production-remotion')
def preflight():
 with sqlite3.connect((R/'state/chat.sqlite3').as_uri()+'?mode=ro',uri=True) as db:assert db.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued','cancel_requested')").fetchone()[0]==0,'chat_jobs_in_flight'
 d=R/'state/production-runs'
 if d.exists():
  for p in d.glob('*/status.json'):
   try:
    if json.loads(p.read_text())['state'] in ('accepted','transcribing','planning','rendering'):raise RuntimeError('production_job_in_flight')
   except (OSError,ValueError,KeyError):pass
 for p in ['/Users/USER/resources/YOUR_A1FACD7F45','/opt/homebrew/bin/ffmpeg','/opt/homebrew/bin/ffprobe','/opt/homebrew/bin/whisper-cli','/Users/USER/resources/YOUR_219F7CE40F']:
  assert pathlib.Path(p).is_file(),'missing_local_dependency:'+p
 assert pathlib.Path('/Users/USER/resources/YOUR_4CA1C6F4A7').is_file()
 assert pathlib.Path('/Users/USER/resources/YOUR_B21D3855ED').is_file()
def prepare(base):
 preflight();base.mkdir(parents=True,exist_ok=True,mode=0o700);assert not (base/'runtime-manifest.json').exists(),'already_prepared'
 protected={str(p):sha(p) for p in [R/'config.json',R/'state/chat.sqlite3',pathlib.Path.home()/'Library/Application Support/ERC Studio/accounts.json',pathlib.Path.home()/'Library/Application Support/ERC Studio/content-cache.json']}
 untouched={str(p):sha(p) for p in (R/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p not in FILES.values()}
 items=[]
 for i,(src,dst) in enumerate(FILES.items()):
  old=dst.read_bytes() if dst.is_file() else None;new=src.read_bytes();o=base/'runtime-original'/str(i);m=base/'runtime-modified'/str(i);o.parent.mkdir(parents=True,exist_ok=True);m.parent.mkdir(parents=True,exist_ok=True)
  if old is not None:o.write_bytes(old);o.chmod(0o600)
  m.write_bytes(new);m.chmod(0o600)
  items.append({'path':str(dst),'source':str(src),'before':hashlib.sha256(old).hexdigest() if old is not None else None,'after':hashlib.sha256(new).hexdigest(),'original':str(o) if old is not None else None,'modified':str(m),'mode':dst.stat().st_mode&0o777 if old is not None else 0o644})
 f=base/'runtime-manifest.json';f.write_text(json.dumps({'files':items,'protected':protected,'untouched':untouched},indent=2));f.chmod(0o600);print(json.dumps({'prepared':len(items),'untouched':len(untouched)}))
def rollback(base):
 preflight();m=json.loads((base/'runtime-manifest.json').read_text())
 for e in m['files']:assert sha(pathlib.Path(e['path']))==e['after'],'rollback_target_changed:'+e['path']
 for e in m['files']:
  p=pathlib.Path(e['path'])
  if e['original'] is None:p.unlink()
  else:p.write_bytes(pathlib.Path(e['original']).read_bytes());p.chmod(e['mode'])
 subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],check=True)
 assert all(sha(pathlib.Path(e['path']))==e['before'] for e in m['files'])
 print(json.dumps({'rolledBack':len(m['files']),'stateAndConfigNotRestored':True}))
def deploy(base):
 preflight();m=json.loads((base/'runtime-manifest.json').read_text());
 for p,h in m['untouched'].items():assert sha(pathlib.Path(p))==h,'peer_runtime_changed:'+p
 for e in m['files']:assert sha(pathlib.Path(e['path']))==e['before'],'target_runtime_changed:'+e['path'];assert sha(pathlib.Path(e['modified']))==e['after']
 for e in m['files']:
  p=pathlib.Path(e['path']);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.production-deploy');tmp.write_bytes(pathlib.Path(e['modified']).read_bytes());tmp.chmod(e['mode']);os.replace(tmp,p)
 subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],check=True)
 for _ in range(45):
  try:
   with urllib.request.urlopen('http://127.0.0.1:8844/health',timeout=2) as r:health=json.load(r)
   if health.get('worker_alive'):break
  except Exception:pass
  time.sleep(.4)
 else:raise RuntimeError('gateway_health_failed_after_code_install')
 assert all(sha(pathlib.Path(p))==h for p,h in m['untouched'].items())
 assert all(sha(pathlib.Path(e['path']))==e['after'] for e in m['files'])
 print(json.dumps({'deployed':len(m['files']),'worker_alive':health['worker_alive'],'unrelated_runtime_same':len(m['untouched']),'config_unchanged':sha(R/'config.json')==m['protected'][str(R/'config.json')]}))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','deploy','rollback']);a.add_argument('--change',type=pathlib.Path,required=True);v=a.parse_args();{'prepare':prepare,'deploy':deploy,'rollback':rollback}[v.action](v.change.resolve())
