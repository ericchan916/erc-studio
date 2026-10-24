'use strict';
// Local extension document owns same-provider navigation. No cookie API, credential extraction, server URL relay or posting automation.
(()=>{
 const studio='https://studio.example.invalid',provider='https://mp.weixin.qq.com';
 if(location.protocol!=='chrome-extension:'||window.parent===window)return;
 const state=document.getElementById('state'),mount=document.getElementById('mount');let frame=null,dead=false,pending=false,lastNavigationNonce=null;
 const integer=value=>typeof value==='number'&&Number.isInteger(value)&&Number.isFinite(value)?value:null;
 function emit(type,data){if(!dead)window.parent.postMessage({type,data},studio);}
 window.addEventListener('message',event=>{
  if(dead)return;
  if(event.source===window.parent&&event.origin===studio&&event.data?.type==='studio-wechat-host-init'){
   if(frame||pending)return;pending=true;
   chrome.runtime.sendMessage({type:'studio-wechat-host-check'}).then(async result=>{
   if(result?.ok===true)result=await chrome.runtime.sendMessage({type:'studio-wechat-session-check'});
   if(dead)return;
   if(result?.ok!==true){pending=false;state.textContent='本机公众号容器规则尚未启用，请重载内嵌桥。';emit('studio-wechat-host-error');return;}
   frame=document.createElement('iframe');frame.title='微信公众号官方后台';frame.src=provider+'/';frame.referrerPolicy='strict-origin-when-cross-origin';
   frame.setAttribute('sandbox','allow-storage-access-by-user-activation allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads');
   frame.onload=()=>{state.hidden=true;frame.contentWindow.postMessage({type:'studio-wechat-session-enable'},provider);frame.contentWindow.postMessage({type:'studio-wechat-navigation-enable'},provider);emit('studio-wechat-host-loaded');};
   frame.onerror=()=>{state.hidden=false;state.textContent='公众号官方页面加载异常；本人登录与Cookie保持原样。';emit('studio-wechat-host-error');};
   mount.append(frame);pending=false;
   }).catch(()=>{if(dead)return;pending=false;state.textContent='本机扩展连接失败，请重载内嵌桥。';emit('studio-wechat-host-error');});return;
  }
  if(!frame||event.source!==frame.contentWindow||event.origin!==provider)return;
  if(event.data?.type==='studio-wechat-session-ready'){frame.contentWindow.postMessage({type:'studio-wechat-session-enable'},provider);return;}
  if(event.data?.type==='studio-wechat-navigation-ready'){frame.contentWindow.postMessage({type:'studio-wechat-navigation-enable'},provider);return;}
  if(event.data?.type==='studio-wechat-navigate'){
   const {url,nonce}=event.data;if(typeof url!=='string'||url.length>16384||typeof nonce!=='string'||!/^wechat-nav-[a-z0-9-]{1,80}$/.test(nonce)||nonce===lastNavigationNonce)return;
   try{const target=new URL(url);if(target.origin!==provider||target.protocol!=='https:'||target.username||target.password||/(?:login|logout|oauth|captcha|authorize|download)/i.test(target.pathname)||/^(?:login|logout|authorize|download)$/i.test(target.searchParams.get('action')||''))return;lastNavigationNonce=nonce;frame.src=target.href;}catch{}
   return; // Official query values are not forwarded to Studio or persisted.
  }
  if(event.data?.type!=='studio-wechat-qr-diagnostic')return;
  const data=event.data.data;if(!data||!['startlogin','image'].includes(data.phase))return;
  emit('studio-wechat-qr-diagnostic',{phase:data.phase,httpStatus:integer(data.httpStatus),ret:integer(data.ret),kind:['network','response','non-json','image-error'].includes(data.kind)?data.kind:'unknown'});
 });
 window.addEventListener('pagehide',()=>{dead=true;if(frame){frame.onload=null;frame.onerror=null;}mount.replaceChildren();frame=null;});
})();
