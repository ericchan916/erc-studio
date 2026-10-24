'use strict';
const cp=require('node:child_process'),fs=require('node:fs'),path=require('node:path');
const Cache=require('./view-cache.cjs');
const {runCLI}=require('./content/client.cjs');
const {TABLES,DEFINITIONS,SCHEMA_VERSION}=require('./analytics-schema.cjs');
const {validateDays,projectSummary,dataset,rowFields}=require('./analytics-model.cjs');
const CLI=process.env.CONTENTBASE_LARK_CLI||'lark-cli',PROFILE=process.env.CONTENTBASE_LARK_PROFILE||'';
function cli(args){return new Promise((resolve,reject)=>{cp.execFile(CLI,[...args,'--format','json','--profile',PROFILE,'--as','user'],{timeout:90000,maxBuffer:20*1024*1024,env:{...process.env,LARKSUITE_CLI_NO_UPDATE_NOTIFIER:'1',LARKSUITE_CLI_NO_SKILLS_NOTIFIER:'1'}},(error,stdout,stderr)=>{let result;try{result=JSON.parse(error?stderr:stdout);}catch{}if(error||!result?.ok){const e=new Error(result?.error?.type==='authorization'?'analytics_lark_authorization_required':'analytics_lark_query_failed');e.signal=result?.error||{type:'transport'};return reject(e);}resolve(result.data);});});}
function items(value,kind){const list=value?.[kind]||value?.items||value?.list||[];if(!Array.isArray(list))throw new Error('analytics_lark_payload_invalid');return list;}
function save(file,value){fs.mkdirSync(path.dirname(file),{recursive:true,mode:0o700});const tmp=file+'.tmp';fs.writeFileSync(tmp,JSON.stringify(value,null,2),{mode:0o600});fs.renameSync(tmp,file);fs.chmodSync(file,0o600);}
function readConfig(root,complete=true){try{const v=JSON.parse(fs.readFileSync(path.join(root,'analytics-space.json'),'utf8'));if(v.version!==1||!/^\w{8,}$/.test(v.baseToken)||!v.tables||(complete&&!Object.keys(TABLES).every(k=>/^tbl\w+$/.test(v.tables?.[k]||''))))throw 0;return v;}catch(e){if(e.code==='ENOENT')return null;throw new Error('analytics_config_invalid');}}
async function records(config,key,days,run=cli,{date,updatedAt,latest=false}={}){
 const args=['base','+record-list','--base-token',config.baseToken,'--table-id',config.tables[key],'--limit',latest?'1':'200'];
 if(days&&['overview','pages','channels','goals'].includes(key)){
  const conditions=[['窗口天数','==',days],['来源','==','腾讯云 RUM']];
  if(date)conditions.push(['统计日期','==',date]);if(updatedAt)conditions.push(['更新时间','==',updatedAt]);
  args.push('--filter-json',JSON.stringify({logic:'and',conditions}));
 }
 // Explicit top-one selection, not a truncated full-table analysis. Reads survive midnight.
 if(latest)args.push('--sort-json',JSON.stringify([{field:'更新时间',desc:true}]));
 const result=[];for(let offset=0;offset<2000;){const data=await run([...args,'--offset',String(offset)]);const batch=Array.isArray(data.data)&&Array.isArray(data.fields)?data.data.map((row,index)=>({record_id:data.record_id_list?.[index],fields:Object.fromEntries(data.fields.map((name,column)=>[name,row[column]]))})):items(data,'records');result.push(...batch);if(latest||!data.has_more&&!data.hasMore&&!data.pagination?.has_more)return result;if(!batch.length)throw new Error('analytics_pagination_invalid');offset+=batch.length;}throw new Error('analytics_records_truncated');
}
async function upsert(config,key,rows,run=cli){
 if(!rows.length)return {created:0,updated:0};
 // Bitable number cells keep 15 significant digits; text/JSON and integer counters stay exact.
 rows=rows.map(row=>Object.fromEntries(Object.entries(row).map(([name,value])=>[name,typeof value==='number'&&!Number.isInteger(value)?Number(value.toPrecision(15)):value])));
 const schema=await run(['base','+field-list','--base-token',config.baseToken,'--table-id',config.tables[key]]);
 const fieldNames=new Set(items(schema,'fields').map(f=>f.name));for(const row of rows)for(const name of Object.keys(row))if(!fieldNames.has(name))throw new Error('analytics_schema_mismatch');
 const scope={date:rows[0]['统计日期']};
 if(rows.some(row=>row['窗口天数']!==rows[0]['窗口天数']||row['统计日期']!==scope.date))throw new Error('analytics_write_scope_mismatch');
 if(new Set(rows.map(row=>row['记录键'])).size!==rows.length)throw new Error('analytics_duplicate_input_key');
 const existing=await records(config,key,rows[0]['窗口天数'],run,scope),index=new Map();
 for(const record of existing){const name=rowFields(record)['记录键'];if(index.has(name))throw new Error('analytics_duplicate_business_key');index.set(name,record.record_id||record.id);}
 const creates=[],updates={};for(const row of rows){const id=index.get(row['记录键']);if(id)updates[id]=row;else creates.push(row);}
 for(let i=0;i<creates.length;i+=100){const args=['base','+record-batch-create','--base-token',config.baseToken,'--table-id',config.tables[key],'--json',JSON.stringify({create_records:creates.slice(i,i+100)})];await run([...args,'--dry-run']);await run(args);}
 const updateEntries=Object.entries(updates);for(let i=0;i<updateEntries.length;i+=100){const args=['base','+record-batch-update','--base-token',config.baseToken,'--table-id',config.tables[key],'--json',JSON.stringify({update_records:Object.fromEntries(updateEntries.slice(i,i+100))})];await run([...args,'--dry-run']);await run(args);}
 let confirmed=false;for(let attempt=0;attempt<5;attempt++){if(attempt)await new Promise(resolve=>setTimeout(resolve,attempt*700));const verified=await records(config,key,rows[0]['窗口天数'],run,scope),got=new Map(verified.map(r=>{const f=rowFields(r);return [f['记录键'],f];}));confirmed=rows.every(row=>{const found=got.get(row['记录键']);return !!found&&Object.entries(row).every(([name,value])=>value===null?found[name]==null||found[name]==='':found[name]===value);});if(confirmed)break;}
 if(!confirmed)throw new Error('analytics_write_verification_failed');
 return {created:creates.length,updated:updateEntries.length,verified:rows.length};
}
async function initialize(root,run=cli){
 let config=readConfig(root,false);if(!config)throw new Error('analytics_base_anchor_required');
 await run(['base','+base-get','--base-token',config.baseToken]);const catalog=items(await run(['base','+table-list','--base-token',config.baseToken]),'tables');
 for(const [key,table] of Object.entries(TABLES)){
  let id=config.tables[key]||catalog.find(t=>t.name===table.name)?.id;
  if(!id){const args=['base','+table-create','--base-token',config.baseToken,'--name',table.name,'--fields',JSON.stringify(table.fields)];await run([...args,'--dry-run']);const created=await run(args);id=created.table?.id||created.id||created.table_id;}
  if(!/^tbl\w+$/.test(id||''))throw new Error('analytics_table_creation_unverified');config.tables[key]=id;save(path.join(root,'analytics-space.json'),config);
  await run(['base','+field-list','--base-token',config.baseToken,'--table-id',id]);
 }
 const defs=DEFINITIONS.map(([id,metric,source,definition,unit,note])=>({'记录键':id,'指标':metric,'来源':source,'定义':definition,'单位':unit,'版本':SCHEMA_VERSION,'注意事项':note}));await upsert(config,'definitions',defs,run);return config;
}
class AnalyticsService{
 constructor(root,{run=cli,collect=require('./analytics-source.cjs').collect,onChange=()=>{},identityHint,actor}={}){Object.assign(this,{root,run,collect,onChange});this.state={phase:'idle',syncPhase:'idle'};this.inflight=null;this.reading=new Map();this.cacheFile=path.join(root,'analytics-cache.json');this.identityHint=identityHint||(run===cli?Cache.localIdentity:()=>null);this.strictIdentity=run===cli||!!identityHint;this.actor=actor||(run===cli?async()=>{const p=await runCLI(['whoami']);if(p.identity!=='user'||!p.available||p.tokenStatus!=='ready'||!/^ou_/.test(p.onBehalfOf?.openId||''))throw new Error('analytics_identity_unverified');return p.onBehalfOf.openId;}:async()=>null);this.epochs=new Map();this.views=new Map();}
 stateEvent(syncPhase){this.state.syncPhase=syncPhase;this.onChange({...this.state});}
 cached(days,config,actor){for(const file of [path.join(this.root,'analytics-cache-'+days+'.json'),this.cacheFile]){const value=Cache.read(file);if(!value||value.days!==days)continue;
  if(this.strictIdentity&&!Cache.usable(value,config,actor))continue;
  value.layers=(value.layers||[]).filter(x=>x.id==='rum'&&x.source==='rum');value.definitions=(value.definitions||[]).filter(x=>x['来源']!=='服务器日志');return value;
 }return null;}
 open(days=14){validateDays(days);let config;try{config=readConfig(this.root);}catch{return {days,phase:'error',layers:[],stale:false,message:'官网数据配置有误，未展示缓存'};}
  if(!config)return {days,phase:'unconfigured',layers:[],message:'官网数据表尚未配置'};
  const actor=this.identityHint(),memory=this.views.get(days),cached=Cache.usable(memory,config,actor)?memory:this.cached(days,config,actor);
  const result=cached&&(!this.strictIdentity||Cache.usable(cached,config,actor))?{...cached,phase:'cached',stale:true,refreshing:true,message:'上次快照，正在后台核对官网数据'}:{days,phase:'loading',layers:[],stale:false,refreshing:true,message:'正在后台读取官网数据'};
  setImmediate(()=>this.read(days).catch(()=>{}));return Cache.visible(result);
 }
 async read(days=14){validateDays(days);const config=readConfig(this.root),scope=Cache.scope(config,this.identityHint()),current=this.reading.get(days);
  if(current&&current.scope===scope)return current.promise;
  const epoch=(this.epochs.get(days)||0)+1;this.epochs.set(days,epoch);
  const promise=this._read(days,epoch).finally(()=>{if(this.reading.get(days)?.promise===promise)this.reading.delete(days);});this.reading.set(days,{scope,promise});return promise;
 }
 async _read(days,epoch){let config,actorId=null;try{config=readConfig(this.root);if(!config)return {days,phase:'unconfigured',layers:[],message:'官网数据表尚未配置'};
  const hint=this.identityHint();actorId=await this.actor();if(this.strictIdentity&&(!hint||hint!==actorId))throw new Error('analytics_identity_changed');
  const overviewPromise=records(config,'overview',days,this.run,{latest:true});
  const dimensionsPromise=overviewPromise.then(overview=>{const snapshot=overview[0]&&rowFields(overview[0]);return snapshot?Promise.all(['pages','channels','goals'].map(key=>records(config,key,days,this.run,{date:snapshot['统计日期'],updatedAt:snapshot['更新时间']}))):[[],[],[]];});
  const constantsPromise=Promise.all(['definitions','insights'].map(key=>records(config,key,days,this.run)));
  const [overview,dimensions,constants]=await Promise.all([overviewPromise,dimensionsPromise,constantsPromise]);
  if(epoch!==this.epochs.get(days))return {days,layers:[],phase:'superseded'};if(this.strictIdentity&&this.identityHint()!==actorId)throw new Error('analytics_identity_changed');
  const tableRecords={overview,pages:dimensions[0],channels:dimensions[1],goals:dimensions[2],definitions:constants[0],insights:constants[1]};
  const result={...dataset(tableRecords,days),_actorId:actorId,_cacheScope:Cache.scope(config,actorId),stale:false,refreshing:false};
  save(path.join(this.root,'analytics-cache-'+days+'.json'),result);save(this.cacheFile,result);this.views.set(days,result);this.state={...this.state,phase:result.phase};this.onChange({...this.state,snapshot:Cache.visible(result)});return Cache.visible(result);
 }catch(error){if(epoch!==this.epochs.get(days))return {days,layers:[],phase:'idle'};
  const hint=this.identityHint(),allowed=this.strictIdentity?hint:(hint||actorId),cached=this.cached(days,config,allowed);const usable=!!cached&&(!this.strictIdentity||!!actorId&&actorId===allowed);
  const result={...(usable?cached:{days,layers:[]}),sourcePolicy:'rum-only',phase:'error',refreshing:false,stale:usable&&!!cached.layers.length,error:error.message,message:error.message==='analytics_lark_authorization_required'?'飞书查询授权需要处理，已保留同身份快照':'本次查询未成功，未把缓存标为最新数据'};
  this.onChange({...this.state,snapshot:Cache.visible(result)});return Cache.visible(result);
 }}
 async sync(days=14){validateDays(days);if(this.inflight){if(this.syncDays!==days)throw new Error('analytics_sync_busy');return this.inflight;}this.syncDays=days;
 this.inflight=(async()=>{try{const config=readConfig(this.root);if(!config)throw new Error('analytics_config_missing');const actor=await this.actor();if(this.strictIdentity&&(!actor||this.identityHint()!==actor))throw new Error('analytics_identity_changed');this.stateEvent('collecting');const summary=await this.collect(config,this.root,days);const projected=projectSummary(summary,days);const counts={};for(const key of ['pages','channels','goals','overview']){if(this.strictIdentity&&(this.identityHint()!==actor||await this.actor()!==actor))throw new Error('analytics_identity_changed');this.stateEvent('writing');counts[key]=await upsert(config,key,projected[key],this.run);}this.stateEvent('verifying');if(this.reading.has(days))await this.reading.get(days).promise;const result=await this.read(days),expected=projected.overview[0];if(result.phase!=='ok'||result.layers[0]?.updatedAt!==expected['更新时间']||result.layers[0]?.current.pv!==expected['浏览量']||result.layers[0]?.current.uv!==expected['访客数'])throw new Error('analytics_readback_failed');this.stateEvent('idle');return {...result,syncCounts:counts};}catch(error){this.stateEvent('error');const safeCode=value=>typeof value==='string'&&/^[A-Za-z0-9._-]{1,128}$/.test(value)?value:'';throw new Error(error.message.startsWith('analytics_')?error.message:error.provider==='tencent-rum'?'analytics_rum_'+(safeCode(error.providerCode)||safeCode(error.code)||'query_failed'):'analytics_collection_failed');}})().finally(()=>{this.inflight=null;});return this.inflight;
 }
}
module.exports={AnalyticsService,cli,items,records,upsert,initialize,readConfig,save,PROFILE};
