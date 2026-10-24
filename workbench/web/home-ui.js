'use strict';
let homeAvatar=null,homeOrbit=true;
function ensureHomeAvatar(){if(!homeAvatar)homeAvatar=window.StudioHomeGLB.create($('home-avatar'),value=>{$('home-avatar-status').textContent=value;},()=>window.studio.homeModel());return homeAvatar;}
window.syncStudioHomeAvatar=()=>homeAvatar?.setActive(!$('home-page').hidden&&!$('home-page').classList.contains('chat-mode'));
window.hideStudioHome=()=>{document.body.classList.remove('studio-chat-home');$('home-history-panel').hidden=true;$('home-page').hidden=true;homeAvatar?.setActive(false);window.studioHomeChat?.leave();};
window.openStudioHome=(options={})=>safely(async()=>{
 if(!window.studioHomeChat?.isSignedIn()){window.studioHomeChat?.enter();return;}
 await window.studio.home();window.hideStudioTasks?.();window.hideStudioWebsite?.();window.hideStudioContent?.();window.hideStudioAnalytics?.();
 for(const id of ['account-page','group-page','browser-page'])$(id).hidden=true;
 $('home-page').hidden=false;document.body.classList.add('studio-chat-home');$('home-history-panel').hidden=false;document.querySelector('.topbar').hidden=true;
 document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='home'));
 ensureHomeAvatar();window.syncStudioHomeAvatar();if(!options.skipConversationRefresh)window.studioHomeChat?.enter();
});
$('home-avatar-orbit').textContent='暂停待机';
$('home-avatar-orbit').onclick=()=>{homeOrbit=!homeOrbit;ensureHomeAvatar().setOrbit(homeOrbit);$('home-avatar-orbit').setAttribute('aria-pressed',String(homeOrbit));$('home-avatar-orbit').textContent=homeOrbit?'暂停待机':'恢复待机';};
const homeTurn=document.createElement('button');homeTurn.id='home-model-turn';homeTurn.textContent='转身一圈';homeTurn.title='整体转身交互；当前模型未包含骨骼动画';homeTurn.onclick=()=>ensureHomeAvatar().turn();document.querySelector('.home-avatar-controls').append(homeTurn);
window.addEventListener('beforeunload',()=>homeAvatar?.destroy());
window.openStudioHome();
