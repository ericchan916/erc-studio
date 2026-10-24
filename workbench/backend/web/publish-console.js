'use strict';
// Provider-origin sessions remain in the user's existing local browser; no credentials enter this host.
(()=>{
 const nav=document.querySelector('[data-nav="publish"]');if(!nav)return;
 const wechatContainerOrigin='chrome-extension://kecdiclhheadhligeohfbaboikcdkdpp';
 const wechatContainerUrl=wechatContainerOrigin+'/publisher-host.html';
 const targets={yxer:{label:'蚁小二',url:'https://www.yixiaoer.cn/web/publish',origin:'https://www.yixiaoer.cn'},wechat:{label:'公众号',url:'https://mp.weixin.qq.com/',origin:'https://mp.weixin.qq.com'},x:{label:'X',url:'https://x.com/compose/post',origin:'https://x.com'}};
 const make=(tag,cls)=>{const n=document.createElement(tag);if(cls)n.className=cls;return n;};
 const root=make('section','publish-console publish-official');root.id='publish-console';root.hidden=true;
 const tabs=make('nav','publish-destination-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','发布平台');
 const status=make('p','publish-embed-status'),mount=make('div','publish-embed-mount');status.hidden=true;status.setAttribute('role','status');
 root.append(tabs,status,mount);document.querySelector('main').append(root);
 const frames=new Map(),buttons=new Map();let active='yxer';
 const locked=()=>document.body.classList.contains('studio-auth-locked');
 function error(message){status.textContent=message;status.hidden=false;}
 function clear(){for(const item of frames.values()){clearTimeout(item.timer);item.frame.onload=null;item.frame.onerror=null;}mount.replaceChildren();frames.clear();status.hidden=true;status.textContent='';active='yxer';}
 function hide(){root.hidden=true;status.hidden=true;} // Keep each official editor and its unsent draft while switching studio modules.
 function yxerEnvironmentReady(){
  const value=document.documentElement.dataset.cyberYxerEmbedVersion||'',parts=value.split('.').map(Number);
  return document.documentElement.dataset.cyberYxerEmbed==='enabled'&&parts.length===3&&parts.every(Number.isFinite)&&(parts[0]>0||parts[1]>3||parts[1]===3&&parts[2]>=2);
 }
 function makeYxerSetup(){
  const box=make('section','yxer-first-use'),card=make('div','yxer-first-use-card'),title=make('h2'),lead=make('p'),steps=make('ol'),actions=make('div','yxer-first-use-actions');
  title.textContent='首次使用蚁小二';lead.textContent='这台电脑尚未检测到完整的内嵌桥。请使用办公室入口 studio.example.invalid；飞书登录不会自动登录蚁小二。';
  for(const text of['使用桌面 Chrome 或 Edge，下载并解压最新内嵌桥（包含蚁小二首次登录修复）。','在浏览器扩展页更新已有内嵌桥目录并重载；没有旧版才加载新目录，避免重复安装。','在内嵌页点击“登录蚁小二”，本人完成认证后返回；工作室团队账号需在蚁小二中获得成员权限。']){const li=make('li');li.textContent=text;steps.append(li);}
  const download=make('a','yxer-first-use-primary');download.href='/studio-feishu-embed.zip';download.setAttribute('download','studio-feishu-embed.zip');download.textContent='下载内嵌桥';
  const reload=make('button','yxer-first-use-secondary');reload.type='button';reload.textContent='已安装，刷新检查';reload.onclick=()=>location.reload();actions.append(download,reload);card.append(title,lead,steps,actions);box.append(card);return box;
 }
 function setupYxer(){const box=makeYxerSetup();frames.set('yxer',{frame:box,error:null,timer:null,setup:true});mount.append(box);}
 window.StudioYxerEnvironment={ready:yxerEnvironmentReady,createSetup:makeYxerSetup};
 function select(id){
  if(locked()||root.hidden||!Object.hasOwn(targets,id))return;
  active=id;status.hidden=true;status.textContent='';
  for(const [key,b]of buttons){const chosen=key===id;b.classList.toggle('active',chosen);b.setAttribute('aria-selected',String(chosen));b.tabIndex=chosen?0:-1;}
  for(const [key,item]of frames)item.frame.hidden=key!==id;
  if(frames.has(id)){const item=frames.get(id);if(item.setup&&yxerEnvironmentReady()){item.frame.remove();frames.delete(id);}else{if(item.error)error(item.error);return;}}
  if(id==='yxer'&&!yxerEnvironmentReady()){setupYxer();return;}
  if(id!=='yxer'&&document.documentElement.dataset.cyberPublisherEmbed!=='enabled'){error('请先更新并重载本机内嵌桥0.3.0，再使用公众号与X页签。');return;}
  if(id==='wechat'&&document.documentElement.dataset.cyberWechatContainerUrl!==wechatContainerUrl){error('公众号需内嵌桥0.4.0的本机浏览器容器，请更新并重载扩展。');return;}
  const target=targets[id],frame=make('iframe','publish-official-frame'),item={frame,error:null,timer:null};
  frame.id='publish-pane-'+id;frame.title=target.label+'官方发布页面';frame.src=id==='wechat'?wechatContainerUrl:target.url;frame.referrerPolicy='strict-origin-when-cross-origin';
  frame.setAttribute('sandbox','allow-storage-access-by-user-activation allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads');
  frame.setAttribute('role','tabpanel');frame.setAttribute('aria-labelledby','publish-tab-'+id);
  if(id==='wechat'){item.bridgeOrigin=wechatContainerOrigin;frame.onload=()=>{if(frames.get(id)===item&&!locked())frame.contentWindow.postMessage({type:'studio-wechat-host-init'},wechatContainerOrigin);};}
  frame.onerror=()=>{if(frames.get(id)!==item)return;item.error=target.label+'页面加载异常，请检查本机内嵌桥。';if(active===id&&!root.hidden)error(item.error);};
  frames.set(id,item);mount.append(frame);
  if(id==='wechat')item.timer=setTimeout(()=>{if(frames.get(id)!==item||item.hostLoaded)return;item.error='公众号本机容器尚未回应，请检查扩展0.4.0及刷新工作室。';if(active===id&&!root.hidden&&!locked())error(item.error);},15000);
  if(id==='yxer')item.timer=setTimeout(()=>{if(frames.get(id)!==item)return;item.error='蚁小二内嵌认证未完成，请检查本机扩展及本人登录。';if(active===id&&!root.hidden&&!locked())error(item.error);},15000);
 }
 for(const [id,target]of Object.entries(targets)){
  const b=make('button');b.id='publish-tab-'+id;b.type='button';b.textContent=target.label;b.setAttribute('role','tab');b.setAttribute('aria-controls','publish-pane-'+id);b.onclick=()=>select(id);
  b.onkeydown=e=>{const keys=Object.keys(targets),index=keys.indexOf(id);if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const next=e.key==='Home'?keys[0]:e.key==='End'?keys.at(-1):keys[(index+(e.key==='ArrowRight'?1:-1)+keys.length)%keys.length];select(next);buttons.get(next).focus();};
  tabs.append(b);buttons.set(id,b);
 }
 nav.disabled=false;nav.title='蚁小二 / 公众号 / X 发布';
 nav.onclick=()=>{
  if(locked())return;
  for(const f of ['hideStudioHome','hideStudioTasks','hideStudioContent','hideStudioAnalytics','hideStudioWebsite','hideStudioBar'])window[f]?.();
  for(const section of document.querySelectorAll('main > section'))if(section!==root)section.hidden=true;
  document.querySelector('.topbar').hidden=true;document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b===nav));root.hidden=false;select(active);
 };
 document.addEventListener('click',e=>{const other=e.target.closest('[data-nav],[data-analytics-page],[data-website-page]');if(other&&other!==nav)hide();},true);
 new MutationObserver(()=>{if(locked()){hide();clear();}}).observe(document.body,{attributes:true,attributeFilter:['class']});
 window.addEventListener('message',event=>{
  const wx=frames.get('wechat');
  if(wx&&!locked()&&event.source===wx.frame.contentWindow&&event.origin===wx.bridgeOrigin&&event.data?.type==='studio-wechat-host-loaded'){clearTimeout(wx.timer);wx.hostLoaded=true;wx.error=null;if(active==='wechat'){status.hidden=true;status.textContent='';}return;}
  if(wx&&!locked()&&event.source===wx.frame.contentWindow&&event.origin===wx.bridgeOrigin&&event.data?.type==='studio-wechat-host-error'){wx.error='公众号本机浏览器容器未就绪，请重载内嵌桥。';if(active==='wechat'&&!root.hidden)error(wx.error);return;}
  if(wx&&!locked()&&event.source===wx.frame.contentWindow&&event.origin===wx.bridgeOrigin&&event.data?.type==='studio-wechat-qr-diagnostic'){
   const data=event.data.data;
   if(!data||typeof data!=='object'||!['startlogin','image'].includes(data.phase))return;
   const integer=v=>typeof v==='number'&&Number.isInteger(v)&&Number.isFinite(v)?String(v):'未知';
   if(data.phase==='startlogin'){
    wx.diagnostic={httpStatus:integer(data.httpStatus),ret:integer(data.ret)};
    if(data.httpStatus===200&&data.ret===0){wx.error=null;if(active==='wechat'){status.hidden=true;status.textContent='';}}
    else{wx.error='公众号扫码初始化失败：HTTP '+wx.diagnostic.httpStatus+' / ret '+wx.diagnostic.ret+'。这是实际响应，不等同于Cookie过期。';if(active==='wechat'&&!root.hidden)error(wx.error);}
   }else{
    wx.error='公众号二维码图片加载失败。扫码初始化：HTTP '+(wx.diagnostic?.httpStatus||'未知')+' / ret '+(wx.diagnostic?.ret||'未知')+'。';if(active==='wechat'&&!root.hidden)error(wx.error);
   }
   return;
  }
  const item=frames.get('yxer');if(!item||item.setup||locked()||event.source!==item.frame.contentWindow||event.origin!==targets.yxer.origin)return;
  const data=event.data;if(!data||typeof data!=='object')return;
  if(data.type==='auth-ready'){item.frame.contentWindow.postMessage({type:'studio-yxer-auth-ready'},targets.yxer.origin);clearTimeout(item.timer);item.error=null;if(active==='yxer'){status.hidden=true;status.textContent='';}}
  else if(data.type==='auth-failed'){item.frame.contentWindow.postMessage({type:'studio-yxer-auth-failed'},targets.yxer.origin);clearTimeout(item.timer);item.error=null;if(active==='yxer'&&!root.hidden)status.hidden=true;}
  else if(data.type==='studio-yxer-bridge'&&data.state==='auth-submitted'){clearTimeout(item.timer);item.timer=setTimeout(()=>{if(frames.get('yxer')!==item||locked())return;item.frame.contentWindow.postMessage({type:'studio-yxer-auth-failed'},targets.yxer.origin);},30000);}
  else if(data.type==='studio-yxer-bridge'&&['available','browser-login-required','login-window-opened'].includes(data.state)){clearTimeout(item.timer);item.error=null;if(active==='yxer')status.hidden=true;}
 });
 window.addEventListener('pagehide',clear);
})();
