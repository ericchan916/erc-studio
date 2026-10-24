'use strict';
// Browser-only row controls. IDs are resolved by the trusted backend, not by account names.
(()=>{
 if(!window.render||!window.studio)return;
 const labels={healthy:'正常',expired:'需重新授权',unknown:'待核验',disabled:'未启用',unbound:'未关联'};
 const errors={account_authorization_permission_denied:'当前成员没有本机账号授权操作权限。',scientist_binding_missing:'尚未关联科学家账号，请先由维护方核对账号ID。',scientist_account_disabled:'该采集账号未启用，保留当前配置。',authorization_existing_job:'该账号已有授权任务，请先核验原任务。',authorization_preview_expired:'确认预览已过期，请重新打开。',authorization_binding_changed:'账号绑定已变化，请重新核对。'};
 const make=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
 let catalog=new Map(),busy=null,epoch=0,last=0,denied=false,refreshTimer=null,current=null,frame=null,poll=null;
 const modal=make('dialog','account-authorization-dialog');modal.setAttribute('aria-label','账号授权管理');document.body.append(modal);
 const locked=()=>document.body.classList.contains('studio-auth-locked');
 async function request(path,method='GET',body,headers={}){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),35000);
  try{const r=await fetch('/v1/account-authorizations'+path,{method,credentials:'same-origin',signal:controller.signal,headers:{...(body?{'Content-Type':'application/json'}:{}),...headers},...(body?{body:JSON.stringify(body)}:{})});const p=await r.json();if(!r.ok){const e=Error(errors[p.code]||p.code||'请求未完成');e.code=p.code;e.status=r.status;throw e;}return p;}finally{clearTimeout(timer);}
 }
 const glyph=name=>{const img=make('img','authorization-icon');img.src='/vendor/tabler-content/'+name+'.svg';img.alt='';return img;};
 function checkedTime(value){const raw=typeof value==='number'&&value<1000000000000?value*1000:value;const date=new Date(raw);return Number.isFinite(date.getTime())?date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'未提供';}
 function line(kind,info){const n=make('div','authorization-status');n.title=kind==='publish'?'来源：蚁小二CLI账号状态；不代表创作者后台网页已登录':info?.checkedAt?'科学家最近检查：'+checkedTime(info.checkedAt)+'；正常同时要求身份匹配与检查仍有效':'科学家授权与账号身份尚未核验';n.append(make('span','authorization-status-label',kind==='publish'?'发布':'采集'),make('i','authorization-dot '+(info?.state||'unknown')),make('span','authorization-status-value',labels[info?.state]||'待核验'));return n;}
 function button(kind,a,info){const b=make('button','authorization-entry '+(kind==='publish'?'is-publish':'is-collect'));b.type='button';b.append(glyph(kind==='publish'?'external-link':'search'),make('span','',kind==='publish'?'发布授权':'采集授权'));b.setAttribute('aria-label',a.name+' '+(kind==='publish'?'发布授权':'采集授权'));b.disabled=locked()||denied||!info||(kind==='publish'?!info.canOpen:!info.canLogin);b.title=denied?'当前成员未开放本机授权操作':kind==='publish'?'蚁小二平台发布授权':info?.state==='disabled'?'采集账号尚未启用':info?.state==='unbound'?'缺少科学家准确账号映射':'数据科学家独立采集授权';b.onclick=()=>open(a,kind);return b;}
 function paint(){
  if(typeof state==='undefined'||state.browserMode!=='web_native')return;
  const rows=[...document.querySelectorAll('#account-rows > tr')],cards=[...document.querySelectorAll('#grid-cards > .grid-card')];
  for(const [nodes,card]of[[rows,false],[cards,true]])for(const n of nodes){
   let account=null;
   if(!card){const visible=window.rows().slice((page-1)*($('page-size').selectedIndex===0?30:60),page*($('page-size').selectedIndex===0?30:60));account=visible[nodes.indexOf(n)];}
   else{const visible=window.rows().slice((page-1)*($('page-size').selectedIndex===0?30:60),page*($('page-size').selectedIndex===0?30:60));account=visible[nodes.indexOf(n)];}
   if(!account)continue;n.dataset.authorizationAccount=account.id;
   const info=catalog.get(account.id);const target=card?n.children[1]:n.querySelector('.account-status-cell');
   if(!target)continue;
   target.replaceChildren(line('publish',info?.publish),line('collect',info?.collect));if(card)target.classList.add('authorization-card-status');
   const ops=card?n:n.querySelector('.account-actions-cell');ops?.querySelector('.account-authorization-actions')?.remove();const actions=make('div','account-authorization-actions');actions.append(button('publish',account,info?.publish),button('collect',account,info?.collect));if(card)ops.append(actions);else ops?.prepend(actions);
  }
  document.querySelector('.account-table .actions-cell')?.classList.add('authorization-actions-head');
 }
 async function load(force=false){
  if(locked())return;if(busy)return busy;if(!force&&Date.now()-last<30000)return;
  const ticket=epoch;
  busy=request('').then(v=>{if(ticket!==epoch||locked())return;catalog=new Map(v.items.map(x=>[x.accountId,x]));denied=false;last=Date.now();window.render();}).catch(e=>{if(ticket!==epoch)return;catalog.clear();denied=e.status===403;last=Date.now();paint();}).finally(()=>{if(ticket===epoch)busy=null;});return busy;
 }
 const filter=document.getElementById('status-filter');
 if(filter&&window.rows){
  const options=[['all','全部授权状态'],['publish:healthy','发布 · 正常'],['publish:expired','发布 · 需授权'],['collect:healthy','采集 · 正常'],['collect:unknown','采集 · 待核验'],['collect:disabled','采集 · 未启用'],['collect:unbound','采集 · 未关联']];
  filter.replaceChildren(...options.map(([value,label])=>new Option(label,value)));filter.setAttribute('aria-label','授权状态');
  const originalRows=window.rows;
  window.rows=function(){const value=filter.value;filter.value='all';let list;try{list=originalRows();}finally{filter.value=value;}if(value==='all')return list;const[kind,status]=value.split(':');return list.filter(a=>catalog.get(a.id)?.[kind]?.state===status);};
 }
 const render=window.render;window.render=function(){render();paint();if(typeof state!=='undefined'&&state.browserMode==='web_native'){clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>load(),0);}};
 function dismiss(){clearTimeout(poll);poll=null;if(frame){frame.remove();frame=null;}current=null;modal.close();modal.replaceChildren();}
 modal.addEventListener('cancel',e=>{e.preventDefault();dismiss();});modal.addEventListener('close',()=>{clearTimeout(poll);frame?.remove();frame=null;current=null;});
 function skeleton(a,kind){
  dismiss();modal.classList.toggle('is-collect',kind==='collect');const header=make('header','authorization-dialog-header'),identity=make('div'),eyebrow=make('span','authorization-eyebrow',kind==='publish'?'发布授权 · 蚁小二':'采集授权 · 数据科学家');identity.append(eyebrow,make('h2','',a.name));const platform=typeof state!=='undefined'?state.platforms.find(p=>p.id===a.platform)?.name:a.platform;identity.append(make('p','authorization-subtitle',platform+' · 两套授权独立维护'));
  const close=make('button','authorization-close');close.append(glyph('x'));close.setAttribute('aria-label','关闭授权窗口');close.onclick=dismiss;header.append(identity,close);modal.append(header);modal.showModal();return epoch;
 }
 function problem(text){const p=make('p','authorization-error',text);p.setAttribute('role','alert');modal.append(p);}
 async function open(a,kind){
  if(locked())return;const info=catalog.get(a.id);if(!info)return;const ticket=skeleton(a,kind);current={a,kind,ticket};
  if(kind==='publish'){
   const environment=window.StudioYxerEnvironment;if(!environment?.ready()){if(environment)modal.append(environment.createSetup());else problem('本机尚未检测到蚁小二内嵌桥，请先进入发布页更新扩展后重试。');return;}
   const note=make('div','authorization-source-note');note.append(make('span','', '请在蚁小二原页核对所选账号后授权。'),make('code','', '来源账号 ID '+info.publish.sourceId));modal.append(note);
   frame=make('iframe','authorization-official-frame');frame.title='蚁小二账号授权';frame.src=info.publish.url;frame.referrerPolicy='strict-origin-when-cross-origin';frame.setAttribute('sandbox','allow-storage-access-by-user-activation allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox');modal.append(frame);return;
  }
  const content=make('div','authorization-collect-body');modal.append(content);const text=make('p','authorization-flow-copy','正在核对准确账号映射与登录预览…');content.append(text);
  try{
   const operation=info.collect.operation;
   const plan=operation?await request('/'+operation.id):await request('/preview','POST',{accountId:a.id},{'Idempotency-Key':crypto.randomUUID()});
   if(ticket!==epoch||!current||current.a.id!==a.id||locked())return;current.plan=plan;text.textContent='将在这台 Mac 打开此账号的科学家登录窗口，由本人扫码或验证。';
   const box=make('div','authorization-confirm-summary');box.append(make('strong','',a.name),make('span','', '只更新采集授权，不影响蚁小二发布授权'),make('span','', '保留原浏览器空间 · 不启动采集 · 不同步飞书'));content.append(box);
   const status=make('p','authorization-job-status');status.setAttribute('role','status');content.append(status);const footer=make('footer','authorization-dialog-footer'),cancel=make('button','authorization-secondary','关闭'),confirm=make('button','authorization-primary','确认打开采集授权窗口');cancel.onclick=dismiss;footer.append(cancel,confirm);content.append(footer);
   function view(v){status.textContent=({preview:'等待本人确认',accepted:'窗口启动请求已受理，等待本人完成登录',running:'正在等待本人扫码或验证码',succeeded:'采集授权与账号身份已核验',verification_required:'流程结束，授权或账号身份仍需核验',failed:'授权请求未完成，请检查科学家原任务',execution_unknown:'结果待核验，保留原任务，不重复启动',submitting:'正在提交原授权请求'})[v.state]||'状态待核验';confirm.hidden=v.state!=='preview';}
   async function pollStatus(){clearTimeout(poll);if(!current?.plan||modal.open===false||ticket!==epoch)return;const id=current.plan.id;try{const result=await request('/'+id);if(!current||current.plan.id!==id||ticket!==epoch)return;current.plan=result;view(result);if(['accepted','running','submitting'].includes(result.state))poll=setTimeout(pollStatus,2500);else await load(true);}catch(e){if(current&&ticket===epoch)status.textContent='状态查询中断，原任务保留。';}}
   view(plan);if(plan.state!=='preview')pollStatus();
   confirm.onclick=async()=>{if(!current?.plan||confirm.disabled||ticket!==epoch)return;confirm.disabled=true;const id=current.plan.id,confirmation=current.plan.confirmation;try{const result=await request('/'+id+'/submit','POST',{approved:true,confirmation});if(!current||current.plan.id!==id||ticket!==epoch)return;current.plan=result;view(result);pollStatus();}catch(e){if(current&&ticket===epoch){confirm.hidden=true;status.textContent='提交结果待核验，请查询原任务，不重复启动。';poll=setTimeout(pollStatus,1000);}}};
  }catch(e){if(ticket===epoch&&current)problem(e.message);}
 }
 new MutationObserver(()=>{if(locked()){epoch++;catalog.clear();busy=null;last=0;clearTimeout(refreshTimer);dismiss();paint();}}).observe(document.body,{attributes:true,attributeFilter:['class']});
 window.addEventListener('pagehide',dismiss);window.StudioAccountAuthorization={load,open};paint();load();
})();
