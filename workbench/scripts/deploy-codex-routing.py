#!/usr/bin/env python3
"""Three-way narrow overlay. Never restore credentials, history or business state."""
import argparse,hashlib,json,os,pathlib,shutil,sqlite3,subprocess,tempfile,time,urllib.request
S=pathlib.Path(__file__).resolve().parents[1];R=pathlib.Path.home()/'Library/Application Support/ContentBaseStudioGateway';LABEL='com.contentbase.studio-conversation-gateway'
FILES={'backend/core.py':'src/core.py','backend/execution_adapter.py':'src/execution_adapter.py','backend/server.py':'src/server.py','backend/local_codex.py':'src/local_codex.py','backend/production_backend.py':'src/production_backend.py','backend/production_skill.py':'src/production_skill.py','backend/web/browser-adapter.js':'src/web/browser-adapter.js','web/production-ui.js':'src/office-app/web/production-ui.js','web/production-model.js':'src/office-app/web/production-model.js'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def save(p,j):p.write_text(json.dumps(j,indent=2));p.chmod(0o600)
def projection(c):return hashlib.sha256(json.dumps({k:v for k,v in c.items() if k!='local_codex'},sort_keys=True).encode()).hexdigest()
def idle(rollback=False):
 with sqlite3.connect((R/'state/chat.sqlite3').as_uri()+'?mode=ro',uri=True) as d:
  if d.execute("select count(*) from jobs where state in ('running','queued','cancel_requested')").fetchone()[0]:raise RuntimeError('chat_in_flight')
  if rollback:
   rows=d.execute("select e.request_json from conversation_execution e join jobs j on j.id=e.job where j.state in ('waiting_input','waiting_confirmation','waiting_authorization','paused','execution_unknown')").fetchall()
   if any(json.loads(x[0]).get('engine')=='codex-cli' for x in rows):raise RuntimeError('native_pending_job_kept_current_route')
 for folder in ['production-runs','publish-runs','account-authorization-jobs']:
  for p in (R/'state'/folder).glob('*/status.json'):
   value=json.loads(p.read_text())
   if value.get('state') in ('accepted','planning','rendering','transcribing','running','submitting','preparing'):raise RuntimeError('in_flight:'+folder)
   if rollback and value.get('workflowVersion')=='video-use-codex-cli-v1' and value.get('state') in ('waiting_input','execution_unknown'):raise RuntimeError('native_production_pending_kept_current_route')
def prepare(b):
 idle();b.mkdir(parents=True,exist_ok=True,mode=0o700)
 if (b/'runtime-manifest.json').exists():raise RuntimeError('already_prepared')
 rows=[]
 for rel,target in FILES.items():
  src=S/rel;dst=R/target;old=b/'runtime-original'/target;new=b/'runtime-modified'/target;old.parent.mkdir(parents=True,exist_ok=True);new.parent.mkdir(parents=True,exist_ok=True)
  if dst.is_symlink():raise RuntimeError('unsafe_target')
  if dst.exists():shutil.copy2(dst,old)
  base=b/'original-source'/rel
  if not base.exists() and rel not in ('backend/local_codex.py','backend/production_skill.py'):
   raw=subprocess.check_output(['git','show','HEAD:integrations/studio-account-matrix/'+rel],cwd=S);base.parent.mkdir(parents=True,exist_ok=True);base.write_bytes(raw)
  if dst.exists() and base.exists():
   p=subprocess.run(['git','merge-file','-p',str(old),str(base),str(src)],capture_output=True)
   if p.returncode and rel=='backend/server.py':
    # The live publisher CSP was installed by a parallel task. Preserve its
    # exact three untouched blocks while applying only this task's HTTP routes.
    merged=src.read_text();live=old.read_text();expected=base.read_text()
    declaration=next((x for x in live.splitlines() if x.startswith('PUBLISHER_FRAME_ORIGIN=')),None)
    if not declaration:raise RuntimeError('unrecognized_server_conflict')
    anchor="PUBLIC_SITE_ORIGIN='https://example.invalid'"
    merged=merged.replace(anchor,anchor+'\n'+declaration,1);expected=expected.replace(anchor,anchor+'\n'+declaration,1)
    for tag in ('raw=raw.replace(needle,','Content-Security-Policy'):
     live_lines=[x for x in live.splitlines() if tag in x and 'PUBLISHER_FRAME_ORIGIN' in x]
     current=[x for x in merged.splitlines() if tag in x and (tag!='Content-Security-Policy' or 'raw=target.read_bytes()' in x)]
     if len(live_lines)!=1 or len(current)!=1:raise RuntimeError('server_peer_block_ambiguous')
     merged=merged.replace(current[0],live_lines[0],1)
     before=[x for x in expected.splitlines() if tag in x and (tag!='Content-Security-Policy' or 'raw=target.read_bytes()' in x)]
     if len(before)!=1:raise RuntimeError('server_peer_baseline_ambiguous')
     expected=expected.replace(before[0],live_lines[0],1)
    if expected!=live:raise RuntimeError('unrecognized_server_peer_changes')
    new.write_text(merged)
   elif p.returncode and rel in ('backend/production_backend.py','web/production-ui.js'):
    baseline=subprocess.check_output(['git','show','HEAD:integrations/studio-account-matrix/'+rel],cwd=S)
    if old.read_bytes()!=baseline:raise RuntimeError('unrecognized_previous_prototype_conflict')
    new.write_bytes(src.read_bytes())
   elif p.returncode:
    (b/('merge-conflict-'+src.name)).write_bytes(p.stdout);raise RuntimeError('merge_conflict:'+rel)
   else:new.write_bytes(p.stdout)
  else:shutil.copy2(src,new)
  rows.append({'rel':target,'source':str(src),'before':sha(old),'after':sha(new),'mode':dst.stat().st_mode&0o777 if dst.exists() else 0o644})
 c=json.loads((R/'config.json').read_text());protected={str(p.relative_to(R)):sha(p) for p in (R/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts and str(p.relative_to(R)) not in FILES.values()}
 save(b/'runtime-manifest.json',{'files':rows,'protected':protected,'configProjection':projection(c),'previousLocalCodex':c.get('local_codex'),'hadLocalCodex':'local_codex' in c});print(json.dumps({'prepared':len(rows),'otherFilesProtected':len(protected)}))
def verify(b,after):
 m=json.loads((b/'runtime-manifest.json').read_text())
 for r in m['files']:
  if sha(R/r['rel'])!=r['after' if after else 'before']:raise RuntimeError('changed_target:'+r['rel'])
 for p,h in m['protected'].items():
  if sha(R/p)!=h:raise RuntimeError('unrelated_runtime_changed:'+p)
 c=json.loads((R/'config.json').read_text());assert projection(c)==m['configProjection'];return m
def restart():
 subprocess.run(['launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],check=True)
 for _ in range(90):
  try:
   with urllib.request.urlopen('http://127.0.0.1:8844/health',timeout=2) as r:j=json.load(r)
   if j.get('worker_alive'):return j
  except Exception:pass
  time.sleep(.5)
 raise RuntimeError('health_unavailable')
def apply(b,rollback=False):
 idle(rollback);m=verify(b,rollback)
 for r in m['files']:
  dst=R/r['rel'];src=b/('runtime-original' if rollback else 'runtime-modified')/r['rel']
  if rollback and r['before'] is None:
   if dst.exists():dst.unlink()
  else:
   assert sha(src)==r['before' if rollback else 'after'];dst.parent.mkdir(parents=True,exist_ok=True);tmp=dst.with_suffix(dst.suffix+'.codex-routing');tmp.write_bytes(src.read_bytes());tmp.chmod(r['mode']);tmp.replace(dst)
 c=json.loads((R/'config.json').read_text())
 if rollback:
  if m['hadLocalCodex']:c['local_codex']=m['previousLocalCodex']
  else:c.pop('local_codex',None)
 else:c['local_codex']={'enabled':True}
 save(R/'config.json',c);health=restart();verify(b,not rollback);print(json.dumps({'rolledBack' if rollback else 'deployed':len(m['files']),'health':health,'historyCredentialsAndOtherRuntimePreserved':True}))
def rehearse(b):
 m=json.loads((b/'runtime-manifest.json').read_text())
 with tempfile.TemporaryDirectory(dir=b) as t:
  for i,r in enumerate(m['files']):
   p=pathlib.Path(t)/str(i);p.write_bytes((b/'runtime-modified'/r['rel']).read_bytes())
   if r['before'] is None:p.unlink();assert not p.exists()
   else:p.write_bytes((b/'runtime-original'/r['rel']).read_bytes());assert sha(p)==r['before']
 print(json.dumps({'rollbackRehearsed':len(m['files']),'mutableDataNotRestored':True}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','rehearse','deploy','rollback','verify']);p.add_argument('--change',required=True,type=pathlib.Path);a=p.parse_args();b=a.change.resolve()
 if a.action=='prepare':prepare(b)
 elif a.action=='rehearse':rehearse(b)
 elif a.action=='verify':verify(b,True);print('RUNTIME_SCOPE_VERIFIED')
 else:apply(b,a.action=='rollback')
