'use strict';
const {SCHEMA_VERSION}=require('./analytics-schema.cjs');
const SITE='example.invalid',DAYS=[7,14,30,90];
const number=value=>value!==null&&value!==undefined&&value!==''&&Number.isFinite(Number(value))?Number(value):null;
const text=value=>typeof value==='string'?value.slice(0,2000):'';
const fields=(value,keys)=>Object.fromEntries(keys.filter(k=>value?.[k]!==undefined).map(k=>[k,number(value[k])]));
function pathOnly(value){try{const p=String(value||'').split(/[?#]/)[0];return p.startsWith('/')&&!p.includes('://')?p.slice(0,300):'';}catch{return '';}}
function validateDays(value){if(!DAYS.includes(value))throw new Error('analytics_days_invalid');return value;}
function dateKey(value){return new Date(value).toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});}
function delta(value,previous,available=true){const a=number(value),b=number(previous);return available&&a!==null&&b!==null&&b>0?Math.round((a/b-1)*10000)/100:null;}
const metricsKeys=['pv','uv','sessions','avgDurationSeconds','durationKnownViews','durationKnownSessions','durationCoverage'];
function metrics(data,source){const result=fields(data?.metrics,metricsKeys);if(source==='rum'){
 if(data?.dataQuality?.sessionCoverage?.complete!==true)result.sessions=null;
 if(!['ready','complete','observed'].includes(data?.dataQuality?.businessEventCoverage?.status)||data?.dataQuality?.businessEventCoverage?.complete===false)result.avgDurationSeconds=null;
 }return result;}
function projectSummary(summary,days){
 validateDays(days);const anchor=summary.updatedAt;if(!Number.isFinite(Date.parse(anchor)))throw new Error('analytics_clock_invalid');
 const output={overview:[],pages:[],channels:[],goals:[]};
 const rum=summary.dataSource==='tencent-rum'?summary:summary.layers?.rum?.summary;
 if(!rum||rum.dataSource!=='tencent-rum')throw new Error('analytics_rum_source_required');
 const layers={rum:{status:summary.layers?.rum?.status||'ready',summary:rum}};
 for(const [source,layer] of Object.entries(layers)){
  if(source!=='rum')continue;
  const data=layer.summary;if(!data)continue;
  const label='腾讯云 RUM',date=dateKey(anchor),prefix=[SITE,date,days,source].join('|');
  const common={'统计日期':date,'站点':SITE,'来源':label,'窗口天数':days,'周期':'当前','窗口起':text(data.range?.start),'窗口止':text(data.range?.end),'更新时间':text(data.updatedAt||anchor),'口径版本':SCHEMA_VERSION,'状态':layer.status||'ready'};
  const coverage=data.coverage||{};const current=metrics(data,source);const previous=metrics({metrics:data.previousPeriod?.metrics,dataQuality:{...data.dataQuality,sessionCoverage:coverage.previous,businessEventCoverage:data.previousPeriod?.businessEventCoverage||data.dataQuality?.businessEventCoverage}},source);
  const dwellCoverage=number(data.engagement?.durationCoverage??coverage.durationCoverage);
  const comparison=coverage.comparisonAvailable!==false&&data.dataQuality?.comparisonAvailable!==false&&!!data.previousPeriod?.metrics;
  const chartScope=data.dataQuality?.trendEnvironmentScope||'same-source';
  const safeCoverage=value=>value?{status:text(value.status),source:text(value.source),complete:value.complete===true,start:text(value.start),end:text(value.end),requestedStart:text(value.requestedStart),retainedDays:number(value.retainedDays),totalLogs:number(value.totalLogs),acceptedLogs:number(value.acceptedLogs),acceptedEvents:number(value.acceptedEvents),invalidLogs:number(value.invalidLogs),truncated:value.truncated===true,reason:text(value.reason),observedEventNames:(value.observedEventNames||[]).map(text)}:null;
  const technical={queryErrors:Object.fromEntries(Object.entries(data.dataQuality?.dimensionErrors||{}).filter(([,v])=>v).map(([k,v])=>[k,{code:text(v.code),action:text(v.action),providerCode:text(v.providerCode)}])),performance:fields(data.performance,['firstScreenMs','lcpMs','inpMs','cls','apiAverageMs','staticAverageMs','fcpMs']),qualityMetrics:fields(data.metrics,['pageErrors','score','pagePerformanceScore','jsErrorScore']),previousPageSources:(data.previousPageSources||[]).map(v=>({name:text(v.name),pv:number(v.pv)})),devices:(data.devices||[]).map(v=>({name:text(v.name||v.type),pv:number(v.pv),uv:number(v.uv),count:number(v.count)})),browsers:(data.browsers||[]).map(v=>({name:text(v.name),count:number(v.count??v.pv)})),operatingSystems:(data.operatingSystems||[]).map(v=>({name:text(v.name),count:number(v.count??v.pv)})),regions:(data.regions||[]).map(v=>({name:text(v.name),count:number(v.count)})),coverage:{status:text(coverage.current?.status||coverage.status),durationCoverage:dwellCoverage,durationKnownViews:number(coverage.current?.durationKnownViews),durationKnownSessions:number(data.engagement?.durationKnownSessions),currentAvailable:coverage.currentAvailable!==false,comparisonAvailable:comparison},measurementBasis:text(data.dataQuality?.measurementBasis),pageCoverage:safeCoverage(data.dataQuality?.pageJourneyCoverage),sessionCoverage:safeCoverage(data.dataQuality?.sessionCoverage),businessEventCoverage:safeCoverage(data.dataQuality?.businessEventCoverage),trendEnvironmentScope:chartScope,detail:text(data.dataQuality?.detail),pagePaths:{homeNextPages:(data.pagePaths?.homeNextPages||[]).map(v=>({path:pathOnly(v.path),sessions:number(v.sessions??v.count)}))}};
  const trends=(data.trend||[]).map(point=>({date:text(point.date),...fields(point,['pv','uv','sessions','avgDurationSeconds'])}));
  output.overview.push({...common,'记录键':prefix+'|window','浏览量':current.pv??null,'访客数':current.uv??null,'会话数':current.sessions??null,'平均停留秒':current.avgDurationSeconds??null,'时长覆盖率':dwellCoverage,'指标JSON':JSON.stringify({source,current,previous,comparisonAvailable:comparison,range:data.range,previousRange:data.previousPeriod?.range||null}),'趋势JSON':JSON.stringify(trends),'技术JSON':JSON.stringify(technical)});
  for(const page of data.topPages||[]){const pathname=pathOnly(page.path);if(!pathname)continue;output.pages.push({...common,'记录键':prefix+'|page|'+pathname,'页面路径':pathname,'浏览量':number(page.pv),'访客数':number(page.uv),'会话数':number(page.sessions),'指标JSON':JSON.stringify(fields(page,['pv','uv','sessions','entries','exits','singlePageSessions','avgDurationSeconds','durationKnownViews','durationKnownSessions','durationCoverage']))});}
  for(const channel of data.sources||[]){if(!number(channel.pv)&&!number(channel.uv))continue;const name=text(channel.name);output.channels.push({...common,'记录键':prefix+'|channel|'+name,'渠道':name,'浏览量':number(channel.pv),'访客数':number(channel.uv),'指标JSON':JSON.stringify(fields(channel,['pv','uv','sessions']))});}
  if(data.cta){for(const item of data.cta.items||data.cta.byCta||[]){const ctaId=text(item.ctaId||item.id||item.label),placement=text(item.placement);const id=ctaId+(placement?'@'+placement:'');if(!ctaId)continue;output.goals.push({...common,'记录键':prefix+'|cta|'+id,'目标事件':id,'曝光':number(item.impressions),'点击':number(item.clicks),'会话数':number(item.sessions),'指标JSON':JSON.stringify({kind:'cta',ctaId,placement,...fields(item,['impressions','clicks','sessions'])})});}}
  for(const action of data.businessActions||[]){const id=text(action.eventName);if(!id)continue;output.goals.push({...common,'记录键':prefix+'|action|'+id,'目标事件':id,'曝光':null,'点击':number(action.events),'会话数':number(action.sessions),'指标JSON':JSON.stringify({kind:'action',...fields(action,['events','sessions'])})});}
  for(const step of data.wechatFunnel?.steps||[]){const id=text(step.id||step.eventName);if(!id)continue;output.goals.push({...common,'记录键':prefix+'|goal|'+id,'目标事件':id,'曝光':null,'点击':number(step.count??step.events),'会话数':number(step.sessions),'指标JSON':JSON.stringify({kind:'wechat',...fields(step,['count','events','sessions'])})});}
 }
 if(!output.overview.length)throw new Error('analytics_sources_unavailable');return output;
}
function cell(value){if(Array.isArray(value))return value.map(x=>x?.text??x?.name??x).join('');if(value&&typeof value==='object'&&'value'in value)return cell(value.value);return value;}
function rowFields(row){return Object.fromEntries(Object.entries(row.fields||row.values||row).map(([k,v])=>[k,cell(v)]));}
function json(value,fallback){try{return JSON.parse(value);}catch{return fallback;}}
function dataset(records,days,now=new Date()){
 const overview=(records.overview||[]).map(rowFields).filter(r=>number(r['窗口天数'])===days).sort((a,b)=>String(b['更新时间']).localeCompare(String(a['更新时间'])));
 const layers=[];for(const source of ['rum']){const row=overview.find(r=>r['来源']==='腾讯云 RUM');if(!row)continue;
  const measure=json(row['指标JSON'],{});if(measure.source!=='rum')throw new Error('analytics_source_mismatch');const date=row['统计日期'];const select=key=>(records[key]||[]).map(rowFields).filter(r=>r['来源']===row['来源']&&r['统计日期']===date&&r['更新时间']===row['更新时间']&&number(r['窗口天数'])===days);
  layers.push({id:source,label:row['来源'],updatedAt:row['更新时间'],snapshotDate:date,snapshotIsPreviousDay:date<dateKey(now),state:row['状态'],...measure,trend:json(row['趋势JSON'],[]),technical:json(row['技术JSON'],{}),pages:select('pages').map(r=>({path:r['页面路径'],...json(r['指标JSON'],{})})),channels:select('channels').map(r=>({name:r['渠道'],...json(r['指标JSON'],{})})),goals:select('goals').map(r=>({id:r['目标事件'],...json(r['指标JSON'],{})}))});
 }
 return {days,layers,sourcePolicy:'rum-only',definitions:(records.definitions||[]).map(rowFields).filter(r=>r['来源']!=='服务器日志'),insights:(records.insights||[]).map(rowFields),phase:layers.length?'ok':'empty',readAt:new Date().toISOString()};
}
module.exports={SITE,DAYS,number,delta,pathOnly,validateDays,projectSummary,dataset,rowFields,dateKey};
