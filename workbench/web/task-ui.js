'use strict';
let studioFeishuPage='tasks';
const studioFeishuAPI=()=>studioFeishuPage==='library'?window.studio.bar.library:window.studio.tasks;
window.hideStudioTasks=()=>{$('task-page').hidden=true;document.querySelector('.topbar').hidden=false;};
window.syncStudioTaskBounds=()=>{if($('task-page').hidden)return;const r=$('task-mount').getBoundingClientRect();safely(()=>studioFeishuAPI().bounds({x:r.x,y:r.y,width:r.width,height:r.height}));};
function openStudioFeishuPane(page){return safely(async()=>{
 await window.studio.close();studioFeishuPage=page;window.hideStudioBar?.();window.hideStudioContent?.();window.hideStudioAnalytics?.();window.hideStudioHome?.();window.hideStudioWebsite?.();
 for(const id of ['account-page','group-page','browser-page'])$(id).hidden=true;
 $('task-page').hidden=false;document.querySelector('.topbar').hidden=true;
 $('task-home').textContent=page==='library'?'返回活动资料库':'返回任务总栏';
 $('task-status').textContent=page==='library'?'正在打开飞书活动资料库…':'正在打开飞书公司任务总栏…';$('task-host').textContent='';$('task-page').classList.remove('load-error');
 document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav===(page==='library'?'bar':'tasks')));
 document.querySelectorAll('.website-subnav button').forEach(b=>b.classList.toggle('active',page==='library'&&b.dataset.barPage==='library'));
 await studioFeishuAPI().open();window.syncStudioTaskBounds();
});}
window.openStudioTasks=()=>openStudioFeishuPane('tasks');
window.openStudioBarLibrary=()=>openStudioFeishuPane('library');
function studioFeishuState(page,v){if(studioFeishuPage!==page||$('task-page').hidden)return;
 $('task-status').textContent=v.message||'正在打开飞书…';$('task-status').dataset.phase=v.phase;
 $('task-host').textContent=v.hostname;$('task-page').classList.toggle('load-error',['error','blocked','unconfigured'].includes(v.phase));
 $('task-reload').disabled=!v.configured;window.syncStudioTaskBounds();
}
window.studio.tasks.onState(v=>studioFeishuState('tasks',v));
window.studio.bar.library.onState(v=>studioFeishuState('library',v));
$('task-reload').onclick=()=>safely(()=>studioFeishuAPI().command('reload'));
$('task-home').onclick=()=>safely(()=>studioFeishuAPI().command('home'));
