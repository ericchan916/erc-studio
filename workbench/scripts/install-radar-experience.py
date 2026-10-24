#!/usr/bin/env python3
"""Narrow update of the existing radar UI, read-only draft endpoint, and rollback."""
import argparse, hashlib, json, os, subprocess
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location('radar_deploy', ROOT / 'scripts/deploy-radar-web.py')
base = module_from_spec(spec)
spec.loader.exec_module(base)
sha = base.sha


def patch(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError('radar_experience_anchor_drift:' + before[:50])
    return text.replace(before, after)


def server_patch(text):
    begin="            if method=='GET' and path=='/v1/radar/snapshot':"
    end="            if method=='GET' and path=='/v1/me':"
    a=text.index(begin);b=text.index(end,a)
    source=(ROOT/'backend/server.py').read_text()
    sa=source.index("            if method=='POST' and path=='/v1/radar/article':");sb=source.index(end,sa)
    return text[:a]+source[sa:sb]+text[b:]


def prepare(change):
    base.idle()
    change.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = change / 'runtime-manifest.json'
    if manifest.exists():
        raise RuntimeError('change_already_prepared')
    live = base.LIVE / 'src'
    server = (live / 'server.py').read_text()
    server = server_patch(server)
    adapter = (live / 'web/browser-adapter.js').read_text()
    old = "refresh:async()=>request('/v1/radar/snapshot?refresh=1')}"
    new = "refresh:async()=>request('/v1/radar/snapshot?refresh=1'),article:async(key,kind)=>request('/v1/radar/article',{method:'POST',body:{key,kind}})}"
    adapter = patch(adapter, old, new)
    index = (live / 'office-app/web/index.html').read_text()
    index = patch(index, 'href="radar.css"', 'href="radar-experience-icons.css"')
    index = patch(index, '<script src="radar-ui.js"></script>', '<script src="radar-article.js"></script><script src="radar-ui.js"></script>')
    modified = {'server.py':server.encode(), 'web/browser-adapter.js':adapter.encode(),
                'office-app/web/index.html':index.encode(),
                'radar_snapshot.py':(ROOT / 'backend/radar_snapshot.py').read_bytes(),
                'office-app/web/radar-ui.js':(ROOT / 'web/radar-ui.js').read_bytes(),
                'office-app/web/radar.css':(ROOT / 'web/radar.css').read_bytes(),
                'office-app/web/radar-experience.css':(ROOT / 'web/radar.css').read_bytes(),
                'office-app/web/radar-experience-icons.css':(ROOT / 'web/radar.css').read_bytes(),
                'office-app/web/radar-article.js':(ROOT / 'web/radar-article.js').read_bytes(),
                'office-app/web/vendor/tabler-content/radar-2.svg':(ROOT / 'web/vendor/tabler-content/radar-2.svg').read_bytes()}
    before = base.fingerprint(live)
    files = []
    for name, value in modified.items():
        existed = (live / name).exists()
        original = (live / name).read_bytes() if existed else b''
        for folder, content in [('original-runtime',original), ('modified-runtime',value)]:
            p = change / folder / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            p.chmod(0o600)
        files.append({'relative':name,'existed':existed,'original':sha(original),'modified':sha(value)})
    config = base.LIVE / 'config.json'
    manifest.write_text(json.dumps({'runtime':str(base.LIVE),'files':files,
        'protected':{k:v for k,v in before.items() if k not in modified},
        'config_hash':sha(config.read_bytes())}, ensure_ascii=False, indent=2))
    manifest.chmod(0o600)
    print(json.dumps({'prepared':True,'files':len(files),'protectedFiles':len(before)-sum(f['existed'] for f in files)}))


def read_manifest(change):
    m = json.loads((change / 'runtime-manifest.json').read_text())
    if Path(m['runtime']) != base.LIVE:
        raise RuntimeError('unexpected_runtime_target')
    return m


def validate(change):
    m = read_manifest(change)
    for f in m['files']:
        p = change / 'modified-runtime' / f['relative']
        if sha(p.read_bytes()) != f['modified']:
            raise RuntimeError('modified_artifact_drift')
        if p.suffix == '.py':compile(p.read_text(),str(p),'exec')
        if p.suffix == '.js':subprocess.run(['/usr/local/bin/node','--check',str(p)],check=True)
    print(json.dumps({'validated':True,'files':len(m['files'])}))


def stage(change):
    """Refresh reviewed source in the same prepared change; keep its original backup."""
    m=read_manifest(change)
    # Identity notification is a narrow lifecycle signal, not a new auth path.
    home='office-app/web/home-chat.js'
    if not any(f['relative']==home for f in m['files']):
        original=(base.LIVE/'src'/home).read_bytes()
        if sha(original)!=m['protected'].get(home):raise RuntimeError('identity_shell_changed')
        p=change/'original-runtime'/home;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(original);p.chmod(0o600)
        m['files'].append({'relative':home,'existed':True,'original':sha(original),'modified':''})
        del m['protected'][home]
    text=(change/'original-runtime'/home).read_text()
    anchor="function renderIdentity(){document.body.classList.toggle('studio-auth-locked',!member);"
    text=patch(text,anchor,anchor+"if(typeof window.CustomEvent==='function')window.dispatchEvent(new window.CustomEvent('studio-identity',{detail:member?.principal_id||null}));")
    text=patch(text,'isSignedIn:()=>!!member,leave:','isSignedIn:()=>!!member,principal:()=>member?.principal_id||null,leave:')
    p=change/'modified-runtime'/home;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);p.chmod(0o600)
    for f in m['files']:
        if f['relative']==home:f['modified']=sha(p.read_bytes())
    for f in m['files']:
        p=base.LIVE/'src'/f['relative']
        if f['existed']:
            if not p.is_file() or sha(p.read_bytes())!=f['original']:raise RuntimeError('runtime_no_longer_original')
        elif p.exists():raise RuntimeError('runtime_no_longer_original')
    server=server_patch((change/'original-runtime/server.py').read_text())
    (change/'modified-runtime/server.py').write_text(server)
    for f in m['files']:
        if f['relative']=='server.py':f['modified']=sha(server.encode())
    sources={'radar_snapshot.py':'backend/radar_snapshot.py',
             'office-app/web/radar-ui.js':'web/radar-ui.js',
             'office-app/web/radar.css':'web/radar.css',
             'office-app/web/radar-experience.css':'web/radar.css',
             'office-app/web/radar-experience-icons.css':'web/radar.css',
             'office-app/web/radar-article.js':'web/radar-article.js',
             'office-app/web/vendor/tabler-content/radar-2.svg':'web/vendor/tabler-content/radar-2.svg'}
    for f in m['files']:
        if f['relative'] in sources:
            data=(ROOT/sources[f['relative']]).read_bytes()
            p=change/'modified-runtime'/f['relative'];p.write_bytes(data);p.chmod(0o600);f['modified']=sha(data)
    rehearsal=change/'rollback-rehearsal'/home
    if (change/'rollback-rehearsal').exists() and not rehearsal.exists():
        rehearsal.parent.mkdir(parents=True,exist_ok=True)
        rehearsal.write_bytes((change/'original-runtime'/home).read_bytes())
    (change/'runtime-manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2))
    print(json.dumps({'staged':True,'originalBackupPreserved':True,'files':len(m['files'])}))


def apply(change, rollback=False, rehearsal=False):
    m = read_manifest(change)
    live = change / 'rollback-rehearsal' if rehearsal else base.LIVE / 'src'
    if not rehearsal:
        base.idle()
        if sha((base.LIVE/'config.json').read_bytes()) != m['config_hash']:
            raise RuntimeError('runtime_config_changed')
        for name, checksum in m['protected'].items():
            if sha((live/name).read_bytes()) != checksum:raise RuntimeError('unrelated_runtime_changed:'+name)
    for f in m['files']:
        p=live/f['relative'];expected=f['modified'] if rollback else f['original']
        if p.exists():
            if sha(p.read_bytes())!=expected:raise RuntimeError('target_changed:'+f['relative'])
        elif rollback or f['existed']:raise RuntimeError('target_missing:'+f['relative'])
    for f in m['files']:
        p=live/f['relative'];p.parent.mkdir(parents=True,exist_ok=True)
        if rollback and not f['existed']:p.unlink();continue
        content=(change/('original-runtime' if rollback else 'modified-runtime')/f['relative']).read_bytes()
        temp=p.with_name(p.name+'.radar-tmp');temp.write_bytes(content);temp.chmod(0o600);temp.replace(p)
    if not rehearsal:base.idle()
    health = None if rehearsal else base.restart()
    for f in m['files']:
        p=live/f['relative']
        if rollback and not f['existed']:assert not p.exists()
        else:assert sha(p.read_bytes())==f['original' if rollback else 'modified']
    if not rehearsal:
        assert sha((base.LIVE/'config.json').read_bytes())==m['config_hash']
        for name,checksum in m['protected'].items():assert sha((live/name).read_bytes())==checksum
    print(json.dumps({'rollback' if rollback else 'deployed':True,'rehearsal':rehearsal,'files':len(m['files']),'health':health and health['status'],'unrelatedCodeAndConfigPreserved':True}))


def rehearse(change):
    m=read_manifest(change);folder=change/'rollback-rehearsal'
    if folder.exists():
        for f in m['files']:
            p=folder/f['relative']
            if f['existed']:
                if not p.is_file() or sha(p.read_bytes())!=f['original']:raise RuntimeError('rehearsal_state_changed')
            elif p.exists():raise RuntimeError('rehearsal_state_changed')
    for f in m['files']:
        if f['existed']:
            p=folder/f['relative'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((change/'original-runtime'/f['relative']).read_bytes())
    apply(change,rehearsal=True)
    apply(change,rollback=True,rehearsal=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','stage','validate','rehearse','deploy','rollback']);parser.add_argument('--change',type=Path,required=True);args=parser.parse_args();change=args.change.resolve()
    if args.action=='prepare':prepare(change)
    elif args.action=='stage':stage(change)
    elif args.action=='validate':validate(change)
    elif args.action=='rehearse':rehearse(change)
    else:apply(change,rollback=args.action=='rollback')
