'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(process.env.PUBLISH_UI_UNDER_TEST||path.join(__dirname,'../backend/web/publish-console.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};}
function fixture({jobs=[],fieldResponse=null,uploadResponse=null,hangCatalog=false}={}){
 const ids=new Map(),timers=new Map(),calls=[];let timerId=0,observer=null,locked=false;
 const modes=[],urls=[],revoked=[];
 class LocalURL extends URL{static createObjectURL(file){const url='blob:fixture-'+(urls.length+1);urls.push({file,url});return url;}static revokeObjectURL(url){revoked.push(url);}}
 class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.hidden=false;this.disabled=false;this.checked=false;this.files=[];this.value='';this.classes=new Set();this.classList={toggle:(name,value)=>{value??=!this.classes.has(name);value?this.classes.add(name):this.classes.delete(name);return value;},contains:name=>this.classes.has(name),add:name=>this.classes.add(name)};this._text='';}
  setAttribute(k,v){this[k]=v;}
  click(){this.clicked=true;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  append(...children){this.children.push(...children);for(const c of children)c.parent=this;}
  replaceChildren(...children){this.children=children;}
  set textContent(v){this._text=String(v);this.children=[];}
  get textContent(){return this._text+this.children.map(x=>x.textContent||'').join('');}
  get firstChild(){if(!this.children[0])this.children.push(new Element('text'));return this.children[0];}
  set innerHTML(value){for(const match of value.matchAll(/<([a-z]+)[^>]*\bid="(pub-[^"]+)"[^>]*>/g)){const n=new Element(match[1]);n.id=match[2];ids.set(n.id,n);}for(const kind of ['imageText','video']){const n=new Element('button');n.dataset.publishKind=kind;modes.push(n);}}
  querySelectorAll(selector){const walk=n=>[n,...n.children.flatMap(walk)];const media=ids.get('pub-file-list');if(selector==='.publish-media-remove,.publish-media-order button')return media?walk(media).filter(x=>x.className==='publish-media-remove'||x.tag==='button'&&x.parent?.className==='publish-media-order'):[];if(selector==='.publish-upload-tile')return media?walk(media).filter(x=>x.className==='publish-upload-tile'):[];if(selector==='[data-publish-kind]')return modes;if(selector==='#pub-accounts input')return ids.get('pub-accounts').children.map(x=>x.children[0]);if(selector.startsWith('#pub-form input'))return [...ids.values()].filter(x=>['input','textarea','select'].includes(x.tag)).concat(modes);return [];}
  reset(){for(const x of ids.values()){x.value='';x.files=[];}}
 }
 const main=new Element('main'),top=new Element('header'),nav=new Element('button'),body=new Element('body');nav.dataset.nav='publish';body.classList.contains=()=>locked;
 const doc={body,createElement:t=>new Element(t),getElementById:id=>ids.get(id),querySelector:selector=>selector==='main'?main:selector==='.topbar'?top:nav,querySelectorAll:selector=>selector==='main > section'?main.children:[nav],addEventListener(){}};
 const accounts=[{id:'a',name:'图文账号',platform:'小红书',status:1,types:['imageText','video']},{id:'off',name:'过期账号',platform:'小红书',status:2,types:['imageText','video']},{id:'video-only',name:'仅视频',platform:'哔哩哔哩',status:1,types:['video']}];
 const crypto=require('node:crypto').webcrypto;
 const response=data=>({ok:true,status:200,json:async()=>structuredClone(data)});
 const fetch=async(url,options)=>{calls.push({url,options});if(url.endsWith('/catalog'))return hangCatalog?new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(Error('aborted'),{name:'AbortError'})))):response({accounts,checkedAt:1});if(url.endsWith('/jobs')&&options.method==='GET')return response({items:jobs});if(url.endsWith('/fields'))return fieldResponse?fieldResponse.promise:response({fields:{title:{maxLength:20},description:{maxLength:1000}},imageLimit:18});if(url.includes('/files/'))return uploadResponse?uploadResponse.promise:response(jobs[0]);if(url.endsWith('/prepare'))return response({...jobs[0],state:'preparing'});throw Error('unexpected '+url);};
 vm.runInNewContext(source,{document:doc,window:{addEventListener(){}},crypto,URL:LocalURL,AbortController,Option:function(label,value){const n=new Element('option');n.textContent=label;n.value=value;return n;},MutationObserver:class{constructor(cb){observer=cb;}observe(){}},fetch,setTimeout:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id)});
 return {ids,nav,modes,calls,timers,accounts,urls,revoked,async open(){await nav.onclick();},input(id){return ids.get(id);},checkbox(i){return ids.get('pub-accounts').children[i].children[0];},lock(){locked=true;observer();},response};
}
const job=kind=>({id:'original-job',kind,channel:'local',title:'原任务标题',description:'原任务正文',state:'uploading',targets:[{accountId:'a',platform:'小红书',accountName:'图文账号',state:'waiting'}],files:kind==='video'?[{id:'v',name:'v.mp4',size:4,role:'video',received:0},{id:'c',name:'c.png',size:4,role:'cover',received:0}]:[{id:'i',name:'i.png',size:4,role:'image',received:0}],confirmation:null});

test('refresh then select unfinished video restores mode and enables file inputs without changing accounts',async()=>{
 const j=job('video'),f=fixture({jobs:[j]});await f.open();f.input('pub-history').children[0].onclick();
 assert.equal(f.input('pub-media').disabled,false);assert.equal(f.input('pub-cover').disabled,false);assert.equal(f.input('pub-cover-label').hidden,false);assert.equal(f.input('pub-media').multiple,false);assert.equal(f.input('pub-check').disabled,false);assert.equal(f.input('pub-title').value,j.title);assert.equal(f.input('pub-title').disabled,true);assert.equal(f.input('pub-channel').value,'local');
});
test('offline and unsupported accounts stay disabled after shared controls rerender',async()=>{
 const f=fixture();await f.open();assert.equal(f.checkbox(0).disabled,false);assert.equal(f.checkbox(1).disabled,true);assert.equal(f.checkbox(2).disabled,true);
});
test('late imageText contract after switching to video does not resurrect old selection',async()=>{
 const d=deferred(),f=fixture({fieldResponse:d});await f.open();const input=f.checkbox(0);input.checked=true;const pending=input.onchange();assert.equal(f.input('pub-check').disabled,true);f.modes[1].onclick();d.resolve(f.response({fields:{title:{}},imageLimit:18}));await pending;assert.equal(f.input('pub-check').disabled,true);assert.equal(f.checkbox(0).checked,false);assert.equal(f.input('pub-target-fields').textContent,'');
});
test('field query blocks checking until exact selection contract is settled',async()=>{
 const d=deferred(),f=fixture({fieldResponse:d});await f.open();const input=f.checkbox(0);input.checked=true;const pending=input.onchange();assert.equal(f.input('pub-check').disabled,true);assert.equal(f.input('pub-refresh').disabled,true);d.resolve(f.response({fields:{title:{}},imageLimit:18}));await pending;assert.equal(f.input('pub-check').disabled,false);assert.equal(f.input('pub-refresh').disabled,false);
});
test('resume starts from original job and never creates another job',async()=>{
 const j=job('imageText'),f=fixture({jobs:[j]});await f.open();f.input('pub-history').children[0].onclick();f.input('pub-media').files=[{name:'i.png',size:4,slice:()=>new Blob(['test'])}];await f.input('pub-form').onsubmit({preventDefault(){}});
 assert.equal(f.calls.some(c=>c.url.endsWith('/jobs')&&c.options.method==='POST'),false);assert.ok(f.calls.some(c=>c.url.endsWith('/files/i')));assert.ok(f.calls.some(c=>c.url.endsWith('/prepare')));
});
test('wrong resume file count stops before any binary upload',async()=>{
 const f=fixture({jobs:[job('imageText')]});await f.open();f.input('pub-history').children[0].onclick();f.input('pub-media').files=[1,2].map(()=>({name:'i.png',size:4}));await f.input('pub-form').onsubmit({preventDefault(){}});assert.equal(f.calls.some(c=>c.url.includes('/files/')),false);assert.match(f.input('pub-status').textContent,/数量/);
});
test('late upload response after identity lock does not render filename or start prepare',async()=>{
 const j=job('imageText'),d=deferred(),f=fixture({jobs:[j],uploadResponse:d});await f.open();f.input('pub-history').children[0].onclick();f.input('pub-media').files=[{name:'i.png',size:4,slice:()=>new Blob(['test'])}];const running=f.input('pub-form').onsubmit({preventDefault(){}});for(let i=0;i<10&&!f.calls.some(c=>c.url.includes('/files/'));i++)await tick();f.lock();d.resolve(f.response(j));await running;assert.equal(f.calls.some(c=>c.url.endsWith('/prepare')),false);assert.equal(f.input('pub-current').textContent,'');assert.equal(f.input('pub-file-list').textContent,'');
});
test('bounded network timeout reports reason without retrying or creating a job',async()=>{
 const f=fixture({hangCatalog:true}),opening=f.open();const deadline=[...f.timers.values()].find(t=>t.ms===45000);assert.ok(deadline);deadline.fn();await opening;assert.match(f.input('pub-status').textContent,/请求超时/);assert.equal(f.calls.filter(c=>c.url.endsWith('/catalog')).length,1);assert.equal(f.calls.some(c=>c.options.method==='POST'),false);
});
const photo=(name)=>({name,size:4,lastModified:1,arrayBuffer:async()=>new ArrayBuffer(4),slice:()=>new Blob(['test'])});
test('live preview uses exact user text and Unicode code point counters',async()=>{
 const f=fixture();await f.open();f.input('pub-title').value='你好🙂';f.input('pub-title').oninput();f.input('pub-description').value='<script>text only</script>';f.input('pub-description').oninput();assert.equal(f.input('pub-preview-title').textContent,'你好🙂');assert.equal(f.input('pub-title-count').textContent,'3 字');assert.equal(f.input('pub-preview-description').textContent,'<script>text only</script>');assert.equal(f.input('pub-preview-description').children.length,0);
});
test('selected local media receives blob previews, is never uploaded for viewing and is released on identity lock',async()=>{
 const f=fixture();await f.open();f.input('pub-media').files=[photo('a.png'),photo('b.png')];f.input('pub-media').onchange();assert.equal(f.urls.length,2);assert.ok(f.input('pub-preview-media').children[0].src.startsWith('blob:'));assert.equal(f.calls.some(c=>c.url.includes('/files/')),false);f.lock();assert.equal(f.revoked.length,2);
});
test('image reordering changes thumbnail order and preview first image without changing file bytes',async()=>{
 const f=fixture();await f.open();f.input('pub-media').files=[photo('first.png'),photo('second.png')];f.input('pub-media').onchange();const tile=f.input('pub-file-list').children[0],order=tile.children.find(x=>x.className==='publish-media-order');order.children[1].onclick();assert.equal(f.input('pub-file-list').children[0].title,'second.png');assert.equal(f.input('pub-preview-media').children[0].alt,'second.png');
});
test('removing an image revokes its URL and lets the same file be chosen again',async()=>{
 const f=fixture();await f.open();const a=photo('again.png');f.input('pub-media').files=[a];f.input('pub-media').onchange();f.input('pub-file-list').children[0].children.find(x=>x.className==='publish-media-remove').onclick();assert.equal(f.revoked.length,1);assert.equal(f.input('pub-media').value,'');f.input('pub-media').files=[a];f.input('pub-media').onchange();assert.equal(f.input('pub-file-list').children[0].title,'again.png');
});
test('video preview keeps the real video separate from its provided cover',async()=>{
 const f=fixture();await f.open();f.modes[1].onclick();f.input('pub-media').files=[photo('real.mp4')];f.input('pub-media').onchange();f.input('pub-cover').files=[photo('cover.png')];f.input('pub-cover').onchange();const player=f.input('pub-preview-media').children[0];assert.equal(player.tag,'video');assert.equal(player.controls,true);assert.ok(player.poster?.startsWith('blob:'));assert.notEqual(player.poster,player.src);
});
test('history opens only a real task modal and selection closes it',async()=>{
 const f=fixture({jobs:[job('imageText')]});await f.open();f.input('pub-records').onclick();assert.equal(f.input('pub-history-dialog').open,true);f.input('pub-history').children[0].onclick();assert.equal(f.input('pub-history-dialog').open,false);
});
test('shared control refresh never enables image reorder beyond the list boundary',async()=>{
 const f=fixture();await f.open();f.input('pub-media').files=[photo('first.png'),photo('last.png')];f.input('pub-media').onchange();const input=f.checkbox(0);input.checked=true;await input.onchange();const tiles=f.input('pub-file-list').children;const firstOrder=tiles[0].children.find(x=>x.className==='publish-media-order'),lastOrder=tiles[1].children.find(x=>x.className==='publish-media-order');assert.equal(firstOrder.children[0].disabled,true);assert.equal(lastOrder.children[1].disabled,true);firstOrder.children[0].onclick();assert.equal(f.input('pub-file-list').children[0].title,'first.png');
});
test('history restores frozen video kind and copy, rather than leaving the previous draft mode visible',async()=>{
 const j={...job('video'),state:'submitted',targets:[{...job('video').targets[0],state:'submitted',taskSetId:'task'}]},f=fixture({jobs:[j]});await f.open();f.input('pub-history').children[0].onclick();assert.equal(f.input('pub-title').value,j.title);assert.equal(f.input('pub-description').value,j.description);assert.match(f.input('pub-footer-summary').textContent,/视频/);assert.equal(f.input('pub-title').disabled,true);
});
