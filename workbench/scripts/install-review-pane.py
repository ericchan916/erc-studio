#!/usr/bin/env python3
"""Narrow web-only review embed; preserve live renderer changes and login state."""
import argparse,hashlib,json,os,pathlib,sqlite3,subprocess,time,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
LIVE=pathlib.Path.home()/'Library/Application Support/ContentBaseStudioGateway'
LABEL='com.contentbase.studio-conversation-gateway'
def sha(value):return hashlib.sha256(value).hexdigest()
def once(text,old,new):
    if text.count(old)!=1:raise ValueError('review_embed_anchor_changed:'+old[:60])
    return text.replace(old,new)
def renderer(text):
    if 'content-review-mount' in text:raise ValueError('review_embed_already_present')
    text=once(text," root.querySelector('.content-header').after(projectRoot);", """ root.querySelector('.content-header').after(projectRoot);
 const reviewAPI=window.studio.content.review,reviewRoot=node('section','content-native-pane');reviewRoot.id='content-review-pane';reviewRoot.hidden=true;
 reviewRoot.innerHTML='<div id="content-review-mount" class="content-project-mount"></div>';projectRoot.after(reviewRoot);
 if(reviewAPI)root.querySelector('[data-content-tab="reviews"]').textContent='数据复盘';
 const reviewEmbedded=()=>view==='reviews'&&!!reviewAPI,embeddedView=()=>view==='projects'||reviewEmbedded();""")
    text=once(text," window.addEventListener('resize',()=>safely(()=>nativeProjects.resize()));", """ let reviewErrorKey='';
 const nativeReviews=reviewAPI?window.StudioContentProjectController.create({api:reviewAPI,desired:()=>!root.hidden&&reviewEmbedded()&&!document.body.classList.contains('studio-auth-locked'),mount:()=>C('content-review-mount').getBoundingClientRect(),onState:state=>{reviewRoot.dataset.phase=state.phase;reviewRoot.setAttribute('aria-label',state.message||'飞书内容数据复盘');const key=['error','blocked','unconfigured'].includes(state.phase)?state.phase+':'+state.message:'';if(key&&key!==reviewErrorKey)toast(state.message||'复盘原表暂未打开');reviewErrorKey=key;},onError:()=>{reviewRoot.dataset.phase='error';toast('复盘原表暂未打开；请重新进入，原登录状态保留');}}):null;
 window.addEventListener('resize',()=>{safely(()=>nativeProjects.resize());safely(()=>nativeReviews?.resize());});
 if(typeof ResizeObserver==='function')new ResizeObserver(()=>safely(()=>nativeReviews?.resize())).observe(C('content-review-mount'));""")
    text=once(text,"if(document.body.classList.contains('studio-auth-locked'))safely(()=>nativeProjects.hide());", "if(document.body.classList.contains('studio-auth-locked')){safely(()=>nativeProjects.hide());safely(()=>nativeReviews?.hide());}")
    text=once(text,"function render(){const embedded=view==='projects';projectRoot.hidden=!embedded;", "function render(){const review=reviewEmbedded(),embedded=embeddedView();projectRoot.hidden=view!=='projects';reviewRoot.hidden=!review;")
    text=once(text,"if(embedded){safely(()=>nativeProjects.show());return;}safely(()=>nativeProjects.hide());", "if(review){C('content-actor').hidden=C('content-refresh').hidden=false;C('content-actor').textContent='飞书原表 · 本人身份与权限';C('content-refresh').disabled=false;safely(()=>nativeProjects.hide());safely(()=>nativeReviews.show());return;}safely(()=>nativeReviews?.hide());if(embedded){safely(()=>nativeProjects.show());return;}safely(()=>nativeProjects.hide());")
    text=once(text,"root.hidden=true;safely(()=>nativeProjects.hide());document.querySelector('.topbar').hidden=false;", "root.hidden=true;safely(()=>nativeProjects.hide());safely(()=>nativeReviews?.hide());document.querySelector('.topbar').hidden=false;")
    text=once(text,"if(view==='projects')await window.studio.close();", "if(embeddedView())await window.studio.close();")
    text=once(text,"if(view!=='projects')await read(true);", "if(!embeddedView())await read(true);")
    text=once(text,"C('content-refresh').onclick=()=>read();", "C('content-refresh').onclick=()=>reviewEmbedded()?safely(()=>nativeReviews.command('reload')):read();")
    return text

def idle():
    p=LIVE/'state/chat.sqlite3'
    with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
        if db.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued','cancel_requested')").fetchone()[0]:raise ValueError('gateway_jobs_in_flight')
def prepare(change,url):
    from urllib.parse import urlsplit,parse_qs
    u=urlsplit(url)
    if u.scheme!='https' or not (u.hostname or '').endswith('.feishu.cn') or u.username or u.password or u.fragment or not u.path.startswith('/base/'):raise ValueError('review_target_invalid')
    if not parse_qs(u.query).get('table',[''])[0].startswith('tbl'):raise ValueError('review_table_required')
    idle();change.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (change/'runtime-manifest.json').exists():raise ValueError('already_prepared')
    root=LIVE/'src';config=json.loads((LIVE/'config.json').read_text());config['workspace_links']['reviews']=url
    values={root/'server.py':(ROOT/'backend/server.py').read_bytes(),root/'web/browser-adapter.js':(ROOT/'backend/web/browser-adapter.js').read_bytes(),root/'office-app/web/content-ui.js':renderer((root/'office-app/web/content-ui.js').read_text()).encode(),LIVE/'config.json':(json.dumps(config,ensure_ascii=False,indent=2)+'\n').encode()}
    for n in ('server.py','web/browser-adapter.js'):
        if (root/n).read_bytes()!=(change/'source-original/backend'/n).read_bytes():raise ValueError('live_backend_drift:'+n)
    items=[]
    for i,(target,data) in enumerate(values.items()):
        old=target.read_bytes()
        for label,value in [('original',old),('modified',data)]:
            p=change/label/str(i);p.parent.mkdir(exist_ok=True,mode=0o700);p.write_bytes(value);p.chmod(0o600)
        items.append({'path':str(target),'original':sha(old),'modified':sha(data),'slot':str(i)})
    protected={str(p):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p not in values}
    m={'files':items,'protected':protected};(change/'runtime-manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2));print(json.dumps({'prepared':len(items),'protected_files':len(protected)}))
def apply(change,rollback=False):
    idle();m=json.loads((change/'runtime-manifest.json').read_text())
    for p,h in m['protected'].items():assert sha(pathlib.Path(p).read_bytes())==h,p
    for e in m['files']:assert sha(pathlib.Path(e['path']).read_bytes())==e['modified' if rollback else 'original'],e['path']
    for e in m['files']:
        p=pathlib.Path(e['path']);raw=(change/('original' if rollback else 'modified')/e['slot']).read_bytes();assert sha(raw)==e['original' if rollback else 'modified'];temp=p.with_suffix(p.suffix+'.review-tmp');temp.write_bytes(raw);temp.chmod(0o600);temp.replace(p)
    subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],check=True)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with opener.open('http://127.0.0.1:8844/health',timeout=2) as r:value=json.load(r)
            if value.get('worker_alive'):break
        except Exception:pass
        time.sleep(.5)
    else:raise ValueError('gateway_health_failed')
    for e in m['files']:assert sha(pathlib.Path(e['path']).read_bytes())==e['original' if rollback else 'modified']
    for p,h in m['protected'].items():assert sha(pathlib.Path(p).read_bytes())==h,p
    print(json.dumps({'rollback' if rollback else 'deployed':True,'files':len(m['files']),'other_files_preserved':len(m['protected']),'worker_alive':True}))
def rehearse(change):
    m=json.loads((change/'runtime-manifest.json').read_text());dest=change/'rehearsal';dest.mkdir(exist_ok=True)
    for e in m['files']:
        p=dest/e['slot'];p.write_bytes((change/'modified'/e['slot']).read_bytes());p.write_bytes((change/'original'/e['slot']).read_bytes());assert sha(p.read_bytes())==e['original']
    compile((dest/'0').read_text(),'restored-server.py','exec');subprocess.run(['/usr/local/bin/node','--check','--input-type=commonjs'],input=(dest/'2').read_text(),text=True,check=True)
    print(json.dumps({'rollback_rehearsed':True,'restored_files':len(m['files'])}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','deploy','rollback','rehearse']);p.add_argument('--change',type=pathlib.Path,required=True);p.add_argument('--url');a=p.parse_args()
    if a.action=='prepare':prepare(a.change,a.url)
    elif a.action=='rehearse':rehearse(a.change)
    else:apply(a.change,a.action=='rollback')
