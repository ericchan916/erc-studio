#!/usr/bin/env python3
"""Narrow deployment of workflow UI/API into the existing studio web gateway."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
LIVE=Path.home()/'Library/Application Support/ContentBaseStudioGateway'
LABEL='com.contentbase.studio-conversation-gateway'

def sha(data):return hashlib.sha256(data).hexdigest()
def once(text,old,new):
    if text.count(old)!=1:raise RuntimeError('workflow_web_anchor_drift')
    return text.replace(old,new)

def server_hooks(text):
    if 'from workflow_web import workflow_snapshot' not in text:
        text=once(text,'from oauth import DeviceOAuth','from oauth import DeviceOAuth\nfrom workflow_web import workflow_snapshot')
        text=once(text,"('/v1/conversations','/v1/workspace/snapshot')","('/v1/conversations','/v1/workspace/snapshot','/v1/workflows/snapshot')")
        anchor="            if method=='GET' and path=='/v1/me':self.reply(200,service.info(owner));return"
        text=once(text,anchor,anchor+"\n            if method=='GET' and path=='/v1/workflows/snapshot':self.reply(200,workflow_snapshot(service,owner,query));return")
    return text

def adapter_hooks(text):
    if 'workflow:async input=>' in text:return text
    source=(ROOT/'backend/web/browser-adapter.js').read_text()
    line=next(line for line in source.splitlines() if line.startswith('  workflow:async input=>'))
    return once(text,' const studio={',' const studio={\n'+line)

def index_hooks(text):
    # HTML is no-store, but CSS may be cached for an hour. Rotate its content URL.
    css_name='workflow-'+sha((ROOT/'web/workflow.css').read_bytes())[:12]+'.css'
    pattern=r'href="workflow(?:-[0-9a-f]{12})?\.css"'
    matches=re.findall(pattern,text)
    if len(matches)>1:raise RuntimeError('workflow_stylesheet_anchor_drift')
    if matches:text=re.sub(pattern,'href="'+css_name+'"',text)
    else:text=once(text,'</head>','<link rel="stylesheet" href="'+css_name+'"></head>')
    if 'src="workflow-ui.js"' not in text:text=once(text,'</body>','<script src="workflow-ui.js"></script></body>')
    return text

def idle():
    p=LIVE/'state/chat.sqlite3'
    connection=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
    try:n=connection.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued','cancel_requested')").fetchone()[0]
    finally:connection.close()
    if n:raise RuntimeError('active_gateway_jobs_wait_before_restart')

def fingerprint(root):
    return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}

def prepare(change):
    idle();change.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (change/'manifest.json').exists():raise RuntimeError('workflow_deploy_already_prepared')
    src=LIVE/'src'
    files={'server.py':server_hooks((src/'server.py').read_text()).encode(),
        'web/browser-adapter.js':adapter_hooks((src/'web/browser-adapter.js').read_text()).encode(),
        'office-app/web/index.html':index_hooks((src/'office-app/web/index.html').read_text()).encode(),
        'workflow_web.py':(ROOT/'backend/workflow_web.py').read_bytes(),
        'office-app/web/workflow-ui.js':(ROOT/'web/workflow-ui.js').read_bytes(),
        'office-app/web/workflow.css':(ROOT/'web/workflow.css').read_bytes()}
    for name in ['collector.py','receipts.py','catalog.json']:files['workflow-monitor/'+name]=(ROOT/'src/workflow'/name).read_bytes()
    css=files['office-app/web/workflow.css']
    files['office-app/web/workflow-'+sha(css)[:12]+'.css']=css
    before=fingerprint(src);items=[]
    for name,data in files.items():
        target=src/name
        if target.is_symlink():raise RuntimeError('workflow_deploy_symlink')
        old=target.read_bytes() if target.exists() else None
        for directory,value in [('original',old),('modified',data)]:
            if value is None:continue
            p=change/directory/name;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);p.write_bytes(value);p.chmod(0o600)
        items.append({'relative':name,'original':sha(old) if old is not None else None,'modified':sha(data)})
    state={str(p):sha(p.read_bytes()) for p in [LIVE/'config.json',Path.home()/'Library/Application Support/ERC Studio/accounts.json']}
    manifest={'runtime':str(LIVE),'label':LABEL,'files':items,'protected':{k:v for k,v in before.items() if k not in files},'protectedState':state}
    (change/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'prepared':len(items),'preservedRuntimeFiles':len(manifest['protected']),'userConfigUnchanged':True}))

def validate(change):
    manifest=json.loads((change/'manifest.json').read_text())
    for item in manifest['files']:
        p=change/'modified'/item['relative']
        if sha(p.read_bytes())!=item['modified']:raise RuntimeError('workflow_prepared_changed')
        if p.suffix=='.py':compile(p.read_text(),str(p),'exec')
        if p.suffix=='.js':subprocess.run(['/usr/local/bin/node','--check',str(p)],check=True,capture_output=True)
    print(json.dumps({'syntaxValid':True,'files':len(manifest['files'])}))

def restart():
    subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],check=True)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with opener.open('http://127.0.0.1:8844/health',timeout=2) as response:value=json.load(response)
            if value.get('worker_alive'):return
        except Exception:pass
        time.sleep(.5)
    raise RuntimeError('workflow_gateway_health_failed')

def apply(change,rollback=False):
    idle();m=json.loads((change/'manifest.json').read_text());src=Path(m['runtime'])/'src'
    for name,h in m['protected'].items():
        if sha((src/name).read_bytes())!=h:raise RuntimeError('unrelated_runtime_changed:'+name)
    for file,h in m['protectedState'].items():
        if sha(Path(file).read_bytes())!=h:raise RuntimeError('protected_state_changed')
    for item in m['files']:
        p=src/item['relative'];current=sha(p.read_bytes()) if p.exists() else None
        if current!=(item['modified'] if rollback else item['original']):raise RuntimeError('target_changed_since_prepare:'+item['relative'])
    for item in m['files']:
        p=src/item['relative'];value=item['original'] if rollback else item['modified']
        if value is None:p.unlink();continue
        data=(change/('original' if rollback else 'modified')/item['relative']).read_bytes();assert sha(data)==value
        p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);temp=p.with_suffix(p.suffix+'.workflow-tmp');temp.write_bytes(data);temp.chmod(0o600);temp.replace(p)
    idle();restart()
    for name,h in m['protected'].items():assert sha((src/name).read_bytes())==h
    for file,h in m['protectedState'].items():assert sha(Path(file).read_bytes())==h
    print(json.dumps({'rolledBack' if rollback else 'deployed':True,'files':len(m['files']),'unrelatedRuntimePreserved':True,'credentialsAndAccountsPreserved':True}))

def rehearse(change):
    m=json.loads((change/'manifest.json').read_text());target=change/'rollback-rehearsal'
    if target.exists():raise RuntimeError('workflow_rehearsal_exists')
    target.mkdir(mode=0o700)
    for item in m['files']:
        p=target/item['relative'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((change/'modified'/item['relative']).read_bytes())
        if item['original'] is None:p.unlink()
        else:p.write_bytes((change/'original'/item['relative']).read_bytes());assert sha(p.read_bytes())==item['original']
    print(json.dumps({'rollbackRehearsal':True,'restored':sum(i['original'] is not None for i in m['files']),'newFilesRemoved':sum(i['original'] is None for i in m['files']),'businessDataTouched':False}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','validate','rehearse','deploy','rollback']);p.add_argument('--change',type=Path,required=True);a=p.parse_args();change=a.change.resolve()
    if a.action=='prepare':prepare(change)
    elif a.action=='validate':validate(change)
    elif a.action=='rehearse':rehearse(change)
    else:apply(change,a.action=='rollback')
