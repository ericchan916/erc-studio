'use strict';
window.hideStudioWebsite=()=>{
 $('website-page').hidden=true;document.querySelector('.topbar').hidden=false;
 document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='account'));
};
window.syncStudioWebsiteBounds=()=>{if($('website-page').hidden)return;const r=$('website-mount').getBoundingClientRect();safely(()=>window.studio.website.bounds({x:r.x,y:r.y,width:r.width,height:r.height}));};
window.openStudioWebsite=()=>safely(async()=>{
 await window.studio.close();window.hideStudioAnalytics?.();window.hideStudioHome?.();
 window.hideStudioTasks?.();
 for(const id of ['account-page','group-page','browser-page'])$(id).hidden=true;
 $('website-page').hidden=false;document.querySelector('.topbar').hidden=true;
 document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='website'));
 window.markStudioWebsiteNav?.('site');await window.studio.website.open();window.syncStudioWebsiteBounds();
});
window.studio.website.onState(v=>{
 $('website-status').textContent=v.message||'正在打开官网…';$('website-status').dataset.phase=v.phase;
 $('website-status').classList.toggle('website-error',v.phase==='error');$('website-host').textContent=v.hostname;
 $('website-page').classList.toggle('load-error',v.phase==='error');
 document.querySelector('[data-website-command="back"]').disabled=!v.canGoBack;
 window.syncStudioWebsiteBounds();
});
document.querySelectorAll('[data-website-command]').forEach(b=>b.onclick=()=>safely(()=>window.studio.website.command(b.dataset.websiteCommand)));
$('website-return').onclick=()=>showTab('accounts');
