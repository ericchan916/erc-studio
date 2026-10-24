'use strict';
// Provider-origin session handling. No credentials leave the official origin or enter the studio server.
(()=>{
 const studio='https://studio.example.invalid',official='https://www.yixiaoer.cn';
 if(location.origin!==official)return;
 // A temporary, user-opened official login window only reports completion, never its token.
 if(window.top===window.self){
  const marker=new URL(location.href).hash.match(/studio-yxer-login=([a-zA-Z0-9_-]{8,96})/);
  const name=String(window.name||'').match(/^studio-yxer-login-([a-zA-Z0-9_-]{8,96})$/);
  const nonce=marker?.[1]||name?.[1];if(!nonce||!window.opener)return;
  try{if(window.opener.location.origin!==official||new URL(window.opener.document.referrer).origin!==studio)return;}catch{return;}
  let done=false;const timer=setInterval(()=>{
   if(done||/\/(?:login|wechat-bind)(?:\/|$)/.test(location.pathname))return;
   try{if(!localStorage.getItem('token'))return;done=true;window.opener.postMessage({type:'studio-yxer-login-ready',nonce},official);clearInterval(timer);}catch{}
  },1200);
  window.addEventListener('pagehide',()=>clearInterval(timer));return;
 }
 try{if(new URL(document.referrer).origin!==studio)return;}catch{return;}
 let disposed=false,sent=false,gate=null,storage=null,loginPopup=null,loginNonce=null,loginPoll=null;
 const notify=state=>{document.documentElement.dataset.cyberYxerBridgeState=state;window.parent.postMessage({type:'studio-yxer-bridge',state,version:'0.3.2'},studio);};
 const originalLog=console.log;
 console.log=function(...args){if(args[0]==='[Apass] Received message:'&&args[1]?.type==='auth')return;return originalLog.apply(this,args);};
 function token(source){try{const raw=(source||storage||localStorage).getItem('token');if(!raw)return null;let value;try{value=JSON.parse(raw);}catch{value=raw;}return typeof value==='string'&&value.trim()?value:null;}catch{return null;}}
 function authenticate(source){
  if(disposed||sent)return;const value=token(source);if(!value){notify('browser-login-required');showGate();return;}
  gate?.remove();gate=null;sent=true;
  window.dispatchEvent(new MessageEvent('message',{origin:studio,source:window.parent,data:{type:'auth',data:value,router:new URL(location.href).searchParams.get('router')==='account'?'account':'publish-list',config:{entries:['publishButton','addAccountButton','publishRecordLink'],publishTypes:['video','imageText','article','wechatOA'],contentSources:['native','assetLibrary']}}}));
  notify('auth-submitted');
 }
 async function activate(){
  if(typeof document.requestStorageAccess!=='function')throw Error('storage_access_unsupported');
  const handle=await document.requestStorageAccess({cookies:true,localStorage:true});if(disposed)return false;
  if(!handle?.localStorage)throw Error('storage_access_unavailable');storage=handle.localStorage;sent=false;authenticate(storage);return sent;
 }
 function showGate(message){
  if(disposed||!document.body)return;
  if(gate){if(message)gate.querySelector('p').textContent=message;return;}
  gate=document.createElement('section');gate.id='studio-yxer-session-gate';Object.assign(gate.style,{position:'fixed',inset:'0',zIndex:'2147483647',display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:'16px',padding:'28px',background:'#fff',color:'#25282d',fontFamily:'sans-serif'});
  const title=document.createElement('h2'),note=document.createElement('p'),actions=document.createElement('div'),login=document.createElement('button'),retry=document.createElement('button');
  title.textContent='连接本人蚁小二账号';note.textContent=message||'飞书登录和蚁小二登录相互独立。首次使用请先在官方认证窗口由本人登录，再返回此页检查。';Object.assign(note.style,{maxWidth:'520px',lineHeight:'1.8',textAlign:'center',color:'#747c88',fontSize:'13px'});Object.assign(actions.style,{display:'flex',gap:'10px',flexWrap:'wrap',justifyContent:'center'});
  login.textContent='登录蚁小二';retry.textContent='已完成登录，恢复内嵌';
  for(const b of[login,retry])Object.assign(b.style,{padding:'11px 18px',border:'1px solid #dfe3e9',borderRadius:'6px',background:'#fff',color:'#4d5560',cursor:'pointer'});
  Object.assign(login.style,{background:'#25282d',color:'#fff',borderColor:'#25282d'});
  login.onclick=()=>{
   if(loginPopup&&!loginPopup.closed){loginPopup.focus?.();return;}
   loginNonce=typeof crypto!=='undefined'&&typeof crypto.randomUUID==='function'?crypto.randomUUID():'login-'+Date.now()+'-'+Math.random().toString(16).slice(2);
   const returnPath='/web/publish#studio-yxer-login='+loginNonce;
   const url=official+'/web/login?redirect='+encodeURIComponent(returnPath)+'#studio-yxer-login='+loginNonce;
   loginPopup=window.open(url,'studio-yxer-login-'+loginNonce,'popup,width=560,height=760');
   if(!loginPopup){note.textContent='浏览器拦截了本人登录窗口，请允许此官方页面弹出窗口后重试。';return;}
   note.textContent='请在蚁小二官方窗口完成本人登录。返回后点击“已完成登录，恢复内嵌”；工作室不接收你的密码或登录凭据。';notify('login-window-opened');clearInterval(loginPoll);
   loginPoll=setInterval(()=>{if(disposed||!loginPopup)return;if(loginPopup.closed){clearInterval(loginPoll);loginPopup=null;initialize();}},1200);
  };
  retry.onclick=async()=>{retry.disabled=true;try{const ok=await activate();if(!disposed&&!ok)note.textContent='尚未发现本人蚁小二会话，请先完成官方登录。';}catch(e){if(!disposed)note.textContent=String(e.message).startsWith('storage_access_')?'该浏览器尚未提供所需的会话访问接口，请更新桌面 Chrome/Edge 后重试；不是蚁小二账号过期。':'浏览器尚未允许此官方子页面访问自己的会话，请在提示中允许后重试。';}finally{if(!disposed)retry.disabled=false;}};
  actions.append(login,retry);gate.append(title,note,actions);document.body.append(gate);
 }
 async function initialize(){
  if(disposed)return;if(token()){authenticate();return;}
  try{if(typeof document.requestStorageAccess==='function'&&typeof navigator!=='undefined'&&navigator.permissions){const permission=await navigator.permissions.query({name:'storage-access'});if(permission.state==='granted'||(typeof document.hasStorageAccess==='function'&&await document.hasStorageAccess())){await activate();return;}}}catch{}
  authenticate();
 }
 window.addEventListener('message',event=>{
  if(event.source===loginPopup&&event.origin===official&&event.data?.type==='studio-yxer-login-ready'&&event.data.nonce===loginNonce){initialize();return;}
  if(event.source!==window.parent||event.origin!==studio)return;
  if(event.data?.type==='studio-yxer-auth-retry'){sent=false;initialize();}
  else if(event.data?.type==='studio-yxer-auth-failed'){sent=false;showGate('官方身份或团队核验未通过。请本人重新登录并检查账号是否已加入蚁小二工作室团队；不共享管理员登录。');}
  else if(event.data?.type==='studio-yxer-auth-ready'){gate?.remove();gate=null;clearInterval(loginPoll);try{loginPopup?.close();}catch{}loginPopup=null;}
 });
 window.addEventListener('pagehide',()=>{disposed=true;clearInterval(loginPoll);console.log=originalLog;try{loginPopup?.close();}catch{}});
 function ready(){setTimeout(initialize,1000);notify('available');}
 if(document.readyState==='complete')ready();else window.addEventListener('load',ready,{once:true});
})();
