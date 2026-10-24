'use strict';
// Browser transport for the exact native renderer. No Node/Electron APIs or credentials.
(()=>{
 const sheet=document.createElement('link');sheet.rel='stylesheet';sheet.href='/browser-adapter.css';document.head.append(sheet);
 const listeners=new Map(),emit=(name,value)=>{for(const fn of listeners.get(name)||[])fn(value);},on=(name,fn)=>{const set=listeners.get(name)||new Set();set.add(fn);listeners.set(name,set);return()=>set.delete(fn);};
 let links=null,contentSnapshot=null,contentRefreshInFlight=null,accountSnapshot=null,accountsInFlight=null,scanFlow=null,otpFlow=null;
 const uuid=()=>crypto.randomUUID();
 async function request(path,{method='GET',body,key}={}){const response=await fetch(path,{method,credentials:'same-origin',headers:{...(body!==undefined?{'Content-Type':'application/json'}:{}),...(key?{'Idempotency-Key':key}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{})});let packet;try{packet=await response.json();}catch{packet={code:'invalid_response',message:'服务返回格式异常'}}if(!response.ok){const e=new Error(packet.message||packet.code);e.code=packet.code;e.status=response.status;throw e;}return packet;}
 async function packet(action){try{return {ok:true,data:await action()};}catch(e){return {ok:false,error:{code:e.code||'request_failed',message:e.message,status:e.status||0}};}}
 async function proof(){const verifier=Array.from(crypto.getRandomValues(new Uint8Array(32))).map(x=>x.toString(16).padStart(2,'0')).join(''),hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier));return {verifier,challenge:Array.from(new Uint8Array(hash)).map(x=>x.toString(16).padStart(2,'0')).join('')};}
 const query=value=>{const u=new URLSearchParams();for(const [k,v]of Object.entries(value||{}))if(v!==undefined&&v!==null&&v!=='')u.set(k==='search'?'q':k,String(v));return u.toString()?'?'+u:'';};
 async function conversation(input){return packet(async()=>{const op=input.op;
  if(op==='status')return request('/health');if(op==='copy'){await navigator.clipboard.writeText(input.text);return {copied:true};}if(op==='members')return request('/v1/auth/members');
  if(op==='scanStart'){const p=await proof(),flow=await request('/v1/auth/feishu/start',{method:'POST',body:{client_challenge:p.challenge,device_id:uuid()}});scanFlow={...flow,verifier:p.verifier};return flow;}
  if(op==='scanOpen'){if(!scanFlow)throw Error('请先生成登录二维码');window.open(scanFlow.verification_url,'_blank','noopener,noreferrer');return {opened:true};}
  if(op==='scanPoll'){if(!scanFlow)throw Error('请先生成登录二维码');return request('/v1/auth/feishu/poll',{method:'POST',body:{login_id:scanFlow.login_id,client_verifier:scanFlow.verifier}});}
  if(op==='otpStart'){const p=await proof(),flow=await request('/v1/auth/login/start',{method:'POST',body:{member_handle:input.member_handle,client_challenge:p.challenge,device_id:uuid()}});otpFlow={...flow,verifier:p.verifier};return flow;}
  if(op==='otpPoll'){if(!otpFlow)throw Error('请先获取登录码');return request('/v1/auth/login/poll',{method:'POST',body:{login_id:otpFlow.login_id,client_verifier:otpFlow.verifier,code:input.code}});}
  if(op==='me')return request('/v1/me');if(op==='logout')return request('/v1/auth/logout',{method:'POST',body:{},key:uuid()});
  if(op==='conversations')return request('/v1/conversations'+query({scope:input.scope,q:input.search,cursor:input.cursor,limit:input.limit}));
  if(op==='start')return request('/v1/conversations/start-message',{method:'POST',body:{title:input.title,text:input.text,client_message_id:input.client_message_id},key:input.key});
  if(op==='detail')return request('/v1/conversations/'+input.conversation_id);
  if(op==='active')return request('/v1/conversations/active');
  if(op==='continue')return request('/v1/conversations/'+input.conversation_id+'/continue',{method:'POST',body:{expected_revision:input.expected_revision},key:input.key});
  if(['rename','pin','archive','trash','restore'].includes(op)){const body={expected_version:input.expected_version,...(op==='rename'?{title:input.title}:{}),...(op==='pin'?{pinned:input.pinned}:{}),...(op==='archive'?{archived:input.archived}:{})};return request('/v1/conversations/'+input.conversation_id+'/'+op,{method:'POST',body,key:input.key});}
  if(op==='retry')return request('/v1/jobs/'+input.job_id+'/retry',{method:'POST',body:{client_message_id:input.client_message_id,expected_version:input.expected_version,acknowledge_unknown:!!input.acknowledge_unknown},key:input.key});
  if(op==='create')return request('/v1/conversations',{method:'POST',body:{title:input.title},key:input.key});
  if(op==='messages')return request('/v1/conversations/'+input.conversation_id+'/messages'+query({before:input.before,limit:input.limit}));
  if(op==='send')return request('/v1/conversations/'+input.conversation_id+'/messages',{method:'POST',body:{text:input.text,client_message_id:input.client_message_id},key:input.key});
  if(op==='respond')return request('/v1/jobs/'+input.job_id+'/respond',{method:'POST',body:{text:input.text,client_message_id:input.client_message_id},key:input.key});
  if(op==='recover')return request('/v1/jobs/'+input.job_id+'/recover',{method:'POST',body:{},key:input.key});
  if(op==='job')return request('/v1/jobs/'+input.job_id);if(op==='cancel')return request('/v1/jobs/'+input.job_id+'/cancel',{method:'POST',body:{},key:input.key});throw Error('不支持的对话操作');
 });}
 async function getLinks(){return links||(links=await request('/v1/workspace/links'));}
 function external(url){const u=new URL(url);if(u.protocol!=='https:'||u.username||u.password)throw Error('入口地址未通过校验');window.open(u.href,'_blank','noopener,noreferrer');return true;}
 function localNavigate(url){const u=new URL(url);if(u.protocol!=='https:'||!u.hostname.endsWith('.feishu.cn')||u.username||u.password)throw Error('飞书入口地址未通过校验');window.location.assign(u.href);return true;}
 function mount(id,title,message,url){const root=document.getElementById(id);if(!root)return;root.replaceChildren();const box=document.createElement('div');box.className='office-browser-card';const h=document.createElement('h2'),p=document.createElement('p'),button=document.createElement('button');h.textContent=title;p.textContent=message;button.textContent='在新标签页打开';button.onclick=()=>external(url);box.append(h,p,button);root.append(box);}
 const feishuFrames=new Map();let embedCapability;
 // A project iframe lifecycle is not a content data snapshot.
 const paneStateChannel=kind=>kind==='content'?'content-project-state':kind==='reviews'?'data-review-state':kind+'-state';
 async function embedEnabled(){if(document.documentElement.dataset.cyberFeishuEmbed==='enabled')return true;if(embedCapability!==undefined)return embedCapability;try{const response=await fetch('/health',{credentials:'same-origin',cache:'no-store'});embedCapability=response.headers.get('x-cyber-feishu-embed')==='enabled';}catch{embedCapability=false;}return embedCapability;}
 function mountFeishu(kind,id,url,enabled){const target=new URL(url);if(target.protocol!=='https:'||!target.hostname.endsWith('.feishu.cn')||target.username||target.password)throw Error('飞书入口地址未通过校验');const root=document.getElementById(id);if(!root)return false;root.replaceChildren();if(!enabled){const box=document.createElement('div');box.className='office-browser-card';const title=document.createElement('h2'),text=document.createElement('p'),install=document.createElement('a'),direct=document.createElement('button');title.textContent='当前浏览器未启用飞书内嵌桥';text.textContent='请使用 Chrome 或 Edge 安装ERC Studio扩展后重新打开本页。应用内浏览器不支持此扩展。';install.href='/studio-feishu-embed.zip';install.textContent='下载内嵌桥扩展';install.className='btn';install.setAttribute('download','studio-feishu-embed.zip');direct.textContent='临时直接打开飞书';direct.onclick=()=>localNavigate(target.href);box.append(title,text,install,direct);root.append(box);return false;}const frame=document.createElement('iframe');frame.title=kind==='tasks'?'飞书公司任务总栏':kind==='content'?'飞书内容项目':kind==='videoScripts'?'飞书视频脚本':kind==='articleScripts'?'飞书文章脚本':kind==='reviews'?'飞书内容数据复盘':kind==='bar'?'飞书活动运营活动运营':'飞书活动资料库';frame.src=target.href;frame.referrerPolicy='strict-origin-when-cross-origin';frame.setAttribute('allow','clipboard-read; clipboard-write');Object.assign(frame.style,{display:'block',width:'100%',height:'100%',border:'0',background:'#fff'});root.append(frame);feishuFrames.set(kind,{frame,target:target.href});return true;}
 const embeddedPane=(kind,mountId)=>({open:async()=>{const [l,enabled]=await Promise.all([getLinks(),embedEnabled()]),url=l[kind],ok=mountFeishu(kind,mountId,url,enabled);emit(paneStateChannel(kind),{phase:ok?'ready':'blocked',configured:true,hostname:new URL(url).hostname,message:ok?'已在员工本地浏览器内嵌飞书':'需要 Chrome / Edge 内嵌桥扩展',canGoBack:false});return ok;},hide:async()=>{const item=feishuFrames.get(kind);if(item){item.frame.remove();feishuFrames.delete(kind);}return true;},bounds:async()=>true,command:async command=>{const item=feishuFrames.get(kind);if(!item){const [l,enabled]=await Promise.all([getLinks(),embedEnabled()]);return mountFeishu(kind,mountId,l[kind],enabled);}item.frame.src=command==='home'?item.target:item.frame.src;return true;},onState:fn=>on(paneStateChannel(kind),fn),state:async()=>{const enabled=await embedEnabled();return {phase:enabled?'ready':'blocked',configured:true,message:enabled?'飞书内嵌桥已启用':'需要安装飞书内嵌桥'};}});
 let websiteFrame=null;
 function mountWebsite(){const root=document.getElementById('website-mount');if(!root)return false;root.replaceChildren();const frame=document.createElement('iframe');frame.title='ERC Studio官网';frame.src='/?embedded_site=1';frame.referrerPolicy='strict-origin-when-cross-origin';frame.setAttribute('sandbox','allow-scripts allow-same-origin allow-forms allow-popups allow-downloads');Object.assign(frame.style,{display:'block',width:'100%',height:'100%',border:'0',background:'#fff'});root.append(frame);websiteFrame=frame;return true;}
 async function snapshot(kind,days,refresh=false){return request('/v1/workspace/snapshot?'+new URLSearchParams({kind,...(days?{days}:{}),...(refresh?{refresh:'1'}:{})}));}
 async function accounts(){if(accountsInFlight)return accountsInFlight;accountsInFlight=snapshot('accounts').then(value=>accountSnapshot=value).finally(()=>accountsInFlight=null);return accountsInFlight;}
 async function cachedContent(){return contentSnapshot||(contentSnapshot=await snapshot('content'));}
 async function refreshContent(){if(contentRefreshInFlight)return contentRefreshInFlight;contentRefreshInFlight=snapshot('content',undefined,true).then(value=>{contentSnapshot=value;emit('content-state',value);return value;}).finally(()=>contentRefreshInFlight=null);return contentRefreshInFlight;}
 async function content(){const value=await cachedContent();void refreshContent().catch(()=>{});return value;}
 // A route-specific video limit matches the backend; legacy proxies can fall
 // back once after a definite 413 without replacing the job or replaying edits.
 const productionUploadChunk=524288;
 const production={
  engine:()=>request('/v1/production/engine'),
  start:(payload,key)=>request('/v1/production/jobs',{method:'POST',body:payload,key}),
  status:id=>request('/v1/production/jobs/'+id),
  confirm:id=>request('/v1/production/jobs/'+id+'/confirm',{method:'POST',body:{},key:uuid()}),
  submit:id=>request('/v1/production/jobs/'+id+'/start',{method:'POST',body:{},key:uuid()}),
  respond:(id,text,key)=>request('/v1/production/jobs/'+id+'/respond',{method:'POST',body:{text},key}),
  list:id=>request('/v1/production/projects/'+id+'/jobs'),
  upload:async(id,file,onProgress=()=>{})=>{
   let state=await request('/v1/production/jobs/'+id),chunkSize=productionUploadChunk;
   if(state.state!=='uploading')return state;
   if(state.received>0){
    let same=false;
    for(const length of new Set([Math.min(file.size,65536),Math.min(file.size,productionUploadChunk)])){
     const first=await file.slice(0,length).arrayBuffer(),digest=await crypto.subtle.digest('SHA-256',first),hash=Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join('');
     if(hash===state.sourceFirstChunkSha256){same=true;break;}
    }
    if(!same)throw Error('重新选择的原片与原任务不同，请核对原文件');
   }
   for(let offset=state.received;offset<file.size;){
    const chunk=file.slice(offset,Math.min(offset+chunkSize,file.size)),bytes=await chunk.arrayBuffer(),digest=await crypto.subtle.digest('SHA-256',bytes),hash=Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join(''),controller=new AbortController(),deadline=setTimeout(()=>controller.abort(),30000);
    let response,result;
    try{
     response=await fetch('/v1/production/jobs/'+id+'/source',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-Upload-Offset':String(offset),'X-Chunk-SHA256':hash},body:chunk,signal:controller.signal});
     result=await response.json().catch(()=>null);
    }catch(error){
     if(error.name==='AbortError')throw Error('上传分片超过30秒未确认；原任务已保留，重新查询后继续上传');
     throw error;
    }finally{clearTimeout(deadline);}
    if(response.status===413&&chunkSize>65536){chunkSize=65536;continue;}
    if(!response.ok){
     const error=new Error(response.status===413?'网页入口拒绝64 KB上传分片（HTTP 413）；原任务仍保留':result?.message||'上传请求未完成（HTTP '+response.status+'）');
     error.code=result?.code||'upload_http_error';error.status=response.status;throw error;
    }
    if(!result||!Number.isSafeInteger(result.received)||result.received!==offset+chunk.size)throw Error('上传进度未确认；请查询原任务后继续');
    offset=result.received;onProgress(offset,file.size);state=result;
   }
   return state;
  }
 };
 const readonly=async()=>{throw Error('办公室网页为只读入口；请在飞书原表完成此操作');};
 const analytics=window.StudioAnalyticsTransport.create({request,emit:value=>emit('analytics-state',value),uuid});
 new MutationObserver(()=>{if(document.body.classList.contains('studio-auth-locked'))analytics.reset();}).observe(document.body,{attributes:true,attributeFilter:['class']});
 const pane=(kind,mountId,title,message)=>({open:async()=>{const l=await getLinks(),url=l[kind];mount(mountId,title,message,url);emit(kind+'-state',{phase:'ready',configured:true,hostname:new URL(url).hostname,message:'已准备本人原生网页入口'});return true;},hide:async()=>true,bounds:async()=>true,command:async()=>{const l=await getLinks();external(l[kind]);return true;},onState:fn=>on(kind+'-state',fn)});
 const studio={
  workflow:async input=>{if(!input||typeof input!=='object'||Array.isArray(input)||Object.keys(input).some(k=>!['operation','id'].includes(k))||!['snapshot','refresh','detail'].includes(input.operation))throw Error('workflow_input_invalid');try{const value=await request('/v1/workflows/snapshot'+(input.operation==='refresh'?'?refresh=1':''));if(input.operation==='detail'){const item=value.data?.items.find(x=>x.id===input.id);if(!item)throw Error('workflow_id_invalid');return {...value,data:{item,observedAt:value.data.observedAt},history:(value.history||[]).filter(e=>e.id===input.id)};}return value;}catch(e){return {ok:false,phase:'error',error:e.code||'workflow_source_unavailable',message:e.status===403?'当前成员没有工作流维护查看权限':e.status===401?'请登录后查看工作流状态':'工作流状态暂未读取',readOnly:true};}},
  conversation:{call:conversation},home:async()=>true,homeModel:async()=>({bytes:new Uint8Array(await(await fetch('/assets/model.glb')).arrayBuffer())}),
  tasks:embeddedPane('tasks','task-mount'),
  website:{open:async()=>{const l=await getLinks();mountWebsite();emit('website-state',{phase:'ready',configured:true,hostname:new URL(l.website).hostname,message:'官网已在当前页面内嵌',canGoBack:true});return true;},hide:async()=>true,bounds:async()=>true,command:async command=>{if(!websiteFrame)mountWebsite();if(command==='reload')websiteFrame.contentWindow.location.reload();else if(command==='back')websiteFrame.contentWindow.history.back();else websiteFrame.contentWindow.location.href='/?embedded_site=1';return true;},onState:fn=>on('website-state',fn),state:async()=>({phase:'ready',configured:true,message:'办公室内嵌入口'})},
  content:{project:embeddedPane('content','content-project-mount'),videoScripts:embeddedPane('videoScripts','content-video-script-mount'),articleScripts:embeddedPane('articleScripts','content-article-script-mount'),query:async input=>{const s=await cachedContent(),l=await getLinks(),catalog=window.StudioContentCatalog.build(s,{baseURL:l.content,tables:{projects:''}});return window.StudioContentCatalog.query(catalog,input);},open:content,read:refreshContent,createTopic:readonly,editTopic:readonly,decideTopic:readonly,createProject:readonly,saveDraft:readonly,advance:readonly,addPublication:readonly,syncMetrics:readonly,saveReview:readonly,generate:readonly,verify:readonly,pendingResult:readonly,resumeJob:readonly,openDocument:async()=>external((await getLinks()).content),openSource:async()=>external((await getLinks()).content),onState:fn=>on('content-state',fn),onHide:fn=>on('content-hide',fn)},dataReview:embeddedPane('reviews','data-review-mount'),production,
  analytics:{...analytics,onState:fn=>on('analytics-state',fn)},
  radar:{open:async()=>request('/v1/radar/snapshot'),read:async()=>request('/v1/radar/snapshot'),refresh:async()=>request('/v1/radar/snapshot?refresh=1'),article:async(key,kind)=>request('/v1/radar/article',{method:'POST',body:{key,kind}})},
  bar:{activity:embeddedPane('bar','bar-native-mount'),library:embeddedPane('barFolder','task-mount'),read:()=>snapshot('bar'),openSource:async value=>external((await getLinks())[value?.kind==='folder'?'barFolder':'bar'])},
  list:accounts,refresh:async()=>{accountSnapshot=null;return accounts();},add:readonly,update:readonly,group:readonly,assign:readonly,
  open:async id=>{const state=await accounts(),account=state.accounts.find(x=>x.id===id),p=state.platforms.find(x=>x.id===account?.platform);if(!p)throw Error('账号入口不存在');mount('browser-mount',account.name,'使用你自己的浏览器会话登录 '+p.name+'；服务器不保存Cookie。',p.url);emit('browser',{phase:'ready',message:'已准备 '+p.name+' 原生入口',hostname:new URL(p.url).hostname});return {id,platform:p.id};},
  close:async()=>true,bounds:async()=>true,command:async()=>true,onChange:fn=>on('changed',fn),onBrowser:fn=>on('browser',fn)
 };
 window.studio=studio;
 function bindBarEmbedding(){
  const root=document.getElementById('bar-workbench');if(!root||root.dataset.officeActivityEmbed==='bound')return;root.dataset.officeActivityEmbed='bound';
  const layout=document.getElementById('bar-layout'),home=document.getElementById('bar-home'),header=root.querySelector('.content-header'),tabs=root.querySelector('.content-tabs'),footer=root.querySelector('.content-source-footer'),actor=document.getElementById('bar-actor'),refresh=document.getElementById('bar-refresh');
  const mount=document.createElement('div');mount.id='bar-native-mount';Object.assign(mount.style,{flex:'1',minHeight:'0',width:'100%',overflow:'hidden'});mount.hidden=true;header.after(mount);
  let scene=null,epoch=0;const oldOpen=window.openStudioBar,oldHide=window.hideStudioBar,oldRefresh=refresh.onclick;
  const buttons=Array.from(root.querySelectorAll('[data-bar-tab]')),review=buttons.find(b=>b.dataset.barTab==='reviews'),oldReview=review?.onclick;
  function clear(){epoch++;scene=null;mount.hidden=true;studio.bar.activity.hide();if(footer)footer.hidden=false;}
  function select(value){for(const b of buttons)b.classList.toggle('active',b.dataset.barTab===value);document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='bar'));document.querySelectorAll('.website-subnav button').forEach(b=>b.classList.toggle('active',b.dataset.barPage==='workbench'));}
  function openActivity(value){return window.safely(async()=>{await studio.close();window.hideStudioContent?.();window.hideStudioAnalytics?.();window.hideStudioHome?.();window.hideStudioTasks?.();window.hideStudioWebsite?.();for(const id of ['account-page','group-page','browser-page']){const el=document.getElementById(id);if(el)el.hidden=true;}root.hidden=false;document.querySelector('.topbar').hidden=true;scene=value;const ticket=++epoch;layout.hidden=true;home.hidden=true;mount.hidden=false;tabs.hidden=false;if(footer)footer.hidden=true;actor.textContent='飞书原表 · 身份与权限以本人登录为准';refresh.disabled=false;select(value);const ok=await studio.bar.activity.open();if(ticket!==epoch)return;return ok;});}
  window.openStudioBar=mode=>{if(mode==='home'){clear();return oldOpen(mode);}return openActivity('projects');};
  window.hideStudioBar=()=>{clear();return oldHide();};
  for(const b of buttons)if(['topics','projects'].includes(b.dataset.barTab))b.onclick=()=>openActivity(b.dataset.barTab);
  if(review)review.onclick=()=>window.safely(async()=>{clear();await oldOpen('workbench');return oldReview?.();});
  refresh.onclick=()=>scene?window.safely(()=>studio.bar.activity.command('reload')):oldRefresh?.();
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bindBarEmbedding,{once:true});else bindBarEmbedding();
})();
