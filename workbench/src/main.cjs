'use strict';
const {app,BrowserWindow,WebContentsView,ipcMain,dialog,session,Menu}=require('electron');
const path=require('node:path'),fs=require('node:fs');
const {Store}=require('./store.cjs');const {PLATFORMS,platform,allowedURL,classify,isLoginLanding,READ_EVIDENCE,OPEN_LOGIN,OPEN_QR}=require('./platforms.cjs');
const {MetadataRefresher}=require('./metadata.cjs');
const {readHomeModel}=require('./home-model.cjs');
const {registerConversation}=require('./conversation.cjs');
const {WebsitePane}=require('./website.cjs');
const {TaskPane,readTaskConfig,TASK_PARTITION,allowedTaskURL}=require('./tasks.cjs');
const {FolderPane}=require('./folder.cjs');
const {ContentService}=require('./content/service.cjs');
const {ProjectPane}=require('./content/project-pane.cjs');
let contentService;
const {BarService}=require('./bar.cjs');
const {AnalyticsService}=require('./analytics.cjs');
let analyticsService;
let websitePane,taskPane,libraryPane,projectPane;
const stateRoot=process.env.CONTENTBASE_WORKBENCH_STATE||process.env.STUDIO_STATE_DIR||path.join(app.getPath('appData'),'erc-studio');
const qaOrigin=process.env.STUDIO_QA==='1'?process.env.STUDIO_QA_ORIGIN:undefined;
app.setName('ERC Studio');app.setPath('userData',stateRoot);app.enableSandbox();
if(!app.requestSingleInstanceLock())app.quit();
let win,store,metadataRefresh,active=null;const views=new Map(),currentProof=new Set(),popupWindows=new Set();
const uiFile=path.join(__dirname,'../web/index.html');
const {registerWorkflow}=require('./workflow/service.cjs');
function send(channel,value){if(win&&!win.isDestroyed())win.webContents.send(channel,value);}
function list(){const data=store.list();for(const a of data.accounts){a.cachedStatus=a.status;if(!currentProof.has(a.id)&&a.status==='authorized'){a.status='unverified';a.statusReason='上次已登录，待当前页面复核';}}
 return {...data,platforms:PLATFORMS.map(({id,name,short,color,url})=>({id,name,short,color,url})),active,metadataRefresh:metadataRefresh?.state()||{phase:'idle'}};}
function changed(){send('studio:changed',list());}
function trusted(event){if(event.sender!==win.webContents||event.senderFrame!==win.webContents.mainFrame||!event.senderFrame.url.startsWith('file://'))throw new Error('ipc_sender_denied');}
function bind(name,fn){ipcMain.handle(name,async(e,...args)=>{trusted(e);return fn(...args);});}
function accountBrowserEvent(id,extra={}){const view=views.get(id);if(!view)return;let hostname='';try{hostname=new URL(view.webContents.getURL()).hostname;}catch{}
 if(active===id)send('studio:browser',{id,hostname,loading:view.webContents.isLoading(),...extra});}
async function observe(id){const v=views.get(id);if(!v||v.webContents.isDestroyed()||v.webContents.isLoading())return;
 try{const chunks=[];for(const frame of v.webContents.mainFrame.framesInSubtree){try{chunks.push(await frame.executeJavaScript(READ_EVIDENCE));}catch{}}
  const root=chunks[0]||{},ev={url:root.url||v.webContents.getURL(),text:chunks.map(x=>x.text||'').join('\n'),qr:chunks.some(x=>x.qr),loginButton:chunks.some(x=>x.loginButton),challenge:chunks.some(x=>x.challenge)};
  const account=store.get(id),result=classify(account.platform,ev);store.observe(id,result);currentProof.add(id);changed();accountBrowserEvent(id,{status:result.status,reason:result.reason});
  const landing=isLoginLanding(account.platform,v.webContents.getURL());
  if(landing&&result.status==='login_required'&&!v.loginEntryOpened){const entry=await v.webContents.executeJavaScript(OPEN_LOGIN);if(entry?.href&&allowedURL(entry.href,account.platform)){let target=entry.href;const link=new URL(target);const following=link.searchParams.get('followUrl');if(account.platform==='kuaishou'&&link.pathname==='/rest/infra/logout'&&following&&allowedURL(following,account.platform))target=following;v.loginEntryOpened=true;v.webContents.loadURL(target).catch(()=>{});}else v.loginEntryOpened=!!entry?.clicked;}
 }catch{store.observe(id,{status:'unknown',reason:'页面暂时未能核验'});changed();}
}
function guard(wc,a){
 wc.setWindowOpenHandler(({url})=>{if(!allowedURL(url,a.platform))return {action:'deny'};
  // Some creators open passport in a new tab. Keep login in this account's
  // native pane instead of abandoning the matrix in a separate window.
  if(/passport|\/login(?:[/?#]|$)/i.test(url)){wc.loadURL(url).catch(()=>{});return {action:'deny'};}
  return {action:'allow',overrideBrowserWindowOptions:{width:1000,height:740,parent:win,webPreferences:{session:wc.session,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}}};});
 wc.on('did-create-window',child=>{popupWindows.add(child);guard(child.webContents,a);child.on('closed',()=>{popupWindows.delete(child);observe(a.id);});});
 wc.on('will-navigate',(e,url)=>{if(!allowedURL(url,a.platform,qaOrigin)){e.preventDefault();accountBrowserEvent(a.id,{reason:'该链接不属于当前平台，请在系统浏览器中处理'});}});
 wc.on('will-redirect',(e,url)=>{if(!allowedURL(url,a.platform,qaOrigin))e.preventDefault();});
 wc.on('did-start-loading',()=>accountBrowserEvent(a.id));
 wc.on('did-finish-load',()=>{accountBrowserEvent(a.id);for(const delay of [1200,3500,7000])setTimeout(()=>observe(a.id),delay);});
 wc.on('did-fail-load',(_e,code)=>{if(code===-3)return;store.observe(a.id,{status:'unknown',reason:`页面加载异常（${code}），原登录空间已保留`});currentProof.add(a.id);changed();accountBrowserEvent(a.id,{reason:'页面加载失败，可刷新重试'});});
 wc.on('render-process-gone',()=>{store.observe(a.id,{status:'unknown',reason:'页面进程已退出，原登录空间已保留'});changed();});
}
function makeView(a){const ses=session.fromPartition(a.partition,{cache:true});
 // Same Chromium persistent-storage principle as data scientist, a new space per account.
 ses.setPermissionRequestHandler((_wc,_permission,callback)=>callback(false));ses.setPermissionCheckHandler(()=>false);
 const v=new WebContentsView({webPreferences:{session:ses,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,allowRunningInsecureContent:false}});
 v.webContents.setUserAgent(v.webContents.getUserAgent().replace(/\sElectron\/\S+/g,'').replace(/\serc-studio\/\S+/g,''));
 guard(v.webContents,a);views.set(a.id,v);return v;
}
function closeView(hideContent=true){if(hideContent)send('studio:content-hide',true);projectPane?.hide();taskPane?.hide();libraryPane?.hide();websitePane?.hide();if(active&&views.has(active))win.contentView.removeChildView(views.get(active));active=null;win.setTitle('ERC Studio · 账号管理');changed();}
function setBounds(b){if(!active)return;if(!b||!['x','y','width','height'].every(k=>Number.isFinite(b[k])))throw new Error('bounds_invalid');const size=win.getContentSize();
 const x=Math.max(0,Math.min(Math.round(b.x),size[0])),y=Math.max(0,Math.min(Math.round(b.y),size[1]));
 views.get(active).setBounds({x,y,width:Math.max(1,Math.min(Math.round(b.width),size[0]-x)),height:Math.max(1,Math.min(Math.round(b.height),size[1]-y))});}
async function open(id){const a=store.get(id);projectPane?.hide();taskPane?.hide();libraryPane?.hide();websitePane?.hide();if(active&&active!==id)win.contentView.removeChildView(views.get(active));const v=views.get(id)||makeView(a);active=id;win.contentView.addChildView(v);v.setBounds({x:226,y:140,width:Math.max(1,win.getContentSize()[0]-226),height:Math.max(1,win.getContentSize()[1]-140)});
 changed();if(!v.webContents.getURL())v.webContents.loadURL(platform(a.platform).url).catch(()=>{});else observe(id);return {id,platform:a.platform};}
function setup(){
 store=new Store(stateRoot);
 // Default isolated browser QA stays free of account-service side effects.
 const qaRead=process.env.STUDIO_QA==='1'&&process.env.STUDIO_QA_REAL_METADATA!=='1'?async()=>({ok:true,data:[]}):undefined;
 metadataRefresh=new MetadataRefresher(store,{...(qaRead?{read:qaRead}:{}),onChange:changed});
 win=new BrowserWindow({width:1512,height:920,minWidth:980,minHeight:620,title:'ERC Studio · 账号管理',backgroundColor:'#ffffff',webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});
 registerWorkflow({win,ipcMain,stateRoot,uiFile});
 websitePane=new WebsitePane({win,WebContentsView,session,onEvent:v=>send('studio:website',v)});
 try{contentService=new ContentService(stateRoot,{onChange:v=>send('studio:content-state',v)});}catch{contentService={refresh:async()=>({phase:'error',message:'内容配置或本机任务状态有误，原文件已保留',topics:[],projects:[],jobs:[],forms:[],accounts:[],platforms:{},actor:{canWrite:false,canApprove:false}}),dispose:()=>{}};}
 projectPane=new ProjectPane({win,WebContentsView,session,config:contentService.config,onEvent:v=>send('studio:content-project-state',v)});
 bind('studio:content-project-open',()=>{closeView(false);return projectPane.open();});bind('studio:content-project-hide',()=>{projectPane.hide();return true;});bind('studio:content-project-bounds',v=>projectPane.bounds(v));bind('studio:content-project-command',v=>projectPane.command(v));
 analyticsService=new AnalyticsService(stateRoot,{onChange:v=>send('studio:analytics',v)});
 bind('studio:analytics-open',days=>analyticsService.open(days));bind('studio:analytics-read',days=>analyticsService.read(days));bind('studio:analytics-sync',days=>analyticsService.sync(days));
 let taskConfig;try{taskConfig=readTaskConfig(stateRoot);}catch{taskConfig={error:'task_config_invalid'};}
 taskPane=new TaskPane({win,WebContentsView,session,config:taskConfig,onEvent:v=>send('studio:tasks',v)});
 Menu.setApplicationMenu(Menu.buildFromTemplate([{label:'ERC Studio',submenu:[{role:'about'},{type:'separator'},{role:'hide'},{role:'quit'}]},{label:'编辑',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},{label:'窗口',submenu:[{role:'minimize'},{role:'zoom'}]}]));
 win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.on('will-navigate',e=>e.preventDefault());
 bind('studio:website-open',()=>{closeView();return websitePane.open();});bind('studio:website-bounds',v=>websitePane.bounds(v));bind('studio:website-command',v=>websitePane.command(v));bind('studio:website-state',()=>websitePane.state());
 bind('studio:tasks-open',()=>{closeView();return taskPane.open();});bind('studio:tasks-bounds',v=>taskPane.bounds(v));bind('studio:tasks-command',v=>taskPane.command(v));bind('studio:tasks-state',()=>taskPane.state());
 const barService=new BarService(stateRoot);
 libraryPane=new FolderPane({win,WebContentsView,session,config:barService.config?{url:barService.config.folderURL}:barService.configError?{error:true}:null,onEvent:v=>send('studio:bar-library',v)});
 const openLibrary=()=>{closeView();return libraryPane.open();};
 bind('studio:bar-library-open',openLibrary);bind('studio:bar-library-bounds',v=>libraryPane.bounds(v));bind('studio:bar-library-command',v=>libraryPane.command(v));
 bind('studio:bar-read',()=>barService.refresh());
 bind('studio:bar-source',async input=>{if(input?.kind==='folder')return openLibrary();const url=await barService.sourceURL(input),ses=session.fromPartition(TASK_PARTITION);const child=new BrowserWindow({parent:win,width:1200,height:850,title:'活动运营 · 飞书原表',webPreferences:{session:ses,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});child.webContents.setWindowOpenHandler(()=>({action:'deny'}));const target=new URL(url).origin+'/base/'+barService.config.baseToken;for(const name of ['will-navigate','will-redirect'])child.webContents.on(name,(e,u,_inPlace,isMainFrame)=>{if(isMainFrame!==false&&!allowedTaskURL(u,target))e.preventDefault();});await child.loadURL(url);return true;});
 const contentActions=new Set(['createTopic','editTopic','decideTopic','createProject','saveDraft','advance','addPublication','syncMetrics','saveReview','generate','verify','pendingResult','resumeJob']);
 bind('studio:content-open',()=>{closeView(false);win.setTitle('ERC Studio · 内容');return contentService.open?.()||contentService.refresh();});bind('studio:content-read',()=>contentService.refresh());bind('studio:content-query',input=>contentService.query(input));
 bind('studio:content-action',(method,input)=>{if(!contentActions.has(method)||typeof contentService[method]!=='function'||!input||typeof input!=='object'||Array.isArray(input))throw new Error('content_action_invalid');return contentService[method](input);});
 bind('studio:content-document',async id=>{const url=contentService.referencedURL(id),ses=session.fromPartition(TASK_PARTITION);const child=new BrowserWindow({parent:win,width:1200,height:850,title:'ERC Studio · 飞书文档',webPreferences:{session:ses,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});child.webContents.setWindowOpenHandler(()=>({action:'deny'}));for(const name of ['will-navigate','will-redirect'])child.webContents.on(name,(e,target,_inPlace,isMainFrame)=>{if(isMainFrame!==false&&!allowedTaskURL(target,contentService.config.baseURL))e.preventDefault();});await child.loadURL(url);return true;});
 bind('studio:content-source',async input=>{const url=await contentService.sourceURL(input),ses=session.fromPartition(TASK_PARTITION);const child=new BrowserWindow({parent:win,width:1200,height:850,title:'ERC Studio · 飞书文档',webPreferences:{session:ses,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});child.webContents.setWindowOpenHandler(()=>({action:'deny'}));for(const name of ['will-navigate','will-redirect'])child.webContents.on(name,(e,target,_inPlace,isMainFrame)=>{if(isMainFrame!==false&&!allowedTaskURL(target,contentService.config.baseURL))e.preventDefault();});await child.loadURL(url);return true;});
 bind('studio:home-open',()=>{closeView();win.setTitle('ERC Studio · 主页');return true;});
 registerConversation({win,ipcMain,...require('electron'),stateRoot,uiFile});
 bind('studio:home-model',()=>readHomeModel());
 bind('studio:list',()=>list());bind('studio:add',v=>{const a=store.add(v);changed();return a;});bind('studio:update',(id,v)=>{const a=store.update(id,v);changed();return a;});bind('studio:group',n=>{store.addGroup(n);changed();return list();});
 bind('studio:refresh',async()=>{await metadataRefresh.refresh();return list();});
 bind('studio:assign',(ids,group)=>{store.assignGroup(ids,group);changed();return list();});
 bind('studio:open',open);bind('studio:close',closeView);bind('studio:bounds',setBounds);bind('studio:command',async command=>{if(!active)throw new Error('browser_missing');const wc=views.get(active).webContents;
  if(command==='reload')wc.reload();else if(command==='home')await wc.loadURL(platform(store.get(active).platform).url);else if(command==='back'){if(wc.navigationHistory.canGoBack())wc.navigationHistory.goBack();}else if(command==='check')await observe(active);else if(command==='qr'){for(const frame of wc.mainFrame.framesInSubtree){try{if(await frame.executeJavaScript(OPEN_QR))break;}catch{}}setTimeout(()=>observe(active),1200);}else throw new Error('command_invalid');return true;});
 win.on('resize',()=>send('studio:browser',{resize:true}));win.webContents.once('did-finish-load',()=>{contentService.open?.();analyticsService.open(14);});win.loadFile(uiFile);if(process.env.STUDIO_QA!=='1')win.maximize();
 const timer=setInterval(()=>{if(active)observe(active);},12000);timer.unref();
 win.on('close',()=>{projectPane.close();taskPane.close();libraryPane.close();websitePane.close();});win.on('closed',()=>{clearInterval(timer);for(const v of views.values())if(!v.webContents.isDestroyed())v.webContents.close();win=null;});
 if(process.env.STUDIO_QA==='1'){global.__studioQA={store,views,open,observe,closeView,list,win,makeView,metadataRefresh,websitePane,taskPane,contentService,analyticsService};}
}
app.on('second-instance',()=>{if(win){if(win.isMinimized())win.restore();win.focus();metadataRefresh.refresh();}});
app.whenReady().then(setup).catch(error=>{dialog.showErrorBox('ERC Studio启动失败',String(error.message));app.quit();});
app.on('window-all-closed',()=>app.quit());
app.on('before-quit',()=>{contentService?.dispose();metadataRefresh?.cancel();for(const v of views.values())if(!v.webContents.isDestroyed())v.webContents.session.flushStorageData();});
