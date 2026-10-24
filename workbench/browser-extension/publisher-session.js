'use strict';
// Only activate the browser's native same-provider cookie access. Never read/export Cookie or credentials.
(()=>{
 const hosts=new Set(['mp.weixin.qq.com','x.com']),studio='https://studio.example.invalid',container='chrome-extension://kecdiclhheadhligeohfbaboikcdkdpp';
 if(!hosts.has(location.hostname)||window.top===window.self)return;
 let dead=false,gate=null,started=false,activating=false,gateScheduled=false;
 window.addEventListener('pagehide',()=>{dead=true;});
 // This listener is also present after an X-origin hard navigation; no referrer-based credential access.
 if(location.hostname==='x.com'){let lastReloadNonce=null;window.addEventListener('message',event=>{
  if(dead||event.source!==window.parent||event.origin!==studio||event.data?.type!=='studio-publisher-reload')return;
  const nonce=event.data.nonce;if(typeof nonce!=='string'||!/^x-reload-[a-z0-9-]{1,70}$/.test(nonce)||nonce===lastReloadNonce)return;lastReloadNonce=nonce;
  window.parent.postMessage({type:'studio-publisher-reload-started',nonce},studio);
  location.reload();
 });}
 async function activate(){if(dead||activating)return;activating=true;try{await document.requestStorageAccess({cookies:true});if(!dead)location.reload();}catch(e){activating=false;throw e;}}
 function showGate(){
  if(dead||gate)return;
  if(document.readyState==='loading'){if(!gateScheduled){gateScheduled=true;document.addEventListener('DOMContentLoaded',showGate,{once:true});}return;}
  if(!document.body)return;
  gate=document.createElement('section');gate.id='studio-publisher-cookie-access';Object.assign(gate.style,{position:'fixed',inset:'0',zIndex:'2147483647',background:'#fff',color:'#25282d',display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:'16px',fontFamily:'sans-serif'});
  const name=location.hostname==='x.com'?'X':'公众号',title=document.createElement('h2'),note=document.createElement('p'),button=document.createElement('button');
  title.textContent='复用本人'+name+'登录';note.textContent='仅允许此官方子页面使用同一浏览器里它自己的已有登录，不复制或导出Cookie。';button.textContent='允许复用本人登录';Object.assign(button.style,{padding:'12px 20px',border:'0',borderRadius:'6px',background:'#25282d',color:'#fff',cursor:'pointer'});
  button.onclick=async()=>{button.disabled=true;try{await activate();}catch{if(!dead){note.textContent='浏览器尚未授予此官方子页面的会话访问，请在提示中允许后重试。';button.disabled=false;}}};gate.append(title,note,button);document.body.append(gate);
 }
 async function ready(){
  if(dead)return;
  if(typeof document.hasStorageAccess!=='function'||typeof document.requestStorageAccess!=='function')return;
  try{if(await document.hasStorageAccess())return;const p=await navigator.permissions.query({name:'storage-access'});if(p.state==='granted'){await activate();return;}}catch{}
  showGate();
 }
 function start(){if(started||dead)return;started=true;return ready();}
 if(location.hostname==='mp.weixin.qq.com'){
  window.addEventListener('message',event=>{if(event.source===window.parent&&event.origin===container&&event.data?.type==='studio-wechat-session-enable')start();});
  window.parent.postMessage({type:'studio-wechat-session-ready'},container);
 }
 try{if(new URL(document.referrer).origin!==studio)return;}catch{return;}
 // Keep the previously verified direct Studio/X lifecycle unchanged.
 if(document.readyState==='complete')start();else window.addEventListener('load',start,{once:true});
})();
