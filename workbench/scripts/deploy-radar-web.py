#!/usr/bin/env python3
"""Add the blogger topic radar page to the gateway without touching other modules."""
import argparse, hashlib, json, os, shutil, sqlite3, subprocess, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = Path.home() / 'Library/Application Support/ContentBaseStudioGateway'
LABEL = 'com.contentbase.studio-conversation-gateway'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('radar_anchor_drift:' + old[:60])
    return text.replace(old, new)


def server_hooks(text):
    text = once(text, 'from analytics_gateway import AnalyticsGateway',
                'from analytics_gateway import AnalyticsGateway\nfrom radar_snapshot import RadarRefresher,RadarRefreshError')
    text = once(text, "'/v1/workspace/snapshot','/v1/workflows/snapshot') and not re.fullmatch",
                "'/v1/workspace/snapshot','/v1/workflows/snapshot','/v1/radar/snapshot') and not re.fullmatch")
    route = """            if method=='GET' and path=='/v1/radar/snapshot':
                if set(query)-{'refresh'} or query.get('refresh',['0'])[0] not in ('0','1'):raise Problem(400,'invalid_request')
                if not hasattr(service,'radar'):service.radar=RadarRefresher(service.config)
                if query.get('refresh',['0'])[0]=='1':
                    try:self.reply(200,service.radar.refresh())
                    except RadarRefreshError as error:raise Problem(503,str(error))
                    return
                self.reply(200,service.radar.cached());return
            if method=='GET' and path=='/v1/me':"""
    text = once(text, "            if method=='GET' and path=='/v1/me':", route)
    return text


def index_hooks(text):
    text = once(text, '<link rel="stylesheet" href="bar.css">',
                '<link rel="stylesheet" href="bar.css"><link rel="stylesheet" href="radar.css">')
    text = once(text, '<script src="bar-ui.js"></script>',
                '<script src="bar-ui.js"></script><script src="radar-ui.js"></script>')
    return text


def app_hooks(text):
    text = once(text, "['home','主页'],['tasks','任务'],['content','内容'],['account','账号']",
                "['home','主页'],['tasks','任务'],['content','内容'],['radar','选题'],['account','账号']")
    anchor = "else if(name==='website'){b.title='展开官网与数据中心';b.onclick=()=>window.toggleStudioWebsiteNav();}"
    text = once(text, anchor, anchor + "else if(name==='radar'){b.disabled=true;b.title='选题雷达需在浏览器版使用';}")
    return text


def adapter_hooks(text):
    anchor = "  analytics:{...analytics,onState:fn=>on('analytics-state',fn)},"
    return once(text, anchor, anchor + "\n  radar:{open:async()=>request('/v1/radar/snapshot'),read:async()=>request('/v1/radar/snapshot'),refresh:async()=>request('/v1/radar/snapshot?refresh=1')},")


def idle():
    db = sqlite3.connect((LIVE / 'state/chat.sqlite3').as_uri() + '?mode=ro', uri=True)
    try:
        n = db.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued','cancel_requested')").fetchone()[0]
    finally:
        db.close()
    active = []
    for p in (LIVE / 'state/production-runs').glob('*/status.json'):
        try:
            v = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if v.get('state') not in ('succeeded', 'failed', 'execution_unknown'):
            active.append(v.get('id'))
    if n or active:
        raise RuntimeError('active_gateway_or_production_jobs_wait_before_restart')


def fingerprint(root):
    return {str(p.relative_to(root)): sha(p.read_bytes()) for p in root.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def prepare(change):
    idle()
    change.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (change / 'manifest.json').exists():
        raise RuntimeError('radar_already_prepared')
    src = LIVE / 'src'
    patched = {
        'server.py': server_hooks((src / 'server.py').read_text()).encode(),
        'web/browser-adapter.js': adapter_hooks((src / 'web/browser-adapter.js').read_text()).encode(),
        'office-app/web/index.html': index_hooks((src / 'office-app/web/index.html').read_text()).encode(),
        'office-app/web/app.js': app_hooks((src / 'office-app/web/app.js').read_text()).encode(),
    }
    fresh = {
        'radar_snapshot.py': (ROOT / 'backend/radar_snapshot.py').read_bytes(),
        'office-app/web/radar-ui.js': (ROOT / 'web/radar-ui.js').read_bytes(),
        'office-app/web/radar.css': (ROOT / 'web/radar.css').read_bytes(),
    }
    before = fingerprint(src)
    items = []
    for name, data in {**patched, **fresh}.items():
        p = src / name
        old = p.read_bytes() if p.exists() else b''
        for folder, value in (('original', old), ('modified', data)):
            q = change / folder / name
            q.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            q.write_bytes(value)
            q.chmod(0o600)
        items.append({'relative': name, 'existed': bool(old), 'original': sha(old), 'modified': sha(data)})
    protected = {k: v for k, v in before.items() if k not in patched and k not in fresh}
    manifest = {'runtime': str(LIVE), 'label': LABEL, 'files': items, 'protected': protected}
    (change / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({'preparedFiles': len(items), 'preservedRuntimeFiles': len(protected)}))


def validate(change):
    m = json.loads((change / 'manifest.json').read_text())
    for item in m['files']:
        p = change / 'modified' / item['relative']
        assert sha(p.read_bytes()) == item['modified']
        if p.suffix == '.py':
            compile(p.read_text(), str(p), 'exec')
        elif p.suffix == '.js':
            subprocess.run(['/usr/local/bin/node', '--check', str(p)], check=True, capture_output=True)
    print(json.dumps({'validated': True, 'files': len(m['files'])}))


def restart():
    subprocess.run(['/bin/launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/{LABEL}'], check=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(50):
        try:
            with opener.open('http://127.0.0.1:8844/health', timeout=2) as response:
                value = json.load(response)
            if value.get('worker_alive'):
                return value
        except Exception:
            pass
        time.sleep(.3)
    raise RuntimeError('radar_health_failed')


def apply(change, rollback=False):
    idle()
    m = json.loads((change / 'manifest.json').read_text())
    src = Path(m['runtime']) / 'src'
    for name, h in m['protected'].items():
        if sha((src / name).read_bytes()) != h:
            raise RuntimeError('unrelated_runtime_changed:' + name)
    for item in m['files']:
        p = src / item['relative']
        expected = item['modified'] if rollback else item['original']
        if item['existed']:
            if sha(p.read_bytes()) != expected:
                raise RuntimeError('target_changed_since_prepare:' + item['relative'])
        elif p.exists():
            raise RuntimeError('target_appeared_since_prepare:' + item['relative'])
    for item in m['files']:
        p = src / item['relative']
        if rollback and not item['existed']:
            p.unlink(missing_ok=True)
            continue
        data = (change / ('original' if rollback else 'modified') / item['relative']).read_bytes()
        tmp = p.with_suffix(p.suffix + '.radar-tmp')
        tmp.write_bytes(data)
        tmp.chmod(0o600)
        tmp.replace(p)
    idle()
    health = restart()
    for name, h in m['protected'].items():
        assert sha((src / name).read_bytes()) == h
    print(json.dumps({'rolledBack' if rollback else 'deployed': True, 'files': len(m['files']),
                      'health': health['status'], 'unrelatedRuntimePreserved': True}))


def rehearse(change):
    m = json.loads((change / 'manifest.json').read_text())
    target = change / 'rollback-rehearsal'
    target.mkdir(mode=0o700)
    for item in m['files']:
        p = target / item['relative']
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((change / 'modified' / item['relative']).read_bytes())
        if item['existed']:
            p.write_bytes((change / 'original' / item['relative']).read_bytes())
            assert sha(p.read_bytes()) == item['original']
        else:
            p.unlink()
            assert not p.exists()
    print(json.dumps({'rollbackRehearsal': True, 'files': len(m['files'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'validate', 'rehearse', 'deploy', 'rollback'])
    parser.add_argument('--change', type=Path, required=True)
    args = parser.parse_args()
    change = args.change.resolve()
    if args.action == 'prepare':
        prepare(change)
    elif args.action == 'validate':
        validate(change)
    elif args.action == 'rehearse':
        rehearse(change)
    else:
        apply(change, args.action == 'rollback')
