'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const code=fs.readFileSync(require('node:path').join(__dirname,'../../browser-extension/yxer-auth.js'),'utf8');
function run({origin='https://www.yixiaoer.cn',referrer='https://studio.example.invalid/',raw='"fixture-session-token"',top=false,permission=null,handle=null}={}){
 const events={},posted=[],dispatched=[],logs=[],timers=[];const parent={postMessage:(...args)=>posted.push(args)},window={parent,addEventListener:(k,f)=>events[k]=f,dispatchEvent:e=>dispatched.push(e)};window.self=window;window.top=top?window:{};
 const console={log:(...args)=>logs.push(args)};
 vm.runInNewContext(code,{window,location:{origin,href:origin+'/web/publish'},document:{referrer,readyState:'complete',documentElement:{dataset:{}},...(handle?{requestStorageAccess:async types=>{assert.equal(types.localStorage,true);return handle;}}:{})},localStorage:{getItem:key=>{assert.equal(key,'token');return raw;}},...(permission?{navigator:{permissions:{query:async()=>({state:permission})}}}:{}),URL,MessageEvent:class{constructor(type,v){this.type=type;Object.assign(this,v);}},console,setTimeout:f=>timers.push(f),setInterval:()=>1,clearInterval(){}});
 const pending=timers.map(f=>f());return{window,events,posted,dispatched,logs,console,pending};
}
for(const opts of [{top:true},{origin:'https://other.example'},{referrer:'https://other.example/'},{referrer:''}])assert.equal(run(opts).dispatched.length,0);
const x=run();assert.equal(x.dispatched.length,1);const m=x.dispatched[0];assert.equal(m.origin,'https://studio.example.invalid');assert.equal(m.data.data,'fixture-session-token');assert.equal(m.data.type,'auth');assert.equal(m.source,x.window.parent);assert.equal(m.data.config.contentSources.join(','),'native,assetLibrary');
assert.ok(!JSON.stringify(x.posted).includes('fixture-session-token'));x.console.log('[Apass] Received message:',m.data);assert.equal(x.logs.length,0);x.console.log('ordinary');assert.equal(x.logs.length,1);
x.events.message({source:{},origin:m.origin,data:{type:'studio-yxer-auth-retry'}});assert.equal(x.dispatched.length,1);
assert.equal(run({raw:null}).posted.at(-1)[0].state,'browser-login-required');assert.equal(run({raw:null}).dispatched.length,0);
x.events.pagehide();x.events.message({source:x.window.parent,origin:m.origin,data:{type:'studio-yxer-auth-retry'}});assert.equal(x.dispatched.length,1);
console.log('BRIDGE_OK: same official origin only; existing valid-session handoff; no credential to host/log; missing session explicit; foreign source rejected; teardown stops work');

(async()=>{const granted=run({raw:null,permission:'granted',handle:{localStorage:{getItem:()=> '\"fixture-session-token\"'}}});await Promise.all(granted.pending);assert.equal(granted.dispatched.length,1);assert.ok(!JSON.stringify(granted.posted).includes('fixture-session-token'));console.log('STORAGE_REUSE_OK: already-granted access handle restores same-origin session without another prompt');})().catch(e=>{console.error(e);process.exitCode=1;});
