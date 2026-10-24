'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {WebsitePane,WEBSITE_URL,WEBSITE_PARTITION,allowedWebsiteURL,boundedRect}=require('../src/website.cjs');
function fixture(){
 const events={},sent=[],children=[],urls=[];let destroyed=false;
 const ses={setPermissionRequestHandler(fn){this.request=fn;},setPermissionCheckHandler(fn){this.check=fn;},flushStorageData(){this.flushed=true;}};
 const wc={on(name,fn){events[name]=fn;},getURL(){return urls.at(-1)||'';},getUserAgent(){return 'Chrome/148 Electron/44.5.1';},setUserAgent(v){this.ua=v;},setWindowOpenHandler(fn){this.popup=fn;},loadURL(url){urls.push(url);return Promise.resolve();},isDestroyed(){return destroyed;},close(){destroyed=true;},session:ses,navigationHistory:{canGoBack:()=>urls.length>1,goBack:()=>urls.pop()},reload(){this.reloaded=true;}};
 let prefs,partition;const win={getContentSize:()=>[1200,800],contentView:{addChildView:v=>children.push(v),removeChildView:v=>children.splice(children.indexOf(v),1)}};
 const pane=new WebsitePane({win,session:{fromPartition(value){partition=value;return ses;}},WebContentsView:class{constructor(options){prefs=options.webPreferences;this.webContents=wc;}setBounds(b){this.rect=b;}},onEvent:value=>sent.push(value)});
 return {pane,wc,events,ses,sent,children,urls,get prefs(){return prefs;},get partition(){return partition;}};
}
test('official website entry accepts only HTTPS root/www without credentials',()=>{
 assert.ok(allowedWebsiteURL(WEBSITE_URL));assert.ok(allowedWebsiteURL('https://www.example.invalid/knowledge?q=test'));
 for(const value of ['http://example.invalid','https://example.invalid.evil.test','https://evil.test/example.invalid','https://user:password@example.invalid','https://example.invalid:8443','file:///etc/passwd','javascript:alert(1)','invalid'])assert.equal(allowedWebsiteURL(value),false,value);
});
test('website native pane is isolated, sandboxed, permissionless and reused',()=>{
 const f=fixture();f.pane.open();f.pane.open();assert.equal(f.children.length,1);assert.equal(f.partition,WEBSITE_PARTITION);assert.deepEqual(f.urls,[WEBSITE_URL]);
 for(const [key,value] of Object.entries({nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,allowRunningInsecureContent:false}))assert.equal(f.prefs[key],value);
 assert.equal(f.prefs.preload,undefined);assert.equal(f.ses.check(),false);let permission;f.ses.request(null,'camera',v=>permission=v);assert.equal(permission,false);
 f.pane.hide();assert.equal(f.children.length,0);f.pane.open();assert.equal(f.children.length,1);assert.equal(f.urls.length,1);
});
test('website popup stays in same pane; external navigation and redirect are rejected',()=>{
 const f=fixture();f.pane.open();assert.deepEqual(f.wc.popup({url:'https://example.invalid/classroom'}),{action:'deny'});assert.equal(f.urls.at(-1),'https://example.invalid/classroom');
 for(const name of ['will-navigate','will-redirect']){let prevented=false;f.events[name]({preventDefault(){prevented=true;}},'https://evil.test');assert.equal(prevented,true);}
 f.wc.popup({url:'https://evil.test'});assert.equal(f.urls.length,2);assert.match(f.sent.at(-1).message,/不属于官网/);
});
test('main-frame load error is visible; subresource/cancel failures are ignored',()=>{
 const f=fixture();f.pane.open();f.events['did-start-loading']();assert.equal(f.pane.state().phase,'loading');f.events['did-finish-load']();assert.equal(f.pane.state().phase,'ready');
 f.events['did-fail-load'](null,-3,'','',true);f.events['did-fail-load'](null,-105,'','',false);assert.equal(f.pane.state().phase,'ready');f.events['did-fail-load'](null,-105,'','',true);assert.equal(f.pane.state().phase,'error');
 f.events['did-stop-loading']();assert.equal(f.pane.state().phase,'error');f.events['did-start-loading']();f.events['did-stop-loading']();assert.equal(f.pane.state().phase,'ready');
});
test('website commands are allowlisted and teardown flushes without clearing storage',async()=>{
 const f=fixture();await assert.rejects(f.pane.command('home'),/website_missing/);f.pane.open();await f.pane.command('reload');assert.ok(f.wc.reloaded);await assert.rejects(f.pane.command('execute'),/command_invalid/);
 f.pane.close();assert.equal(f.children.length,0);assert.equal(f.ses.flushed,true);assert.equal(f.pane.view,null);
});
test('native website bounds validate finite inputs and remain inside window',()=>{
 assert.deepEqual(boundedRect({x:-10,y:40,width:5000,height:4000},[1200,800]),{x:0,y:40,width:1200,height:760});assert.deepEqual(boundedRect({x:2000,y:2000,width:-1,height:0},[1200,800]),{x:1199,y:799,width:1,height:1});
 for(const value of [null,{}, {x:NaN,y:0,width:1,height:1}])assert.throws(()=>boundedRect(value,[1200,800]),/bounds_invalid/);
});
