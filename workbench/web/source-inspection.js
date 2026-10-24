'use strict';
// Displays original module layouts with no identity, business records or actions.
(function(){
 const none=()=>()=>{};
 const readonlyPlatforms=[{"id":"xiaohongshu","name":"小红书","short":"红","color":"#ff2442","url":"https://creator.xiaohongshu.com/new/home"},{"id":"douyin","name":"抖音","short":"♪","color":"#15191e","url":"https://creator.douyin.com/creator-micro/content/manage"},{"id":"bilibili","name":"哔哩哔哩","short":"哔","color":"#fb7299","url":"https://member.bilibili.com/platform/data-up/video/"},{"id":"kuaishou","name":"快手","short":"快","color":"#ff5400","url":"https://cp.kuaishou.com/"},{"id":"wechat","name":"微信公众号","short":"公","color":"#07b75a","url":"https://mp.weixin.qq.com/"},{"id":"channels","name":"视频号","short":"视","color":"#ffa02e","url":"https://channels.weixin.qq.com/"}];
 const state={phase:'unconfigured',message:'源码只读查看；尚未绑定你自己的资源',configured:false,authenticated:false,topics:[],projects:[],jobs:[],forms:[],accounts:[],platforms:{},actor:{name:'未登录',canWrite:false,canApprove:false},layers:[],activities:[],themes:[]};
 const read=async()=>structuredClone(state);
 const deny=async()=>{throw new Error('source_inspection_readonly：请先配置原生工作台，源码查看模式不执行业务动作');};
 const pane={open:read,hide:async()=>true,state:read,bounds:async()=>true,command:deny,onState:none};
 window.studio={list:async()=>({accounts:[],groups:[],platforms:readonlyPlatforms,browserMode:'source_inspection'}),refresh:async()=>({accounts:[],groups:[],platforms:readonlyPlatforms,browserMode:'source_inspection'}),onChange:none,onBrowser:none,close:async()=>true,home:async()=>true,homeModel:deny,open:deny,add:deny,update:deny,group:deny,assign:deny,command:deny,bounds:async()=>true,
  content:{open:read,read,query:read,project:pane,onState:none,onHide:none,openSource:deny,openDocument:deny,...Object.fromEntries(['createTopic','editTopic','decideTopic','createProject','saveDraft','advance','addPublication','syncMetrics','saveReview','generate','verify','pendingResult','resumeJob'].map(k=>[k,deny]))},
  analytics:{open:read,read,sync:deny,onState:none},tasks:pane,website:pane,bar:{read,openSource:deny,library:pane},workflow:async()=>({schemaVersion:1,items:[],message:state.message}),conversation:{call:deny}};
 function home(){
  document.body.classList.remove('studio-auth-locked');document.getElementById('studio-login-page').hidden=true;
  for(const id of ['account-page','group-page','browser-page','task-page','website-page'])document.getElementById(id).hidden=true;
  const page=document.getElementById('home-page');page.hidden=false;
  document.getElementById('home-history-panel').hidden=true;
  document.getElementById('home-member').textContent='源码只读查看 · 未登录';
  document.getElementById('home-backend-status').textContent='未连接业务后端';
  document.getElementById('home-job-status').textContent='源码查看不会调用模型、读取飞书或发布作品';
  document.getElementById('home-avatar-status').textContent='ERC Studio · EricChan';
  document.querySelector('.topbar').hidden=true;
  document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='home'));
 }
 window.studioHomeChat={isSignedIn:()=>false,enter:home,leave:()=>{}};
 document.addEventListener('DOMContentLoaded',()=>{
  const banner=document.createElement('div');banner.className='source-inspection-banner';banner.textContent='ERC Studio · 本地只读预览｜连接自己的账号与服务后使用业务功能';document.body.prepend(banner);
  home();
  document.querySelectorAll('[data-nav="publish"],[data-nav="data"],[data-nav="radar"]').forEach(b=>{
   b.disabled=false;b.onclick=()=>{
    home();document.getElementById('home-greeting').hidden=true;
    let panel=document.getElementById('source-module-detail');if(!panel){panel=document.createElement('section');panel.id='source-module-detail';panel.className='source-module-detail';document.querySelector('.home-content').prepend(panel);}
    const key=b.dataset.nav,names={publish:'发布',data:'数据复盘',radar:'选题雷达'};
    panel.replaceChildren();const title=document.createElement('h1');title.textContent=names[key]+' · 原模块源码与规范';const body=document.createElement('p');body.textContent='本页未接本人资源，因此仅展示源码入口。原实现已迁入 workbench/backend/ 与 web/，不是模拟业务成功。';const code=document.createElement('pre');code.textContent=key==='publish'?'workbench/backend/publishing.py\nworkbench/backend/web/publish-console.js\ndocs/modules/publishing.md':key==='data'?'workbench/backend/account_metrics.py\nworkbench/web/analytics-ui.js\ndocs/modules/data.md':'workbench/backend/radar_snapshot.py\nworkbench/web/radar-ui.js\ndocs/modules/radar.md';panel.append(title,body,code);
   };
  });
  const first=document.querySelector('[data-nav="home"]');first.addEventListener('click',()=>{document.getElementById('home-greeting').hidden=false;document.getElementById('source-module-detail')?.remove();});
 });
})();
