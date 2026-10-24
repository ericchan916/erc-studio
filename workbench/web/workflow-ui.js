'use strict';
// Visibility follows actual page transitions, not sidebar disclosure clicks.
const workflowVisiblePeer=(children,own)=>Array.from(children).find(page=>page!==own&&page.tagName==='SECTION'&&!page.hidden);
const workflowEvidenceSummary=item=>{
 const codes=item.codes||[];
 if(codes.includes('LAUNCHD_NOT_LOADED'))return '服务未加载';
 if(codes.includes('CONTINUOUS_SERVICE_STOPPED'))return '常驻服务已停止';
 if(codes.includes('LAST_EXIT_NONZERO'))return '最近执行退出异常';
 if(codes.includes('HEALTH_STALE'))return '运行心跳已过期';
 if(codes.includes('QUEUE_UNKNOWN_RESULTS')||codes.includes('RECEIPT_ATTENTION'))return '有待核验或错误回执';
 if(codes.includes('EXHAUSTED_EVENT_COUNT')||codes.includes('DEAD_LETTER_COUNT'))return '存在重试耗尽任务';
 if(codes.includes('HEALTH_ERROR_PRESENT')||codes.includes('HEALTH_ERROR'))return '运行记录存在错误';
 if(codes.includes('BUSINESS_ISSUES')||codes.includes('HEALTH_STATUS_ATTENTION'))return '健康检查需要关注';
 if(codes.includes('HTTP_HEALTH_UNAVAILABLE')||codes.includes('HEALTH_MISSING_OR_INVALID'))return '健康来源暂未读取';
 if(codes.includes('RECEIPT_EVIDENCE_INCOMPLETE'))return '回执证据尚不完整';
 if(codes.includes('NOTIFICATION_QUEUE_ATTENTION'))return '通知队列需要处理';
 return item.evidence?.at(-1)||'暂无独立健康证据';
};
if(typeof module==='object'&&module.exports)module.exports={workflowVisiblePeer,workflowEvidenceSummary};
else (()=>{
 if(typeof window.studio?.workflow!=='function')return;
 const make=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
 const labels={healthy:'技术正常',attention:'需要关注',unknown:'证据不足',waiting:'待触发 / 宽限'};
 const times=v=>v&&Number.isFinite(Date.parse(v))?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'—';
 const root=make('section','workflow-page page-body');root.id='workflow-page';root.hidden=true;document.querySelector('main').append(root);
 const nav=make('button','nav-item'),img=make('img','nav-icon');img.src='vendor/tabler-content/layout-list.svg';img.alt='';nav.dataset.nav='workflow';nav.append(img,make('span','nav-label','工作流'));nav.title='查看本机工作流、调度与健康证据';document.querySelector('[data-nav="account"]').before(nav);
 const header=make('header','workflow-header page-header'),title=make('div');title.append(make('h1','page-title','工作流中心'),make('p','text-secondary','本机工作流、调度与真实运行状态'));
 const tools=make('div','workflow-tools'),auto=make('label','workflow-auto form-check form-switch'),check=make('input','form-check-input');check.type='checkbox';check.checked=true;check.setAttribute('aria-label','自动刷新，每60秒');auto.append(check,make('span','','自动刷新 60秒'));
 const refresh=make('button','workflow-button btn btn-ghost-secondary','刷新'),detect=make('button','workflow-button primary btn btn-dark btn-pill','立即检测');refresh.id='workflow-refresh';detect.id='workflow-detect';tools.append(auto,refresh,detect);header.append(title,tools);root.append(header);
 const summaries=make('div','workflow-summaries');root.append(summaries);
 const layout=make('div','workflow-layout'),rail=make('nav','workflow-filters nav nav-tabs'),content=make('div','workflow-content'),toolbar=make('div','workflow-toolbar'),search=make('input','form-control');rail.setAttribute('aria-label','工作流分类');search.placeholder='搜索工作流、调度或异常码';search.setAttribute('aria-label','搜索工作流');search.id='workflow-search';
 const filter=make('select','form-select');filter.id='workflow-status';filter.setAttribute('aria-label','工作流状态');filter.append(new Option('全部状态','all'),...Object.entries(labels).map(([v,t])=>new Option(t,v)));
 const sort=make('select','form-select');sort.setAttribute('aria-label','工作流排序');sort.append(new Option('优先显示异常','attention'),new Option('按名称排序','name'));toolbar.append(search,filter,sort);
 const wrap=make('div','workflow-table-wrap table-responsive'),table=make('table','workflow-table table table-vcenter table-hover table-transparent'),head=make('thead'),row=make('tr'),body=make('tbody');body.id='workflow-rows';for(const t of ['工作流 / 服务','状态','触发方式','最近心跳','下次调度','健康证据','操作']){const th=make('th','',t);th.scope='col';row.append(th);}head.append(row);table.append(head,body);wrap.append(table);const empty=make('p','workflow-empty','未找到符合条件的工作流');empty.hidden=true;wrap.append(empty);
 const footer=make('footer','workflow-footer'),count=make('span'),pager=make('div'),prev=make('button','workflow-button','上一页'),next=make('button','workflow-button','下一页'),pageLabel=make('span');pager.append(prev,pageLabel,next);footer.append(count,pager);
 const notice=make('p','workflow-notice','准备读取本机运行状态');notice.id='workflow-notice';notice.setAttribute('role','status');content.append(toolbar,wrap,footer,notice);layout.append(rail,content);root.append(layout);
 const coverage=make('details','workflow-coverage'),coverageTitle=make('summary','','监控覆盖与维护登记'),coverageText=make('p');coverage.append(coverageTitle,coverageText);root.append(coverage);
 const drawer=make('dialog','workflow-drawer');drawer.id='workflow-detail';document.body.append(drawer);
 let data=null,history=[],group='all',page=1,busy=false,epoch=0,timer=null,phase='idle',selected=null;
 function badge(status){const b=make('span','workflow-badge '+status);b.append(make('i','status-dot'),make('span','',labels[status]||'未知'));return b;}
 function field(parent,label,value){const block=make('section','workflow-field');block.append(make('h3','',label),make('p','',value||'—'));parent.append(block);}
 function detail(id){const i=data?.items.find(i=>i.id===id);if(!i)return;selected=id;drawer.replaceChildren();const h=make('header'),name=make('div');name.append(make('small','',i.kind==='workflow'?'业务工作流':'运行组件'),make('h2','',i.name),badge(i.status));const close=make('button','workflow-button','关闭');close.onclick=()=>drawer.close();h.append(name,close);drawer.append(h);
  field(drawer,'事实与边界',i.business?.note);field(drawer,'源码维护入口',i.source);field(drawer,'当前异常码',(i.codes||[]).join(' · ')||'暂无；不代表业务已完成');
  if(i.trigger)field(drawer,'触发条件',i.trigger);if(i.action)field(drawer,'主要动作',i.action);if(i.boundary)field(drawer,'执行边界',i.boundary);
  if(i.launchd?.label)field(drawer,'LaunchAgent',i.launchd.label+'\n状态：'+i.launchd.state+' · PID：'+(i.launchd.pid??'—')+' · 退出码：'+(i.launchd.lastExitCode??'未记录'));
  field(drawer,'调度证据',JSON.stringify(i.schedule,null,2));field(drawer,'健康来源与时间',(i.health?.source||'无独立 health')+'\n最近心跳：'+times(i.health?.timestamp)+'\n本次检测：'+times(i.observedAt));
  if(i.supervisor)field(drawer,'守护器结论',JSON.stringify(i.supervisor,null,2));if(i.health?.metrics)field(drawer,'脱敏健康指标',JSON.stringify(i.health.metrics,null,2));
  if(i.dependencyStates?.length){const section=make('section','workflow-field');section.append(make('h3','','依赖状态'));for(const d of i.dependencyStates){const button=make('button','workflow-dependency',d.name+' · '+(labels[d.status]||'未知'));button.onclick=()=>detail(d.id);section.append(button);}drawer.append(section);}
  for(const q of i.queues||[])field(drawer,'技术队列 · '+q.table,q.source+'\n读取：'+q.state+'\n'+JSON.stringify(q.counts,null,2)+'\n最近回执时间：'+times(q.times?.sent_at||q.times?.remote_status_verified_at||q.times?.updated_at));
  for(const q of i.receipts||[])field(drawer,'原流程回执 · '+q.table,q.source+'\n读取：'+q.state+' · 记录：'+(q.total??'未知')+' · 在途：'+(q.activeCount??'未知')+' · 待核验/错误：'+(q.attentionCount??'未知')+'\n'+JSON.stringify(q.counts,null,2)+'\n最近回执：'+times(q.latestAt)+'\n最近成功状态：'+times(q.lastRecordedSuccessAt)+'\n'+(q.successMeaning||i.business?.note||''));
  for(const code of i.runtimeCode||[])field(drawer,'实际执行文件 SHA-256',code.path+'\n'+(code.sha256||'入口文件暂不可读取；未将缺失哈希当作版本已验证'));
  const changes=history.filter(e=>e.id===id);field(drawer,'本机观测到的状态变化',changes.length?changes.slice(-10).reverse().map(e=>times(e.at)+' '+(e.kind==='evidence'?'证据变化 · '+labels[e.to]:labels[e.from]+' → '+labels[e.to])+(e.codes?.length?' · '+e.codes.join(' / '):'')).join('\n'):'尚无变化记录；只记录本 App 实际观测，不补造历史');
  if(!drawer.open)drawer.showModal();
 }
 function render(){
  if(!data)return;summaries.replaceChildren();for(const [label,value]of [['全部监控',data.items.length],['技术正常',data.summary.healthy],['需要关注',data.summary.attention],['待核验 / 待触发',data.summary.unknown+data.summary.waiting]]){const metric=make('div','workflow-summary');metric.append(make('span','subheader',label),make('strong','',String(value)));summaries.append(metric);}
  rail.replaceChildren();for(const [key,label]of [['all','全部'],['service','运行组件'],['workflow','业务流程'],['attention','需要关注'],['unknown','证据不足']]){const n=data.items.filter(i=>key==='all'||i.kind===key||i.status===key).length,b=make('button','workflow-category nav-link'+(key===group?' selected active':''));b.setAttribute('aria-pressed',String(key===group));b.append(make('span','',label),make('span','',String(n)));b.onclick=()=>{group=key;page=1;render();};rail.append(b);}
  const q=search.value.trim().toLowerCase(),priority={attention:0,unknown:1,waiting:2,healthy:3};let items=data.items.filter(i=>(group==='all'||i.kind===group||i.status===group)&&(filter.value==='all'||i.status===filter.value)&&(!q||[i.name,i.source,i.schedule?.label,...(i.codes||[])].join(' ').toLowerCase().includes(q))).sort((a,b)=>(sort.value==='attention'?priority[a.status]-priority[b.status]:0)||a.name.localeCompare(b.name,'zh-CN'));
  const pages=Math.max(1,Math.ceil(items.length/20));page=Math.min(page,pages);body.replaceChildren();for(const i of items.slice((page-1)*20,page*20)){const r=make('tr',i.status==='attention'?'needs-attention':'');r.dataset.workflowId=i.id;const name=make('td'),title=make('strong','',i.name);name.append(title,make('small','',i.group));r.append(name);const status=make('td');status.append(badge(i.status));r.append(status);
   for(const text of [i.schedule?.label||'—',times(i.health?.timestamp),i.schedule?.nextAt?times(i.schedule.nextAt)+'（估算）':'—',workflowEvidenceSummary(i)]){const cell=make('td','',text);cell.title=text;r.append(cell);}const ops=make('td'),b=make('button','workflow-detail-button btn btn-link','详情 →');b.setAttribute('aria-label','查看 '+i.name+' 详情');b.onclick=()=>detail(i.id);ops.append(b);r.append(ops);body.append(r);}
  empty.hidden=items.length!==0;count.textContent='共 '+items.length+' 项 · '+data.coverage.workflowRoutes+' 个业务流程 / '+(data.items.length-data.coverage.workflowRoutes)+' 个运行组件';pageLabel.textContent=page+' / '+pages;prev.disabled=page<=1;next.disabled=page>=pages;
  notice.textContent=(phase==='stale'?'读取失败，以下为旧观测，状态待重新检测。 ':'最近检测：'+times(data.observedAt)+' · ')+(data.coverage.supervisorFresh?'守护器观测新鲜':'守护器观测缺失或过期')+' · 进程在线不等于业务成功';notice.classList.toggle('stale',phase==='stale');root.classList.toggle('workflow-stale',phase==='stale');
  const registry=data.coverage.runtimeRegistry||{};coverageText.textContent='已发现 '+data.coverage.launchAgentCount+' 个相关 LaunchAgent，'+data.coverage.workflowRoutes+' 个路由已登记，'+(data.coverage.routesWithReadableReceipts??0)+' 个流程原回执可读。'+data.coverage.note+'\n待维护登记：'+(data.coverage.unregisteredLabels.join('、')||'无')+'\n目录版本：'+data.catalogVersion+' · 源码路由版本：'+data.registryVersion+'\n在线路由版本：'+(registry.version||'证据不足')+' · 未登记在线路由：'+(registry.unregisteredRouteIds?.join('、')||'无')+'\n源码/在线元数据差异：'+(registry.metadataDriftRouteIds?.join('、')||'无已确认差异');
  if(drawer.open&&selected)detail(selected);
 }
 async function load(force=false){if(busy)return;const token=epoch;busy=true;refresh.disabled=detect.disabled=true;notice.textContent='正在只读检测本机进程、心跳与队列…';try{const r=await window.studio.workflow({operation:force?'refresh':'snapshot'});if(token!==epoch||root.hidden)return;phase=r.phase;if(r.data){data=r.data;history=r.history||[];render();}else{data=null;history=[];selected=null;if(drawer.open)drawer.close();notice.textContent=r.message||'本机状态读取失败，请重试；其他模块不受影响';summaries.replaceChildren();body.replaceChildren();rail.replaceChildren();coverageText.textContent='';count.textContent='暂无可显示的监控数据';pageLabel.textContent='—';prev.disabled=next.disabled=true;}}catch{if(token===epoch&&!root.hidden)notice.textContent='本机状态读取失败，请重试';}finally{busy=false;refresh.disabled=detect.disabled=false;if(token!==epoch&&!root.hidden)queueMicrotask(()=>load(false));}}
 function arm(){clearInterval(timer);timer=setInterval(()=>{if(!root.hidden&&!document.hidden&&check.checked&&!document.body.classList.contains('studio-auth-locked'))load(true);},60000);}
 window.hideStudioWorkflow=(restoreHeader=true)=>{if(root.hidden)return;epoch++;root.hidden=true;clearInterval(timer);if(drawer.open)drawer.close();nav.classList.remove('active');if(restoreHeader)document.querySelector('.topbar').hidden=false;};
 window.openStudioWorkflow=async()=>{await window.studio.close();window.hideStudioBar?.();window.hideStudioContent?.();window.hideStudioContentSources?.();window.hideStudioAnalytics?.();window.hideStudioHome?.();window.hideStudioTasks?.();window.hideStudioWebsite?.();for(const page of root.parentNode.children)if(page!==root&&page.tagName==='SECTION')page.hidden=true;document.querySelector('.topbar').hidden=true;root.hidden=false;epoch++;document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b===nav));arm();if(data)render();await load(true);};
 nav.onclick=()=>safely(()=>window.openStudioWorkflow());refresh.onclick=()=>load(false);detect.onclick=()=>load(true);search.oninput=filter.onchange=sort.onchange=()=>{page=1;render();};prev.onclick=()=>{page--;render();};next.onclick=()=>{page++;render();};
 new MutationObserver(()=>{if(!root.hidden&&workflowVisiblePeer(root.parentNode.children,root))window.hideStudioWorkflow(false);}).observe(root.parentNode,{attributes:true,subtree:true,childList:true,attributeFilter:['hidden']});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!root.hidden)load(true);});new MutationObserver(()=>{if(document.body.classList.contains('studio-auth-locked')){window.hideStudioWorkflow();data=null;history=[];selected=null;body.replaceChildren();summaries.replaceChildren();}}).observe(document.body,{attributes:true,attributeFilter:['class']});
})();
