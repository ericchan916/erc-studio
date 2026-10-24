'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../../browser-extension/yxer-auth.js'),'utf8');
class E{constructor(tag){this.tag=tag;this.children=[];this.style={};this.dataset={};}append(...n){this.children.push(...n);}remove(){this.removed=true;}querySelector(tag){return this.children.find(x=>x.tag===tag);}}
const body=new E('body'),events={},posted=[],dispatched=[],timers=[],opened=[];let unpartitioned=null,denied=false;
const parent={postMessage:(...args)=>posted.push(args)},popup={closed:false,close(){this.closed=true;}},window={parent,name:'',open:(...args)=>{opened.push(args);return popup;},addEventListener:(k,f)=>events[k]=f,dispatchEvent:e=>dispatched.push(e)};window.self=window;window.top={};
const context={window,document:{referrer:'https://studio.example.invalid/',readyState:'complete',body,documentElement:{dataset:{}},createElement:t=>new E(t),requestStorageAccess:async()=>{if(denied)throw Error('denied');return{localStorage:{getItem:()=>unpartitioned}};}},location:{origin:'https://www.yixiaoer.cn',href:'https://www.yixiaoer.cn/web/publish'},localStorage:{getItem:()=>null},navigator:{permissions:{query:async()=>({state:'prompt'})}},crypto:{randomUUID:()=> 'fixture-login-nonce'},console:{log(){}},URL,MessageEvent:class{constructor(type,value){this.type=type;Object.assign(this,value);}},setTimeout:f=>timers.push(f),setInterval:()=>1,clearInterval(){}};
(async()=>{
 vm.runInNewContext(code,context);await Promise.all(timers.map(f=>f()));const gate=body.children[0];assert.equal(gate.children[0].textContent,'连接本人蚁小二账号');const[login,retry]=gate.children[2].children;
 assert.equal(dispatched.length,0);login.onclick();assert.equal(opened.length,1);assert.ok(opened[0][0].startsWith('https://www.yixiaoer.cn/web/login?redirect='));assert.ok(opened[0][0].includes('fixture-login-nonce'));
 events.message({source:{},origin:'https://www.yixiaoer.cn',data:{type:'studio-yxer-login-ready',nonce:'fixture-login-nonce'}});assert.equal(dispatched.length,0);
 denied=true;await retry.onclick();assert.equal(dispatched.length,0);assert.ok(gate.children[1].textContent.includes('尚未允许'));
 denied=false;unpartitioned='"fixture-own-account-token"';await retry.onclick();assert.equal(dispatched.length,1);assert.equal(dispatched[0].data.data,'fixture-own-account-token');assert.ok(gate.removed);assert.ok(!JSON.stringify(posted).includes('fixture-own-account-token'));
 events.message({source:parent,origin:'https://studio.example.invalid',data:{type:'studio-yxer-auth-ready'}});assert.ok(popup.closed);
 events.message({source:parent,origin:'https://studio.example.invalid',data:{type:'studio-yxer-auth-failed'}});assert.equal(body.children.length,2);assert.ok(body.children[1].children[1].textContent.includes('团队'));
 events.pagehide();const count=dispatched.length;events.message({source:parent,origin:'https://studio.example.invalid',data:{type:'studio-yxer-auth-retry'}});assert.equal(dispatched.length,count);
 console.log('FIRST_USE_OK: empty session has official login popup; denial explicit; own storage session reused; no credential to studio; final vendor success closes only owned popup; failed/expired auth has recovery entry');
})().catch(e=>{console.error(e);process.exitCode=1;});

// The official top-level login monitor sends a nonce/status only, and not from unrelated windows.
(()=>{
 let tick;const out=[];const opener={location:{origin:'https://www.yixiaoer.cn'},document:{referrer:'https://studio.example.invalid/'},postMessage:(...args)=>out.push(args)};
 const top={name:'studio-yxer-login-fixture-open-nonce',opener,addEventListener(){}};top.top=top;top.self=top;
 const location={origin:'https://www.yixiaoer.cn',href:'https://www.yixiaoer.cn/web/login#studio-yxer-login=fixture-open-nonce',pathname:'/web/login'};
 vm.runInNewContext(code,{window:top,location,URL,localStorage:{getItem:()=> 'fixture-secret-never-sent'},setInterval:f=>{tick=f;return 1;},clearInterval(){}});
 tick();assert.equal(out.length,0);location.pathname='/web/publish';tick();assert.equal(out.length,1);assert.equal(out[0][0].nonce,'fixture-open-nonce');assert.ok(!JSON.stringify(out).includes('fixture-secret-never-sent'));
 console.log('POPUP_MONITOR_OK: login-page token presence is not completion; same-official-opener/referrer required; only completion nonce/status forwarded');
})();
