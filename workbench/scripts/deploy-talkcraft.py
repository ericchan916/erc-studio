#!/usr/bin/env python3
"""Narrow Talkcraft overlay; no peer code, credentials, or business data restore."""
import argparse,hashlib,json,os,pathlib,shutil,sqlite3,subprocess,tempfile,time,urllib.request
SOURCE=pathlib.Path(__file__).resolve().parents[1];LIVE=pathlib.Path.home()/'Library/Application Support/ContentBaseStudioGateway';LABEL='com.contentbase.studio-conversation-gateway'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def mappings():
 rows={SOURCE/'backend'/n:LIVE/'src'/n for n in ('production_backend.py','production_talkcraft.py','production_talkcraft_executor.py')}
 rows.update({SOURCE/'web'/n:LIVE/'src/office-app/web'/n for n in ('production-model.js','production-ui.js')})
 template=SOURCE/'production-remotion'
 for name in ('talkcraft.schema.json','TALKCRAFT_WORKFLOW.md','talkcraft-media.swift','src/TalkcraftFilm.tsx','src/TalkcraftRoot.tsx'):rows[template/name]=LIVE/'src/production-remotion'/name
 for folder in ('talkcraft','src/talkcraft-cards'):
  for p in (template/folder).rglob('*'):
   if p.is_file() and '__pycache__' not in p.parts:rows[p]=LIVE/'src/production-remotion'/p.relative_to(template)
 return rows

def preflight():
 db=LIVE/'state/chat.sqlite3'
 with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
  if conn.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued','cancel_requested')").fetchone()[0]:raise RuntimeError('chat_jobs_in_flight')
 for folder,states in [('production-runs',{'accepted','transcribing','planning','rendering'}),('publish-runs',{'preparing','submitting'}),('account-authorization-jobs',{'submitting','accepted','running'})]:
  for p in (LIVE/'state'/folder).glob('*/status.json'):
   if json.loads(p.read_text()).get('state') in states:raise RuntimeError('runtime_job_in_flight:'+folder)
 return {'chatJobs':0,'productionJobs':0,'publishJobs':0,'businessDBNotRestored':True}

def prepare(change):
 preflight();change.mkdir(parents=True,exist_ok=True,mode=0o700);change.chmod(0o700)
 manifest=change/'runtime-manifest.json'
 if manifest.exists():raise RuntimeError('overlay_already_prepared')
 rows=mappings();items=[]
 for index,(src,dst) in enumerate(rows.items()):
  if not src.is_file() or src.is_symlink() or dst.is_symlink():raise RuntimeError('invalid_overlay_file')
  old=dst.read_bytes() if dst.is_file() else None;before=change/'runtime-original'/str(index);after=change/'runtime-modified'/str(index)
  before.parent.mkdir(parents=True,exist_ok=True);after.parent.mkdir(parents=True,exist_ok=True)
  if old is not None:before.write_bytes(old);before.chmod(0o600)
  after.write_bytes(src.read_bytes());after.chmod(0o600)
  items.append({'source':str(src),'target':str(dst),'before':hashlib.sha256(old).hexdigest() if old is not None else None,'after':digest(after),'original':str(before) if old is not None else None,'modified':str(after),'mode':dst.stat().st_mode&0o777 if old is not None else 0o644})
 unchanged={str(p):digest(p) for p in (LIVE/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p not in rows.values()}
 data={'files':items,'untouched':unchanged,'protectedConfig':digest(LIVE/'config.json'),'preflight':preflight(),'statePolicy':'Keep all old jobs, source bytes, sessions, database, credentials; never restore mutable data.'}
 manifest.write_text(json.dumps(data,indent=2));manifest.chmod(0o600);print(json.dumps({'prepared':len(items),'untouched':len(unchanged),'configNotCopied':True}))

def verify(change,after):
 data=json.loads((change/'runtime-manifest.json').read_text());key='after' if after else 'before'
 for row in data['files']:
  if digest(pathlib.Path(row['target']))!=row[key]:raise RuntimeError('overlay_target_changed:'+row['target'])
 for name,sha in data['untouched'].items():
  if digest(pathlib.Path(name))!=sha:raise RuntimeError('unrelated_runtime_changed:'+name)
 if digest(LIVE/'config.json')!=data['protectedConfig']:raise RuntimeError('config_changed')
 return data

def _restore(data):
 for row in data['files']:
  dst=pathlib.Path(row['target'])
  if row['original'] is None:
   if dst.exists():dst.unlink()
  else:
   tmp=dst.with_name(dst.name+'.talkcraft-rollback');tmp.write_bytes(pathlib.Path(row['original']).read_bytes());tmp.chmod(row['mode']);os.replace(tmp,dst)

def rehearse(change):
 data=json.loads((change/'runtime-manifest.json').read_text())
 with tempfile.TemporaryDirectory(prefix='rollback-rehearsal-',dir=str(change)) as folder:
  copy=[]
  for index,row in enumerate(data['files']):
   dst=pathlib.Path(folder)/str(index);dst.write_bytes(pathlib.Path(row['modified']).read_bytes());copy.append({**row,'target':str(dst)})
  _restore({'files':copy})
  for row in copy:
   if digest(pathlib.Path(row['target']))!=row['before']:raise RuntimeError('rehearsal_failed')
 print(json.dumps({'rehearsed':len(copy),'baselineRestoredOnCopy':True,'liveUntouched':True}))

def restart():
 subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],check=True)
 for _ in range(60):
  try:
   with urllib.request.urlopen('http://127.0.0.1:8844/health',timeout=2) as response:health=json.load(response)
   if health.get('worker_alive'):return health
  except Exception:pass
  time.sleep(.5)
 raise RuntimeError('gateway_health_missing')

def deploy(change):
 preflight();data=verify(change,False)
 for row in data['files']:
  if digest(pathlib.Path(row['modified']))!=row['after']:raise RuntimeError('modified_artifact_changed')
  dst=pathlib.Path(row['target']);dst.parent.mkdir(parents=True,exist_ok=True);tmp=dst.with_name(dst.name+'.talkcraft-install');tmp.write_bytes(pathlib.Path(row['modified']).read_bytes());tmp.chmod(row['mode']);os.replace(tmp,dst)
 health=restart();verify(change,True);print(json.dumps({'deployed':len(data['files']),'workerAlive':health['worker_alive'],'otherSourcesAndConfigUnchanged':True,'businessWrites':False}))

def rollback(change):
 preflight();data=verify(change,True);_restore(data);health=restart();verify(change,False);print(json.dumps({'rolledBack':len(data['files']),'workerAlive':health['worker_alive'],'stateAndCredentialsRetained':True}))

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','rehearse','deploy','verify','rollback']);parser.add_argument('--change',type=pathlib.Path,required=True);args=parser.parse_args();change=args.change.resolve()
 if args.action=='verify':verify(change,True);print('RUNTIME_OVERLAY_AND_PEERS_VERIFIED')
 else:globals()[args.action](change)
