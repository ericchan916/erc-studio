#!/usr/bin/env python3
"""Narrow gateway overlay; freezes originals, checks idle, preserves credentials."""
import argparse,hashlib,json,os,sqlite3,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LIVE=Path.home()/'Library/Application Support/ContentBaseStudioGateway'
LABEL='com.contentbase.studio-conversation-gateway'
def sha(b):return hashlib.sha256(b).hexdigest()
def idle():
    db=sqlite3.connect(LIVE/'state/chat.sqlite3')
    try:n=db.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued','cancel_requested')").fetchone()[0]
    finally:db.close()
    if n:raise RuntimeError('active_chat_jobs_wait_before_restart')
def once(s,a,b):
    if s.count(a)!=1:raise RuntimeError('runtime_anchor_drift')
    return s.replace(a,b)
def prepare(change):
    idle();change.mkdir(parents=True,exist_ok=True,mode=0o700);manifest=change/'deploy-manifest.json'
    if manifest.exists():raise RuntimeError('deployment_already_prepared')
    files={'server.py':(ROOT/'backend/server.py').read_bytes(),'account_metrics.py':(ROOT/'backend/account_metrics.py').read_bytes(),
        'web/browser-adapter.js':(ROOT/'backend/web/browser-adapter.js').read_bytes(),
        'office-app/web/app.js':(ROOT/'web/app.js').read_bytes(),
        'office-app/web/account-metrics.js':(ROOT/'web/account-metrics.js').read_bytes(),
        'office-app/web/account-metrics.css':(ROOT/'web/account-metrics.css').read_bytes()}
    # Preserve installed index and all unrelated modules, insert only new resources.
    target=LIVE/'src/office-app/web/index.html';s=target.read_text()
    s=once(s,'<script src="app.js">','<script src="account-metrics.js"></script><script src="app.js">')
    s=once(s,'<link rel="stylesheet" href="studio.css">','<link rel="stylesheet" href="studio.css"><link rel="stylesheet" href="account-metrics.css">')
    s=s.replace('登录状态只反映本机空间；粉丝数以蚁小二本次返回为准。','蚁小二在线与网页登录分开判断；来源时间、历史值与缺失状态见账号数据。')
    files['office-app/web/index.html']=s.encode();items=[]
    before_paths=set()
    for p in (LIVE/'src').rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts:before_paths.add(str(p.relative_to(LIVE/'src')))
    protected={name:sha((LIVE/'src'/name).read_bytes()) for name in before_paths-set(files)}
    for name,b in files.items():
        target=LIVE/'src'/name;old=target.read_bytes() if target.exists() else None
        if target.is_symlink():raise RuntimeError('runtime_symlink')
        saved=change/'runtime-original'/name;new=change/'runtime-modified'/name
        saved.parent.mkdir(parents=True,exist_ok=True);new.parent.mkdir(parents=True,exist_ok=True)
        if old is not None:saved.write_bytes(old)
        new.write_bytes(b);items.append({'path':str(target),'relative':name,'original':sha(old) if old is not None else None,'modified':sha(b)})
    value={'files':items,'protectedRuntime':protected,'runtime':str(LIVE),'label':LABEL}
    manifest.write_text(json.dumps(value,indent=2));print(json.dumps({'prepared':len(items),'protectedRuntimeFiles':len(protected),'manifest':str(manifest)}))
def apply(change):
    idle();m=json.loads((change/'deploy-manifest.json').read_text())
    for item in m['files']:
        p=Path(item['path']);before=sha(p.read_bytes()) if p.exists() else None
        if before!=item['original']:raise RuntimeError('runtime_changed_since_prepare')
        if sha((change/'runtime-modified'/item['relative']).read_bytes())!=item['modified']:raise RuntimeError('prepared_bytes_changed')
    config=LIVE/'config.json';accounts=Path.home()/'Library/Application Support/ERC Studio/accounts.json';secrets={str(p):sha(p.read_bytes()) for p in (config,accounts)}
    for item in m['files']:
        p=Path(item['path']);b=(change/'runtime-modified'/item['relative']).read_bytes();tmp=p.with_suffix(p.suffix+'.metrics-tmp');tmp.write_bytes(b);tmp.chmod(p.stat().st_mode&0o777 if p.exists() else 0o600);tmp.replace(p)
    subprocess.run(['launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],check=True)
    port=json.loads(config.read_text())['port']
    for attempt in range(30):
        try:
            health=json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2))
            if health.get('worker_alive'):break
        except Exception:pass
        time.sleep(.5)
    else:raise RuntimeError('gateway_health_failed_use_rollback')
    for p,h in secrets.items():assert sha(Path(p).read_bytes())==h
    for name,h in m['protectedRuntime'].items():assert sha((LIVE/'src'/name).read_bytes())==h
    for item in m['files']:assert sha(Path(item['path']).read_bytes())==item['modified']
    print(json.dumps({'deployedFiles':len(m['files']),'health':health,'protectedStateUnchanged':True,'protectedRuntimeUnchanged':True}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepare',type=Path);p.add_argument('--apply',type=Path);a=p.parse_args()
    if bool(a.prepare)==bool(a.apply):p.error('select one action')
    prepare(a.prepare.resolve()) if a.prepare else apply(a.apply.resolve())
