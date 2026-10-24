'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {TaskPane,TASK_PARTITION,taskURL,allowedTaskURL,readTaskConfig}=require('../src/tasks.cjs');
const URL='https://tenant.feishu.cn/base/BaseFixture';
function fixture(config={url:URL}){
 const events={},urls=[],children=[],sent=[];let prefs,partition,destroyed=false;
 const ses={setPermissionRequestHandler(fn){this.request=fn;},setPermissionCheckHandler(fn){this.check=fn;},flushStorageData(){this.flushed=true;}};
 const wc={session:ses,on:(name,fn)=>events[name]=fn,getURL:()=>urls.at(-1)||'',isDestroyed:()=>destroyed,getUserAgent:()=> 'Chrome Electron/44.5.1 content-company-workbench/0.1.0',setUserAgent(value){this.ua=value;},setWindowOpenHandler(fn){this.popup=fn;},loadURL:url=>{urls.push(url);return Promise.resolve();},close(){destroyed=true;},reload(){this.reloaded=true;},navigationHistory:{canGoBack:()=>urls.length>1,goBack:()=>urls.pop()}};
 const win={getContentSize:()=>[1512,920],setTitle(v){this.title=v;},contentView:{children,addChildView:v=>children.push(v),removeChildView:v=>children.splice(children.indexOf(v),1)}};
 const pane=new TaskPane({config,win,session:{fromPartition(v){partition=v;return ses;}},WebContentsView:class{constructor(options){prefs=options.webPreferences;this.webContents=wc;}setBounds(rect){this.rect=rect;}},onEvent:v=>sent.push(v)});
 return {pane,win,wc,events,urls,children,sent,ses,get prefs(){return prefs;},get partition(){return partition;}};
}
test('task target is an explicit tenant Base URL, never credentials or executable schemes',()=>{
 assert.equal(taskURL(URL),URL);assert.equal(taskURL(URL+'?table=TableFixture'),URL+'?table=TableFixture');
 for(const v of ['http://tenant.feishu.cn/base/BaseFixture','https://tenant.feishu.cn.evil.test/base/BaseFixture','https://user:password@tenant.feishu.cn/base/BaseFixture','https://tenant.feishu.cn:8443/base/BaseFixture','https://tenant.feishu.cn/drive/home','javascript:alert(1)','file:///etc/passwd',null])assert.throws(()=>taskURL(v),/task_url_invalid/);
});
test('navigation accepts tenant and official login; unrelated tenant or encoded credentials stay out',()=>{
 for(const v of [URL,'https://tenant.feishu.cn/wiki/DocumentFixture','https://login.feishu.cn/accounts/page/login','https://accounts.feishu.cn/accounts/page/login','https://passport.feishu.cn/suite/passport/page/login/'])assert.ok(allowedTaskURL(v,URL));
 for(const v of ['https://your-other-tenant.feishu.cn/base/Other','https://evil.test','https://passport.feishu.cn.evil.test','https://user@accounts.feishu.cn','feishu://open','file:///etc/passwd'])assert.equal(allowedTaskURL(v,URL),false);
});
test('native pane has its own persistent session, sandbox, no remote preload/Node and user-limited permissions',()=>{
 const f=fixture();f.pane.open();assert.equal(f.partition,TASK_PARTITION);assert.notEqual(TASK_PARTITION,'persist:studio-website');assert.ok(TASK_PARTITION.startsWith('persist:'));
 for(const [key,v] of Object.entries({nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,allowRunningInsecureContent:false}))assert.equal(f.prefs[key],v);assert.equal(f.prefs.preload,undefined);assert.ok(!f.wc.ua.includes('Electron'));
 for(const permission of ['camera','microphone','geolocation','clipboard-read']){let result;f.ses.request(f.wc,permission,v=>result=v);assert.equal(result,false);assert.equal(f.ses.check(f.wc,permission,'https://tenant.feishu.cn'),false);}
 assert.equal(f.ses.check(f.wc,'clipboard-sanitized-write','https://tenant.feishu.cn'),true);assert.equal(f.ses.check(f.wc,'clipboard-sanitized-write','https://evil.test'),false);
});
test('re-entry reuses the same view and URL without reload, hide does not clear storage',()=>{
 const f=fixture();f.pane.open();const original=f.pane.view;f.pane.open();assert.equal(f.children.length,1);assert.deepEqual(f.urls,[URL]);assert.equal(f.pane.view.rect.y,0);f.pane.hide();assert.equal(f.children.length,0);assert.equal(f.win.title,'ERC Studio · 账号管理');f.pane.open();assert.equal(f.pane.view,original);assert.deepEqual(f.urls,[URL]);assert.equal(f.win.title,'ERC Studio · 任务');
});
test('HTTP load is not authentication; state has no full URL, cookie, user identity or fake success',()=>{
 const f=fixture();f.pane.open();f.wc.loadURL('https://accounts.feishu.cn/accounts/page/login?secret=fixture');f.events['did-finish-load']();const state=f.pane.state();assert.equal(state.phase,'ready');assert.equal(state.hostname,'accounts.feishu.cn');assert.equal(state.authorized,undefined);assert.ok(!JSON.stringify(state).includes('YOUR_TEST_SECRET'));assert.match(state.message,/身份与权限以原页面为准/);
});
test('load failures retain view/session; cancellations and subframes are not business failures',async()=>{
 const f=fixture();f.pane.open();f.events['did-finish-load']();f.events['did-fail-load'](null,-3,'','',true);f.events['did-fail-load'](null,-105,'','',false);assert.equal(f.pane.state().phase,'ready');f.events['did-fail-load'](null,-105,'','',true);assert.equal(f.pane.state().phase,'error');f.events['did-stop-loading']();assert.equal(f.pane.state().phase,'error');const original=f.pane.view;await f.pane.command('reload');assert.equal(f.pane.view,original);assert.ok(f.wc.reloaded);
});
test('official popups keep the same user session and navigation guard',()=>{
 const f=fixture();f.pane.open();const response=f.wc.popup({url:'https://accounts.feishu.cn/accounts/page/login'});assert.equal(response.action,'allow');assert.equal(response.overrideBrowserWindowOptions.webPreferences.session,f.ses);assert.equal(response.overrideBrowserWindowOptions.webPreferences.preload,undefined);assert.deepEqual(f.wc.popup({url:'https://evil.test'}),{action:'deny'});
 for(const name of ['will-navigate','will-redirect']){let blocked=false;f.events[name]({preventDefault:()=>blocked=true},'https://evil.test');assert.ok(blocked);}assert.equal(f.pane.state().phase,'blocked');assert.deepEqual(f.urls,[URL]);
});
test('subframe redirects keep normal Chromium isolation and are not mistaken for a main-page escape',()=>{
 const f=fixture();f.pane.open();let blocked=false;f.events['will-redirect']({preventDefault:()=>blocked=true},'https://cdn.fixture.test/auth-frame',false,false);assert.equal(blocked,false);assert.notEqual(f.pane.state().phase,'blocked');assert.equal(f.prefs.webSecurity,true);
});
test('only fixed browser commands; bounds are clamped; quit flushes without logout or deletion',async()=>{
 const f=fixture();await assert.rejects(f.pane.command('reload'),/task_page_missing/);f.pane.open();await assert.rejects(f.pane.command('execute'),/command_invalid/);f.pane.bounds({x:1000,y:0,width:9999,height:9999});assert.equal(f.pane.view.rect.width,512);assert.equal(f.pane.view.rect.height,920);assert.throws(()=>f.pane.bounds({}),/bounds_invalid/);f.pane.close();assert.equal(f.ses.flushed,true);assert.equal(f.pane.view,null);assert.equal(f.children.length,0);
});
test('target config stays in local runtime and absence/malformed config does not require a parallel task DB',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'task-config-'));try{assert.equal(readTaskConfig(dir),null);fs.writeFileSync(path.join(dir,'task-space.json'),JSON.stringify({version:1,url:URL}));assert.deepEqual(readTaskConfig(dir),{url:URL});fs.writeFileSync(path.join(dir,'task-space.json'),'{broken');assert.throws(()=>readTaskConfig(dir),/task_config_invalid/);const f=fixture({error:'task_config_invalid'});f.pane.open();assert.equal(f.children.length,0);assert.equal(f.pane.state().phase,'unconfigured');assert.match(f.pane.state().message,/配置有误/);}finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('task navigation is between Home and Account; native UI keeps account/website integration and registered IPC',()=>{
 const root=path.resolve(__dirname,'..'),app=fs.readFileSync(path.join(root,'web/app.js'),'utf8'),html=fs.readFileSync(path.join(root,'web/index.html'),'utf8'),main=fs.readFileSync(path.join(root,'src/main.cjs'),'utf8');
 assert.ok(app.indexOf("['home','主页']")<app.indexOf("['tasks','任务']"));assert.ok(app.indexOf("['tasks','任务']")<app.indexOf("['content','内容']"));assert.ok(app.indexOf("['content','内容']")<app.indexOf("['account','账号']"));assert.ok(app.includes('window.hideStudioTasks?.();window.hideStudioWebsite?.();'));assert.ok(app.includes("if(!$('task-page').hidden)return window.syncStudioTaskBounds?.()"));assert.ok(html.includes('id="task-page"'));assert.ok(html.includes('src="task-ui.js"'));assert.ok(main.includes("bind('studio:tasks-open'"));assert.ok(main.includes('trusted(e)'));assert.ok(main.includes('catch{taskConfig={error:'));assert.ok(fs.readFileSync(path.join(root,'web/website-ui.js'),'utf8').includes('window.hideStudioTasks?.()'));
});
