#!/usr/bin/env python3
"""Four-file gateway update, exact hashes and rollback; never replaces App/resources/data."""
import argparse,hashlib,json,os,pathlib,sqlite3,subprocess,time,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
LIVE=pathlib.Path.home()/'Library/Application Support/ContentBaseStudioGateway'
LABEL='com.contentbase.studio-conversation-gateway'
FILES=('server.py','publishing.py','web/publish-console.js','web/publish-console.css')
sha=lambda raw:hashlib.sha256(raw).hexdigest()
def idle():
    config=json.loads((LIVE/'config.json').read_text());state=pathlib.Path(config['state_dir']);db=state/'chat.sqlite3'
    if db.is_file():
        c=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)
        try:
            rows=c.execute("select count(*) from jobs where state in ('queued','running','cancel_requested')").fetchone()[0]
            if rows:raise RuntimeError('gateway_jobs_in_flight')
        finally:c.close()
    for name,states in [('production-runs',{'accepted','transcribing','planning','rendering','verifying'}),('publish-runs',{'preparing','submitting'})]:
        for path in (state/name).glob('*/status.json'):
            if json.loads(path.read_text()).get('state')in states:raise RuntimeError('gateway_jobs_in_flight')
def prepare(change):
    idle();change.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (change/'publish-runtime-manifest.json').exists():raise RuntimeError('already_prepared')
    files=[]
    for name in FILES:
        target=LIVE/'src'/name;source=ROOT/'backend'/name;before=target.read_bytes()if target.exists()else None;after=source.read_bytes()
        if name=='server.py':
            baseline=change/'source-original/integrations/studio-account-matrix/backend/server.py'
            if not baseline.is_file()or baseline.read_bytes()!=before:raise RuntimeError('live_server_drift_review_required')
        for label,raw in [('runtime-original',before),('runtime-modified',after)]:
            if raw is None:continue
            p=change/label/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);p.chmod(0o600)
        files.append({'name':name,'path':str(target),'before':sha(before)if before is not None else None,'after':sha(after)})
    protected={str(p):sha(p.read_bytes()) for p in (LIVE/'src').rglob('*') if p.is_file()and'__pycache__'not in p.parts and str(p)not in {f['path']for f in files}}
    protected[str(LIVE/'config.json')]=sha((LIVE/'config.json').read_bytes())
    value={'files':files,'protected':protected};(change/'publish-runtime-manifest.json').write_text(json.dumps(value,indent=2));print(json.dumps({'prepared':len(files),'protected':len(protected)}))
def record(change,event):
    with (change/'verification.jsonl').open('a')as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
def apply(change,rollback=False):
    idle();manifest=json.loads((change/'publish-runtime-manifest.json').read_text());expected='after'if rollback else'before';desired='before'if rollback else'after'
    for name,value in manifest['protected'].items():assert sha(pathlib.Path(name).read_bytes())==value,name
    for f in manifest['files']:
        target=pathlib.Path(f['path']);actual=sha(target.read_bytes())if target.exists()else None
        assert actual==f[expected],f['name']
    for f in manifest['files']:
        target=pathlib.Path(f['path'])
        if f[desired]is None:target.unlink();continue
        raw=(change/('runtime-original'if rollback else'runtime-modified')/f['name']).read_bytes();assert sha(raw)==f[desired]
        target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_suffix(target.suffix+'.publish-deploy');temp.write_bytes(raw);temp.chmod(0o600);temp.replace(target)
    command=['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'];p=subprocess.run(command,capture_output=True,text=True);record(change,{'command':command,'stdout':p.stdout,'stderr':p.stderr,'exit':p.returncode,'rollback':rollback});p.check_returncode()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(50):
        try:
            with opener.open('http://127.0.0.1:8844/health',timeout=2)as r:health=json.load(r)
            if health.get('worker_alive'):break
        except Exception:pass
        time.sleep(.2)
    else:raise RuntimeError('gateway_health_failed')
    for f in manifest['files']:
        target=pathlib.Path(f['path']);assert (sha(target.read_bytes())if target.exists()else None)==f[desired]
    for name,value in manifest['protected'].items():assert sha(pathlib.Path(name).read_bytes())==value,name
    answer={'rolledBack'if rollback else'deployed':True,'files':len(manifest['files']),'protected':len(manifest['protected']),'worker_alive':True};record(change,answer);print(json.dumps(answer))
def rehearse(change):
    manifest=json.loads((change/'publish-runtime-manifest.json').read_text());out=change/'publish-rollback-rehearsal';out.mkdir(exist_ok=True)
    for f in manifest['files']:
        p=out/f['name'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((change/'runtime-modified'/f['name']).read_bytes());assert sha(p.read_bytes())==f['after']
        if f['before']is None:p.unlink();assert not p.exists()
        else:
            p.write_bytes((change/'runtime-original'/f['name']).read_bytes());assert sha(p.read_bytes())==f['before']
            if p.suffix=='.py':compile(p.read_text(),str(p),'exec')
            elif p.suffix=='.js':subprocess.run(['node','--check',str(p)],check=True)
    print(json.dumps({'rollbackRehearsed':True,'restoredRoles':len(manifest['files'])}))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','deploy','rollback','rehearse']);parser.add_argument('--change',type=pathlib.Path,required=True);a=parser.parse_args()
    if a.action=='prepare':prepare(a.change)
    elif a.action=='rehearse':rehearse(a.change)
    else:apply(a.change,a.action=='rollback')
