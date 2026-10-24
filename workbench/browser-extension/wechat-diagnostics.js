'use strict';
// Passive, provider-origin diagnostics only; never read request bodies, Cookie, headers or QR pixels.
(()=>{
 const studio='https://studio.example.invalid';
 if(location.hostname!=='mp.weixin.qq.com'||window.top===window.self)return;
 try{const ref=new URL(document.referrer);if(ref.origin!==studio&&!(ref.protocol==='chrome-extension:'&&ref.hostname==='kecdiclhheadhligeohfbaboikcdkdpp'&&ref.pathname==='/publisher-host.html'))return;}catch{return;}
 if(typeof XMLHttpRequest==='undefined')return;
 let dead=false;const observed=new WeakSet(),eligible=new WeakMap(),originalOpen=XMLHttpRequest.prototype.open;
 const referrer=new URL(document.referrer),destination=referrer.protocol==='chrome-extension:'?'chrome-extension://'+referrer.hostname:studio;
 const clean=value=>typeof value==='number'&&Number.isFinite(value)&&Number.isInteger(value)?value:null;
 function report(phase,status,ret,kind){if(dead)return;window.parent.postMessage({type:'studio-wechat-qr-diagnostic',data:{phase,httpStatus:clean(status),ret:clean(ret),kind}},destination);}
 const wrappedOpen=function(...args){
  let inspect=false;try{const u=new URL(String(args[1]),location.href);inspect=u.origin===location.origin&&u.pathname==='/cgi-bin/bizlogin'&&u.searchParams.get('action')==='startlogin';}catch{}
  eligible.set(this,inspect);
  const result=originalOpen.apply(this,args);
  if(inspect&&!observed.has(this)){
   observed.add(this);this.addEventListener('loadend',()=>{
    if(dead||!eligible.get(this))return;let ret=null,kind='response';
    try{const value=this.responseType==='json'?this.response:JSON.parse(this.responseText);ret=clean(value?.base_resp?.ret);}catch{kind='non-json';}
    report('startlogin',this.status,ret,this.status===0?'network':kind);
   });
  }
  return result;
 };
 XMLHttpRequest.prototype.open=wrappedOpen;
 window.addEventListener('error',event=>{
  const image=event.target;if(image?.tagName!=='IMG')return;
  try{const u=new URL(image.currentSrc||image.src,location.href);if(u.origin===location.origin&&u.pathname==='/cgi-bin/scanloginqrcode'&&u.searchParams.get('action')==='getqrcode')report('image',null,null,'image-error');}catch{}
 },true);
 window.addEventListener('pagehide',()=>{dead=true;if(XMLHttpRequest.prototype.open===wrappedOpen)XMLHttpRequest.prototype.open=originalOpen;});
})();
