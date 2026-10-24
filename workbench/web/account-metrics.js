'use strict';
// Shared rendering contract: raw source metrics never become inferred totals.
(function(root){
 const segments={video:'视频/图文',dynamic:'动态',article:'文章'},statuses={available:'来源已返回',missing:'未提供',unverified:'待核实',conflict:'来源冲突',unsupported:'当前接口未提供'};
 function number(value){return typeof value==='number'&&Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:20}): '—';}
 function displayNumber(metric){
  if(metric.value===null||metric.value===undefined)return '—';
  if(metric.mappingPolicy==='douyin.fans_index_as_display_total.v1')return number(metric.value);
  if(metric.approximate&&typeof metric.displayValue==='string')return '≈'+metric.displayValue;
  if(metric.unit&&typeof metric.displayValue==='string')return metric.displayValue;
  return number(metric.value)+(metric.unit||'');
 }
 function model(account){
  const m=account.accountMetrics;
  if(m?.totalFans)return {...m,canonicalTotalFans:{...m.totalFans},totalFans:{...(m.displayTotalFans||m.totalFans)},overview:Array.isArray(m.overview)?m.overview:[]};
  const value=typeof account.fans==='number'&&account.fans>0?account.fans:null;
  return {totalFans:{key:'fansTotal',label:'总粉丝',value,status:account.fans===0?'unverified':value===null?'missing':'available',reportedValue:account.fans===0?0:undefined,source:'yxer.accounts.list',fetchedAt:account.fansUpdatedAt,updatedAt:null,reason:account.fans===0?'来源返回0，真实值待核实':'仅有账号清单数据，平台更新时间未提供'},overview:[],cached:true,overviewStatus:'missing',backendSupplement:'缺失字段由科学家核心对应账号接口补齐'};
 }
 const priority=['playCount','greatCount','commentCount','shareCount','netFansCount','collectCount','cancelFansCount','homepageViewCount','likeCount'];
 function managementColumns(){return [{id:'total',key:'total',label:'总粉丝',segment:'account'}];}
 function columns(accounts){
  const found=new Map();for(const a of accounts)for(const x of model(a).overview){if(x.key==='totalFansCount'||x.key==='fansIndex')continue;const id=(x.segment||'video')+':'+x.key;if(!found.has(id))found.set(id,{id,key:x.key,label:x.label,segment:x.segment||'video'});}
  const rank=x=>{const i=priority.indexOf(x.key);return (x.segment==='video'?0:x.segment==='dynamic'?100:200)+(i<0?40:i);};
  return [...managementColumns(),...[...found.values()].sort((a,b)=>rank(a)-rank(b)||a.label.localeCompare(b.label,'zh-CN'))];
 }
 function reading(a,c){const m=model(a);return c.id==='total'?m.totalFans:m.overview.find(x=>(x.segment||'video')+':'+x.key===c.id)||{value:null,status:'missing',label:c.label};}
 function makeValue(a,c,{node,time}){const m=model(a),x=reading(a,c),pending=m.scientistBackend?.state,pendingLabel=pending==='identity_pending'?'身份待核验':pending==='authorization_pending'?'授权待核验':null,e=node('span','metric-number'+(x.value===null?' metric-unavailable':'')+(c.id==='total'?' metric-total':''),x.value===null?(c.id==='total'?(x.status==='unsupported'?statuses.unsupported:pendingLabel||statuses[x.status]||'未提供'):'—'):displayNumber(x));e.title=[x.label,x.source?x.source+' · '+x.key:'来源未提供',x.rawValue!==undefined?'来源原值：'+String(x.rawValue):null,x.approximate?'来源为缩写显示值；换算是近似名义量，不代表精确整数':null,x.unit?'单位：'+x.unit:'单位未提供',x.fetchedAt?'实际查询 '+time(x.fetchedAt):null,x.updatedAt?'平台更新 '+time(x.updatedAt):'平台更新时间未提供',x.reason,c.id==='total'?m.scientistBackend?.reason:null,x.definition].filter(Boolean).join('\n');if(x.stale||x.cached||m.overviewCached||m.refreshError)e.classList.add('metric-historical');if(x.status==='conflict')e.classList.add('metric-conflict');return e;}
 function sourceSummary(a,{node,time}){const m=model(a),x=m.totalFans,e=node('div','metric-source');e.append(node('span','',m.refreshError||m.overviewError?'部分查询失败':m.overview.some(v=>v.source?.startsWith('data-scientist.'))?'蚁小二＋科学家':x.status==='conflict'?'来源冲突':x.stale||m.overview.some(v=>v.stale)?'历史快照':'蚁小二'));e.append(node('small','',m.overviewUpdatedAt?time(m.overviewUpdatedAt):x.updatedAt?time(x.updatedAt):'更新时间未提供'));e.title='实际查询：'+(m.fetchedAt?time(m.fetchedAt):x.fetchedAt?time(x.fetchedAt):'未提供')+'；统计周期未提供';return e;}
 function makeCell(a,{node,time,onDetail}){const e=node('div','account-metrics-strip');for(const c of managementColumns()){const item=node('div','account-metric');item.append(node('span','metric-label',c.label),makeValue(a,c,{node,time}));e.append(item);}e.append(sourceSummary(a,{node,time}));const b=node('button','metric-detail-button','指标详情');b.onclick=()=>onDetail(a);e.append(b);return e;}
 function showDetail(a,{node,time}){
  const m=model(a),d=node('dialog','account-metrics-dialog'),header=node('header'),close=node('button','icon-button','×');close.type='button';close.setAttribute('aria-label','关闭指标详情');close.onclick=()=>d.close();header.append(node('h2','',a.name+' · 账号数据'),close);d.append(header);
  d.append(node('p','metric-note','蚁小二授权与本浏览器登录分别判断；统计周期未返回时不称为今日或近7日数据；比率/时长保留源接口原始数值，单位未提供时不换算。'));
  if(m.refreshError||m.overviewError)d.append(node('p','refresh-error','部分查询失败：'+(m.refreshError||m.overviewError)+'。已有值保留，缺失不补零。'));
  const scroll=node('div','metric-detail-scroll'),table=node('table','table metric-detail-table'),head=node('tr'),body=node('tr');
  for(const c of columns([a])){const th=node('th');th.append(node('span','',c.label),node('small','metric-note',segments[c.segment]||'账号'));head.append(th);const td=node('td');td.append(makeValue(a,c,{node,time}));body.append(td);}
  const thead=node('thead'),tbody=node('tbody');thead.append(head);tbody.append(body);table.append(thead,tbody);scroll.append(table);d.append(scroll);
  const source=node('div','metric-detail-source');source.append(sourceSummary(a,{node,time}),node('span','metric-note','查询 '+(m.fetchedAt?time(m.fetchedAt):'未提供')+' · 统计周期未提供 · ≈表示来源缩写值，非精确整数'));d.append(source);
  if(m.totalFans.reportedValue===0)d.append(node('p','metric-note','账号清单原始返回0；不将其当作已核实的真实总粉丝数。'));
  if(m.totalFans.listReportedValue!==undefined)d.append(node('p','metric-note','账号清单原始总粉丝：'+number(m.totalFans.listReportedValue)+'；数据概览与其不同，待核实。'));
  if(m.totalFans.overviewReportedValue!==undefined)d.append(node('p','metric-note','概览原始缩写：'+m.totalFans.overviewReportedValue+'；总粉丝列保留已取得的精确整数。'));
  if(!m.overview.length)d.append(node('p','metric-note','该账号的数据概览未提供有效指标，不等于没有数据。'));
  if(m.totalFans.reason)d.append(node('p','metric-note',m.totalFans.reason));
  if(m.totalFans.mappingPolicy)d.append(node('p','metric-note','业务展示映射已由用户确认；原接口字段、原指标名及单位保留在数据追溯中。'));
  if(m.totalFans.value===null)d.append(node('p','metric-note',m.backendSupplement||'缺失指标待补'));
  d.addEventListener('close',()=>d.remove(),{once:true});document.body.append(d);d.showModal();return d;
 }
 const api={model,number,displayNumber,managementColumns,columns,reading,makeValue,sourceSummary,makeCell,showDetail};if(typeof module==='object'&&module.exports)module.exports=api;else root.StudioAccountMetrics=api;
})(typeof globalThis==='object'?globalThis:this);
