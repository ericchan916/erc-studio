#!/usr/bin/env python3
"""Move data review and add two Feishu script folders without rebuilding the gateway."""
import argparse,hashlib,json,os,re,shutil,sqlite3,subprocess,time,urllib.parse,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];LIVE=Path.home()/'Library/Application Support/ContentBaseStudioGateway';LABEL='com.contentbase.studio-conversation-gateway'
def sha(data):return hashlib.sha256(data).hexdigest()
def once(text,old,new):
 if text.count(old)!=1:raise RuntimeError('content_navigation_anchor_drift')
 return text.replace(old,new)
def folder_url(value):
 u=urllib.parse.urlsplit(value)
 if u.scheme!='https' or not (u.hostname or '').endswith('.feishu.cn') or u.username or u.password or u.fragment or not re.fullmatch(r'/drive/folder/[A-Za-z0-9]{8,128}',u.path):raise RuntimeError('content_script_folder_url_invalid')
 return value
def server_hooks(text):
 text=text.replace("{'tasks','content','reviews','bar','barFolder','website','platforms'}","{'tasks','content','videoScripts','articleScripts','reviews','bar','barFolder','website','platforms'}")
 text=text.replace("('tasks','content','reviews','bar','barFolder','website')","('tasks','content','videoScripts','articleScripts','reviews','bar','barFolder','website')")
 text=text.replace("('tasks','content','reviews','bar','barFolder')","('tasks','content','videoScripts','articleScripts','reviews','bar','barFolder')")
 if "'videoScripts'" not in text or "'articleScripts'" not in text:raise RuntimeError('content_navigation_server_hook_failed')
 return text
def content_ui_hooks(text):
 if 'window.openStudioDataReview' in text:return text
 had_notice='content-project-notice' in text
 text=once(text,'<button class="content-tab" data-content-tab="topics">选题池</button><button class="content-tab active" data-content-tab="projects">内容项目</button><button class="content-tab" data-content-tab="production">制作</button><button class="content-tab" data-content-tab="reviews">发布与复盘</button>','<button class="content-tab" data-content-tab="projects">内容项目</button><button class="content-tab" data-content-tab="videoScripts">视频脚本</button><button class="content-tab" data-content-tab="articleScripts">文章脚本</button><button class="content-tab active" data-content-tab="production">制作</button>')
 old=""" const reviewAPI=window.studio.content.review,reviewRoot=node('section','content-native-pane');reviewRoot.id='content-review-pane';reviewRoot.hidden=true;
 reviewRoot.innerHTML='<div id="content-review-mount" class="content-project-mount"></div>';projectRoot.after(reviewRoot);
 if(reviewAPI)root.querySelector('[data-content-tab="reviews"]').textContent='数据复盘';
 const reviewEmbedded=()=>view==='reviews'&&!!reviewAPI,embeddedView=()=>view==='projects'||reviewEmbedded();
 const dialog=node('dialog','content-dialog');dialog.id='content-dialog';document.body.append(dialog);
 let data={topics:[],projects:[],jobs:[],forms:['竖屏视频','横屏视频','公众号文章','X帖子'],stages:[],accounts:[],platforms:{}},view='projects',status='all',selected=null,page=1,busy=false,loading=false,modalSubmit=null,lastError='';let edits=new Map(),reviewEdits=new Map(),editScope='unverified';const editScopes=new Map();"""
 new=""" const videoScriptAPI=window.studio.content.videoScripts,articleScriptAPI=window.studio.content.articleScripts,reviewAPI=window.studio.dataReview;
 const videoScriptRoot=node('section','content-native-pane');videoScriptRoot.id='content-video-script-pane';videoScriptRoot.hidden=true;videoScriptRoot.innerHTML='<div id="content-video-script-mount" class="content-project-mount"></div>';projectRoot.after(videoScriptRoot);
 const articleScriptRoot=node('section','content-native-pane');articleScriptRoot.id='content-article-script-pane';articleScriptRoot.hidden=true;articleScriptRoot.innerHTML='<div id="content-article-script-mount" class="content-project-mount"></div>';videoScriptRoot.after(articleScriptRoot);
 for(const [id,api]of [['videoScripts',videoScriptAPI],['articleScripts',articleScriptAPI]])if(!api)root.querySelector('[data-content-tab="'+id+'"]').hidden=true;
 const reviewRoot=node('section','content-workbench');reviewRoot.id='data-review-workbench';reviewRoot.hidden=true;reviewRoot.innerHTML='<header class="content-header"><div class="content-tabs"><button class="content-tab active">数据复盘</button></div><div class="content-tools"><span class="content-actor">飞书原表 · 本人身份与权限</span><button class="content-button" id="data-review-refresh"><img src="vendor/tabler-content/refresh.svg" alt=""><span>刷新原表</span></button></div></header><section class="content-native-pane"><div id="data-review-mount" class="content-project-mount"></div></section>';document.querySelector('main').append(reviewRoot);
 const embeddedView=()=>['projects','videoScripts','articleScripts'].includes(view);
 const dialog=node('dialog','content-dialog');dialog.id='content-dialog';document.body.append(dialog);
 let data={topics:[],projects:[],jobs:[],forms:['竖屏视频','横屏视频','公众号文章','X帖子'],stages:[],accounts:[],platforms:{}},view='production',status='all',selected=null,page=1,busy=false,loading=false,modalSubmit=null,lastError='';let edits=new Map(),reviewEdits=new Map(),editScope='unverified';const editScopes=new Map();"""
 text=once(text,old,new)
 old=""" let reviewErrorKey='';
 const nativeReviews=reviewAPI?window.StudioContentProjectController.create({api:reviewAPI,desired:()=>!root.hidden&&reviewEmbedded()&&!document.body.classList.contains('studio-auth-locked'),mount:()=>C('content-review-mount').getBoundingClientRect(),onState:state=>{reviewRoot.dataset.phase=state.phase;reviewRoot.setAttribute('aria-label',state.message||'飞书内容数据复盘');const key=['error','blocked','unconfigured'].includes(state.phase)?state.phase+':'+state.message:'';if(key&&key!==reviewErrorKey)toast(state.message||'复盘原表暂未打开');reviewErrorKey=key;},onError:()=>{reviewRoot.dataset.phase='error';toast('复盘原表暂未打开；请重新进入，原登录状态保留');}}):null;
 const productionUI=window.StudioProductionUI.create({host:root,toast});
 window.addEventListener('resize',()=>{safely(()=>nativeProjects.resize());safely(()=>nativeReviews?.resize());});
 if(typeof ResizeObserver==='function')new ResizeObserver(()=>safely(()=>nativeReviews?.resize())).observe(C('content-review-mount'));
 if(typeof ResizeObserver==='function')new ResizeObserver(()=>safely(()=>nativeProjects.resize())).observe(C('content-project-mount'));
 if(typeof MutationObserver==='function')new MutationObserver(()=>{if(document.body.classList.contains('studio-auth-locked')){productionUI.lock();safely(()=>nativeProjects.hide());safely(()=>nativeReviews?.hide());}}).observe(document.body,{attributes:true,attributeFilter:['class']});"""
 new=""" const paneController=(api,pane,mount,label)=>api?window.StudioContentProjectController.create({api,desired:()=>!root.hidden&&view===label&&!document.body.classList.contains('studio-auth-locked'),mount:()=>C(mount).getBoundingClientRect(),onState:state=>{pane.dataset.phase=state.phase;pane.setAttribute('aria-label',state.message||'飞书'+(label==='videoScripts'?'视频脚本':'文章脚本'));},onError:()=>{pane.dataset.phase='error';toast((label==='videoScripts'?'视频':'文章')+'脚本目录暂未打开；原登录状态保留');}}):null;
 const nativeVideoScripts=paneController(videoScriptAPI,videoScriptRoot,'content-video-script-mount','videoScripts'),nativeArticleScripts=paneController(articleScriptAPI,articleScriptRoot,'content-article-script-mount','articleScripts');
 let reviewErrorKey='';const nativeReviews=reviewAPI?window.StudioContentProjectController.create({api:reviewAPI,desired:()=>!reviewRoot.hidden&&!document.body.classList.contains('studio-auth-locked'),mount:()=>C('data-review-mount').getBoundingClientRect(),onState:state=>{reviewRoot.dataset.phase=state.phase;reviewRoot.setAttribute('aria-label',state.message||'飞书内容数据复盘');const key=['error','blocked','unconfigured'].includes(state.phase)?state.phase+':'+state.message:'';if(key&&key!==reviewErrorKey)toast(state.message||'复盘原表暂未打开');reviewErrorKey=key;},onError:()=>{reviewRoot.dataset.phase='error';toast('复盘原表暂未打开；请重新进入，原登录状态保留');}}):null;
 const productionUI=window.StudioProductionUI.create({host:root,toast});
 const panes=[nativeProjects,nativeVideoScripts,nativeArticleScripts,nativeReviews];window.addEventListener('resize',()=>panes.forEach(p=>safely(()=>p?.resize())));
 if(typeof ResizeObserver==='function')for(const [controller,mount]of [[nativeReviews,'data-review-mount'],[nativeProjects,'content-project-mount'],[nativeVideoScripts,'content-video-script-mount'],[nativeArticleScripts,'content-article-script-mount']])if(controller)new ResizeObserver(()=>safely(()=>controller.resize())).observe(C(mount));
 if(typeof MutationObserver==='function')new MutationObserver(()=>{if(document.body.classList.contains('studio-auth-locked')){productionUI.lock();reviewRoot.hidden=true;panes.forEach(p=>safely(()=>p?.hide()));}}).observe(document.body,{attributes:true,attributeFilter:['class']});"""
 text=once(text,old,new)
 old="function render(){const making=view==='production';productionUI.setVisible(making&&!root.hidden);if(making){projectRoot.hidden=true;reviewRoot.hidden=true;root.querySelector('.content-layout').hidden=true;root.querySelector('.content-source-footer').hidden=true;C('content-actor').hidden=C('content-refresh').hidden=false;C('content-actor').textContent='飞书候选 · '+(data.stale?'缓存快照':'只读');C('content-refresh').disabled=loading;root.querySelectorAll('[data-content-tab]').forEach(b=>b.classList.toggle('active',b.dataset.contentTab===view));safely(()=>nativeProjects.hide());safely(()=>nativeReviews?.hide());productionUI.update(data);return;}const review=reviewEmbedded(),embedded=embeddedView();projectRoot.hidden=view!=='projects';reviewRoot.hidden=!review;root.querySelector('.content-layout').hidden=embedded;root.querySelector('.content-source-footer').hidden=embedded;C('content-actor').hidden=C('content-refresh').hidden=embedded;root.querySelectorAll('[data-content-tab]').forEach(b=>b.classList.toggle('active',b.dataset.contentTab===view));if(review){C('content-actor').hidden=C('content-refresh').hidden=false;C('content-actor').textContent='飞书原表 · 本人身份与权限';C('content-refresh').disabled=false;safely(()=>nativeProjects.hide());safely(()=>nativeReviews.show());return;}safely(()=>nativeReviews?.hide());if(embedded){safely(()=>nativeProjects.show());return;}safely(()=>nativeProjects.hide());filters();"
 new="function render(){const making=view==='production',embedded=embeddedView();productionUI.setVisible(making&&!root.hidden);projectRoot.hidden=view!=='projects';videoScriptRoot.hidden=view!=='videoScripts';articleScriptRoot.hidden=view!=='articleScripts';root.querySelector('.content-layout').hidden=making||embedded;root.querySelector('.content-source-footer').hidden=making||embedded;C('content-actor').hidden=C('content-refresh').hidden=embedded;root.querySelectorAll('[data-content-tab]').forEach(b=>b.classList.toggle('active',b.dataset.contentTab===view));const selectedPane={projects:nativeProjects,videoScripts:nativeVideoScripts,articleScripts:nativeArticleScripts}[view];for(const pane of [nativeProjects,nativeVideoScripts,nativeArticleScripts])if(pane!==selectedPane)safely(()=>pane?.hide());if(making){C('content-actor').hidden=C('content-refresh').hidden=false;C('content-actor').textContent='飞书候选 · '+(data.stale?'缓存快照':'只读');C('content-refresh').disabled=loading;productionUI.update(data);return;}if(embedded){safely(()=>selectedPane?.show());return;}filters();"
 text=once(text,old,new)
 old=""" window.hideStudioContent=()=>{if(root.hidden)return;root.hidden=true;productionUI.setVisible(false);safely(()=>nativeProjects.hide());safely(()=>nativeReviews?.hide());document.querySelector('.topbar').hidden=false;};
 window.openStudioContent=()=>safely(async()=>{const started=performance.now();if(embeddedView())await window.studio.close();else{const snapshot=await window.studio.content.open();assignData(snapshot);}loading=false;window.hideStudioWebsite?.();window.hideStudioTasks?.();window.hideStudioAnalytics?.();window.hideStudioHome?.();for(const id of ['account-page','group-page','browser-page'])$(id).hidden=true;root.hidden=false;document.querySelector('.topbar').hidden=true;document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='content'));document.querySelectorAll('.website-subnav .active').forEach(b=>b.classList.remove('active'));render();requestAnimationFrame(()=>requestAnimationFrame(()=>performance.measure('studio-content-first-paint',{start:started,end:performance.now()})));});
 window.studio.content.onHide(()=>window.hideStudioContent());window.studio.content.onState(v=>{assignData(v);if(!root.hidden)render();});
 root.querySelectorAll('[data-content-tab]').forEach(b=>b.onclick=()=>safely(async()=>{view=b.dataset.contentTab;status='all';page=1;selected=null;root.querySelectorAll('[data-content-tab]').forEach(x=>x.classList.toggle('active',x===b));render();if(!embeddedView())await read(true);}));
 for(const id of ['content-search','content-account','content-form','content-owner','content-page-size'])C(id).addEventListener(id==='content-search'?'input':'change',()=>{page=1;render();});C('content-prev').onclick=()=>{page--;render();};C('content-next').onclick=()=>{page++;render();};C('content-refresh').onclick=()=>reviewEmbedded()?safely(()=>nativeReviews.command('reload')):read();"""
 new=""" window.hideStudioDataReview=()=>{if(reviewRoot.hidden)return;reviewRoot.hidden=true;safely(()=>nativeReviews?.hide());document.querySelector('.topbar').hidden=false;};
 window.openStudioDataReview=reviewAPI?()=>safely(async()=>{await window.studio.close();window.hideStudioContent();window.hideStudioWebsite?.();window.hideStudioTasks?.();window.hideStudioAnalytics?.();window.hideStudioHome?.();for(const id of ['account-page','group-page','browser-page'])$(id).hidden=true;reviewRoot.hidden=false;document.querySelector('.topbar').hidden=true;document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='data'));document.querySelectorAll('.website-subnav .active').forEach(b=>b.classList.remove('active'));return nativeReviews.show();}):undefined;
 const dataNav=document.querySelector('[data-nav="data"]');if(dataNav&&window.openStudioDataReview){dataNav.disabled=false;dataNav.title='打开飞书数据复盘原表';dataNav.onclick=()=>window.openStudioDataReview();}C('data-review-refresh').onclick=()=>safely(()=>nativeReviews?.command('reload'));
 window.hideStudioContent=()=>{if(root.hidden)return;root.hidden=true;productionUI.setVisible(false);for(const pane of [nativeProjects,nativeVideoScripts,nativeArticleScripts])safely(()=>pane?.hide());document.querySelector('.topbar').hidden=false;};
 window.openStudioContent=()=>safely(async()=>{const started=performance.now();view='production';status='all';page=1;selected=null;const snapshot=await window.studio.content.open();assignData(snapshot);loading=false;window.hideStudioDataReview();window.hideStudioWebsite?.();window.hideStudioTasks?.();window.hideStudioAnalytics?.();window.hideStudioHome?.();for(const id of ['account-page','group-page','browser-page'])$(id).hidden=true;root.hidden=false;document.querySelector('.topbar').hidden=true;document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='content'));document.querySelectorAll('.website-subnav .active').forEach(b=>b.classList.remove('active'));render();requestAnimationFrame(()=>requestAnimationFrame(()=>performance.measure('studio-content-first-paint',{start:started,end:performance.now()})));});
 window.studio.content.onHide(()=>window.hideStudioContent());window.studio.content.onState(v=>{assignData(v);if(!root.hidden)render();});
 root.querySelectorAll('[data-content-tab]').forEach(b=>b.onclick=()=>safely(async()=>{view=b.dataset.contentTab;status='all';page=1;selected=null;render();if(view==='production')await read(true);}));
 for(const id of ['content-search','content-account','content-form','content-owner','content-page-size'])C(id).addEventListener(id==='content-search'?'input':'change',()=>{page=1;render();});C('content-prev').onclick=()=>{page--;render();};C('content-next').onclick=()=>{page++;render();};C('content-refresh').onclick=()=>read();
 window.addEventListener('DOMContentLoaded',()=>{for(const name of ['openStudioAnalytics','openStudioTasks','openStudioWebsite','openStudioHome','openStudioBar','showTab','openAccount']){const original=window[name];if(typeof original==='function'&&!original.__hidesDataReview){const wrapped=(...args)=>{window.hideStudioDataReview();return original(...args);};wrapped.__hidesDataReview=true;window[name]=wrapped;}}});"""
 text=once(text,old,new)
 if had_notice and 'content-project-notice' not in text:raise RuntimeError('content_navigation_unrelated_project_notice_lost')
 return text
def idle():
 db=sqlite3.connect((LIVE/'state/chat.sqlite3').as_uri()+'?mode=ro',uri=True)
 try:n=db.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued','cancel_requested')").fetchone()[0]
 finally:db.close()
 active=[]
 for p in (LIVE/'state/production-runs').glob('*/status.json'):
  try:v=json.loads(p.read_text())
  except (OSError,ValueError):continue
  if v.get('state') not in ('succeeded','failed','execution_unknown'):active.append(v.get('id'))
 if n or active:raise RuntimeError('active_gateway_or_production_jobs_wait_before_restart')
def fingerprint(root):return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
def prepare(change,video,article):
 idle();video=folder_url(video);article=folder_url(article);change.mkdir(parents=True,exist_ok=True,mode=0o700)
 if (change/'manifest.json').exists():raise RuntimeError('content_navigation_already_prepared')
 src=LIVE/'src';files={'server.py':server_hooks((src/'server.py').read_text()).encode(),'web/browser-adapter.js':(ROOT/'backend/web/browser-adapter.js').read_bytes(),'office-app/web/content-ui.js':content_ui_hooks((src/'office-app/web/content-ui.js').read_text()).encode()};before=fingerprint(src);items=[]
 for name,data in files.items():
  p=src/name;old=p.read_bytes();
  for folder,value in [('original',old),('modified',data)]:q=change/folder/name;q.parent.mkdir(parents=True,exist_ok=True,mode=0o700);q.write_bytes(value);q.chmod(0o600)
  items.append({'relative':name,'original':sha(old),'modified':sha(data)})
 config=LIVE/'config.json';old_config=config.read_bytes();value=json.loads(old_config);links=dict(value.get('workspace_links') or {});links.update(videoScripts=video,articleScripts=article);value['workspace_links']=links;new_config=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
 for folder,data in [('original',old_config),('modified',new_config)]:q=change/folder/'config.json';q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(data);q.chmod(0o600)
 protected={k:v for k,v in before.items() if k not in files};manifest={'runtime':str(LIVE),'label':LABEL,'files':items,'config':{'path':str(config),'original':sha(old_config),'modified':sha(new_config)},'protected':protected,'urls':{'videoScripts':video,'articleScripts':article}}
 (change/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));print(json.dumps({'preparedFiles':len(items),'preservedRuntimeFiles':len(protected),'videoScripts':video,'articleScripts':article}))
def validate(change):
 m=json.loads((change/'manifest.json').read_text())
 for item in m['files']:
  p=change/'modified'/item['relative'];assert sha(p.read_bytes())==item['modified'];compile(p.read_text(),str(p),'exec') if p.suffix=='.py' else subprocess.run(['/usr/local/bin/node','--check',str(p)],check=True,capture_output=True)
 assert sha((change/'modified/config.json').read_bytes())==m['config']['modified'];server=json.loads((change/'modified/config.json').read_text());folder_url(server['workspace_links']['videoScripts']);folder_url(server['workspace_links']['articleScripts']);print(json.dumps({'validated':True,'files':len(m['files']),'privateConfigMode':'0600'}))
def restart():
 subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],check=True);opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(50):
  try:
   with opener.open('http://127.0.0.1:8844/health',timeout=2) as response:value=json.load(response)
   if value.get('worker_alive'):return value
  except Exception:pass
  time.sleep(.3)
 raise RuntimeError('content_navigation_health_failed')
def apply(change,rollback=False):
 idle();m=json.loads((change/'manifest.json').read_text());src=Path(m['runtime'])/'src';config=Path(m['config']['path'])
 for name,h in m['protected'].items():
  if sha((src/name).read_bytes())!=h:raise RuntimeError('unrelated_runtime_changed:'+name)
 expected_config=m['config']['modified'] if rollback else m['config']['original']
 if sha(config.read_bytes())!=expected_config:raise RuntimeError('content_navigation_config_changed')
 for item in m['files']:
  p=src/item['relative'];expected=item['modified'] if rollback else item['original']
  if sha(p.read_bytes())!=expected:raise RuntimeError('target_changed_since_prepare:'+item['relative'])
 for item in m['files']:
  p=src/item['relative'];data=(change/('original' if rollback else 'modified')/item['relative']).read_bytes();tmp=p.with_suffix(p.suffix+'.content-nav-tmp');tmp.write_bytes(data);tmp.chmod(0o600);tmp.replace(p)
 data=(change/('original' if rollback else 'modified')/'config.json').read_bytes();tmp=config.with_suffix('.content-nav-tmp');tmp.write_bytes(data);tmp.chmod(0o600);tmp.replace(config);idle();health=restart()
 for name,h in m['protected'].items():assert sha((src/name).read_bytes())==h
 print(json.dumps({'rolledBack' if rollback else 'deployed':True,'files':len(m['files']),'health':health['status'],'unrelatedRuntimePreserved':True,'configMode':oct(config.stat().st_mode&0o777)}))
def rehearse(change):
 m=json.loads((change/'manifest.json').read_text());target=change/'rollback-rehearsal';target.mkdir(mode=0o700)
 for item in m['files']:
  p=target/item['relative'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((change/'modified'/item['relative']).read_bytes());p.write_bytes((change/'original'/item['relative']).read_bytes());assert sha(p.read_bytes())==item['original']
 q=target/'config.json';q.write_bytes((change/'modified/config.json').read_bytes());q.write_bytes((change/'original/config.json').read_bytes());assert sha(q.read_bytes())==m['config']['original'];print(json.dumps({'rollbackRehearsal':True,'files':len(m['files']),'configRestored':True,'businessDataTouched':False}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','validate','rehearse','deploy','rollback']);p.add_argument('--change',type=Path,required=True);p.add_argument('--video-scripts-url');p.add_argument('--article-scripts-url');a=p.parse_args();change=a.change.resolve()
 if a.action=='prepare':prepare(change,a.video_scripts_url,a.article_scripts_url)
 elif a.action=='validate':validate(change)
 elif a.action=='rehearse':rehearse(change)
 else:apply(change,a.action=='rollback')
