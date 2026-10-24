'use strict';
// Authentication shell only. Personal dialogue state, lifecycle and rendering live in separate modules.
(()=>{
const el=id=>document.getElementById(id);let member=null,authGeneration=0,authTimer=null,entering=false,scanStarting=false,view=null;
function error(e){el('home-chat-error').textContent=member?(e?.message||''):'';el('home-login-error').textContent=member?'':(e?.message||'');}
async function run(action){error();try{return await action();}catch(e){if(!e.stale)error(e);return null;}}
async function api(op,data={}){const requestedMember=member,generation=authGeneration;const result=await window.studio.conversation.call({op,...data});if(!result.ok){const stale=generation!==authGeneration||member!==requestedMember;if(result.error.status===401&&!stale&&member){resetPrivate();renderIdentity();error({message:result.error.message});}const e=new Error(result.error.message);e.stale=stale;e.code=result.error.code;e.status=result.error.status;throw e;}return result.data;}
const controller=new window.StudioConversationController({api,changed:()=>view?.render(),failed:error});
view=new window.StudioConversationView({controller,document,run,copy:async text=>api('copy',{text})});
function resetPrivate(){authGeneration++;clearTimeout(authTimer);member=null;controller.identity(null);view.clear();el('home-message').value='';window.StudioTypography?.resizeInput(el('home-message'));el('home-otp-code').value='';el('home-login-status').textContent='';el('home-scan-panel').hidden=true;el('home-scan-qr').removeAttribute('src');el('home-history-search').value='';}
function renderIdentity(){document.body.classList.toggle('studio-auth-locked',!member);if(typeof window.CustomEvent==='function')window.dispatchEvent(new window.CustomEvent('studio-identity',{detail:member?.principal_id||null}));el('studio-login-page').hidden=!!member;document.title=member?'ERC Studio · 主页':'ERC Studio · 登录';if(!member)window.hideStudioHome?.();el('home-page').classList.toggle('chat-connected',!!member);el('home-login').hidden=!!member;el('home-logout').hidden=!member;el('home-member').textContent=member?member.display_name+' · 私人对话':'成员未登录';el('home-send').title=member?'发送至本人对话空间':'先使用本人飞书登录';el('home-send').setAttribute('aria-label',member?'发送消息':'发送消息，需先登录');view.render();}
async function admitted(result){resetPrivate();member=result.member;controller.identity(member);renderIdentity();await window.openStudioHome?.({skipConversationRefresh:true});await controller.enter();}
el('home-logout').onclick=()=>run(async()=>{try{await api('logout');}finally{resetPrivate();renderIdentity();}});
async function startScan(){
 if(scanStarting)return;
 scanStarting=true;
 const start=el('home-scan-start');
 start.disabled=true;
 try{
  resetPrivate();renderIdentity();error();
  el('home-login-status').textContent='正在向飞书申请官方授权二维码…';
  const result=await api('scanStart');
  el('home-scan-qr').src=result.qrcode_data_url;
  el('home-scan-panel').hidden=false;
  el('home-scan-finish').disabled=false;
  el('home-login-status').textContent='等待本人飞书扫码确认，成功后自动进入对话。有效至 '+new Date(result.expires_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'})+'。';
  if(el('home-scan-qr').decode)await el('home-scan-qr').decode();
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  authTimer=setTimeout(()=>run(()=>finish(++authGeneration)),300);
 }catch(e){
  if(!e.stale)el('home-login-status').textContent='二维码生成失败，请重试。';
  throw e;
 }finally{
  scanStarting=false;
  start.disabled=false;
 }
}
async function finish(generation){if(generation!==authGeneration||member)return;try{const result=await api('scanPoll');if(generation!==authGeneration)return;if(result.status==='pending'){authTimer=setTimeout(()=>run(()=>finish(generation)),1000);return;}await admitted(result);}catch(e){if(generation!==authGeneration)return;el('home-scan-finish').disabled=false;if(e.code==='oauth_expired'){el('home-scan-panel').hidden=true;el('home-scan-qr').removeAttribute('src');el('home-login-status').textContent='二维码已过期，请刷新二维码。';}throw e;}}
el('home-scan-start').onclick=()=>run(startScan);el('home-scan-open').onclick=()=>run(()=>api('scanOpen'));el('home-scan-finish').onclick=()=>run(async()=>{clearTimeout(authTimer);el('home-scan-finish').disabled=true;await finish(++authGeneration);});
el('home-otp-start').onclick=()=>run(async()=>{el('home-otp-start').disabled=true;try{resetPrivate();renderIdentity();const result=await api('otpStart',{member_handle:el('home-otp-member').value});el('home-otp-code').disabled=false;el('home-otp-finish').disabled=false;el('home-login-status').textContent='一次性码已发送给 '+result.member_name+'。';}finally{el('home-otp-start').disabled=false;}});
el('home-otp-finish').onclick=()=>run(async()=>admitted(await api('otpPoll',{code:el('home-otp-code').value})));el('home-otp-member').onchange=()=>{resetPrivate();renderIdentity();el('home-otp-code').disabled=true;el('home-otp-finish').disabled=true;};
async function enter(){if(entering)return;entering=true;try{await run(async()=>{const health=await api('status');el('home-backend-status').textContent='已连接 · '+health.model;el('home-backend-status').title=health.origin;el('login-backend-status').textContent='团队后端已连接';const list=await api('members');if(!el('home-otp-member').options.length)for(const m of list.items)el('home-otp-member').add(new Option(m.name,m.handle));try{const me=await api('me'),restored=!member||member.principal_id!==me.principal_id;if(restored)resetPrivate();member=me;controller.identity(member);renderIdentity();if(restored)await window.openStudioHome?.({skipConversationRefresh:true});await controller.enter();}catch(e){if(member)resetPrivate();renderIdentity();if(e.status===401)await startScan();else throw e;}});}finally{entering=false;}}
window.studioHomeChat={enter,isSignedIn:()=>!!member,principal:()=>member?.principal_id||null,leave:()=>controller.leave()};
el('home-backend-retry').onclick=enter;el('login-backend-retry').onclick=enter;window.addEventListener?.('resize',()=>window.StudioTypography?.resizeInput(el('home-message')));window.StudioTypography?.resizeInput(el('home-message'));renderIdentity();enter();
})();
