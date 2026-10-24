'use strict';
// Keep all user-triggered same-provider document requests on the local extension initiator.
// No Cookie access; opaque URLs stay in provider/local extension memory, never the Studio host.
(()=>{
 const provider='https://mp.weixin.qq.com',container='chrome-extension://kecdiclhheadhligeohfbaboikcdkdpp';
 if(location.origin!==provider||window.top===window.self)return;
 let enabled=false,dead=false,sequence=0;const originalOpen=window.open;
 function editorURL(value){
  if(typeof value!=='string'||!value.trim()||value.length>16384)return null;
  try{const u=new URL(value,location.href);if(u.origin!==provider||u.protocol!=='https:'||u.username||u.password)return null;
   // Authentication and downloads retain their official browser behavior.
   if(/(?:login|logout|oauth|captcha|authorize|download)/i.test(u.pathname)||/^(?:login|logout|authorize|download)$/i.test(u.searchParams.get('action')||''))return null;
   return u.href;
  }catch{return null;}
 }
 function route(value){const url=editorURL(value);if(!enabled||dead||!url)return false;const nonce='wechat-nav-'+Date.now().toString(36)+'-'+(++sequence);window.parent.postMessage({type:'studio-wechat-navigate',url,nonce},container);return true;}
 const wrappedOpen=function(url,target,features){
  if(enabled&&!dead&&navigator.userActivation?.isActive&&route(url))return window;
  return originalOpen.apply(this,arguments);
 };
 function click(event){
  if(!enabled||dead||!navigator.userActivation?.isActive||event.isTrusted!==true||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  const anchor=event.target?.closest?.('a[href]');if(!anchor||anchor.hasAttribute('download'))return;
  const value=editorURL(anchor.href);if(!value)return;
  const target=new URL(value),current=new URL(location.href);if(target.pathname===current.pathname&&target.search===current.search&&target.hash)return;
  // Bubble phase lets target/ancestor handlers cancel before this listener; do not stop propagation.
  event.preventDefault();route(value);
 }
 window.addEventListener('message',event=>{
  if(dead||event.source!==window.parent||event.origin!==container||event.data?.type!=='studio-wechat-navigation-enable')return;
  if(enabled)return;enabled=true;window.open=wrappedOpen;document.addEventListener('click',click,false);
 });
 window.addEventListener('pagehide',()=>{dead=true;enabled=false;if(window.open===wrappedOpen)window.open=originalOpen;document.removeEventListener('click',click,false);});
 try{const ref=new URL(document.referrer);if(ref.protocol==='chrome-extension:'&&'chrome-extension://'+ref.hostname===container)window.parent.postMessage({type:'studio-wechat-navigation-ready'},container);}catch{}
})();
