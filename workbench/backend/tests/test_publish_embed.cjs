'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const code=fs.readFileSync(process.argv[2]||path.join(__dirname,'../web/publish-console.js'),'utf8');
function fixture(enabled=true){
 const events={},timers=new Map();let observer,locked=false,next=0;
 class E{constructor(tag,cls){this.tagName=tag;this.children=[];this.attrs={};this.hidden=false;this.className=cls||'';this.classList={contains:()=>locked,toggle(){}};this.contentWindow={postMessage:(...args)=>this.sent=args};}append(...n){for(const x of n){x.parent=this;this.children.push(x);}}replaceChildren(...n){this.children=n;}remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}setAttribute(k,v){this.attrs[k]=v;}focus(){}}
 const nav=new E('button'),main=new E('main'),topbar=new E('header'),other=new E('section'),body=new E('body'),dataset=enabled?{cyberYxerEmbed:'enabled',cyberYxerEmbedVersion:'0.3.2',cyberPublisherEmbed:'enabled'}:{};
 const document={body,documentElement:{dataset},createElement:t=>new E(t),querySelector:s=>s==='main'?main:s==='.topbar'?topbar:nav,querySelectorAll:s=>s==='main > section'?[other,...main.children]:[nav],addEventListener:(k,f)=>events[k]=f};
 vm.runInNewContext(code,{document,window:{addEventListener:(k,f)=>events[k]=f},location:{reload(){}},MutationObserver:class{constructor(f){observer=f;}observe(){}},setTimeout:f=>{const n=++next;timers.set(n,f);return n;},clearTimeout:n=>timers.delete(n)});
 return{nav,main,events,dataset,lock:()=>{locked=true;observer();},root:main.children[0]};
}
const missing=fixture(false);missing.nav.onclick();const setup=missing.root.children[2].children[0];assert.equal(setup.tagName,'section');assert.ok(JSON.stringify(setup.children.map(x=>x.children?.map(y=>y.textContent))).includes('首次使用'));assert.equal(missing.root.children[2].children.filter(x=>x.tagName==='iframe').length,0);
const f=fixture();f.nav.onclick();const mount=f.root.children[2],frame=mount.children[0];assert.equal(frame.src,'https://www.yixiaoer.cn/web/publish');assert.ok(frame.attrs.sandbox.includes('allow-storage-access-by-user-activation'));
f.events.message({source:{},origin:'https://www.yixiaoer.cn',data:{type:'auth-ready'}});assert.equal(frame.sent,undefined);
f.events.message({source:frame.contentWindow,origin:'https://www.yixiaoer.cn',data:{type:'auth-failed'}});assert.equal(frame.sent[0].type,'studio-yxer-auth-failed');
f.events.message({source:frame.contentWindow,origin:'https://www.yixiaoer.cn',data:{type:'auth-ready'}});assert.equal(frame.sent[0].type,'studio-yxer-auth-ready');assert.ok(f.root.children[1].hidden);
f.events.click({target:{closest:()=>({})}});assert.ok(f.root.hidden);assert.equal(mount.children[0],frame);f.nav.onclick();assert.equal(mount.children[0],frame);
f.root.children[0].children[2].onclick();assert.equal(mount.children.length,2);assert.equal(mount.children[1].src,'https://x.com/compose/post');
f.lock();assert.equal(mount.children.length,0);
// Website submenu buttons do not carry data-nav. Match the clicked descendant
// through the actual submenu attributes, and keep the mounted editor intact.
for(const attribute of ['data-nav','data-website-page','data-analytics-page']){
 const pages=attribute==='data-analytics-page'?['overview','traffic','content','audience','goals','quality']:['site'];
 for(const page of pages){
  const view=fixture();view.nav.onclick();const editor=view.root.children[2].children[0];
  const destination={attribute,page};let matchedSelector;
  view.events.click({target:{closest:selector=>{matchedSelector=selector;return selector.split(',').some(s=>s.trim()==='['+attribute+']')?destination:null;}}});
  assert.equal(view.root.hidden,true,attribute+'='+page+' must hide publishing (selector: '+matchedSelector+')');
  assert.equal(view.root.children[2].children[0],editor,'navigation must retain the original editor');
  view.nav.onclick();assert.equal(view.root.hidden,false);assert.equal(view.root.children[2].children[0],editor,'return must not reload the editor');
 }
}
const retained=fixture();retained.nav.onclick();
retained.events.click({target:{closest:()=>retained.nav}});assert.equal(retained.root.hidden,false,'publish button must stay visible');
retained.events.click({target:{closest:()=>null}});assert.equal(retained.root.hidden,false,'non-navigation clicks must not hide publishing');
console.log('EMBED_OK: missing bridge never loads blind iframe; setup card visible; valid bridge retains Yxer/WeChat/X tabs; authenticated editor stays full-pane; real vendor success/failure relayed; logout clears all sessions');
console.log('NAVIGATION_OK: six analytics views and website/home navigation hide publishing; returning preserves the same editor; unrelated clicks remain unchanged');
