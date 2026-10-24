'use strict';
// Trusted gateway worker: fixed root, fixed CLI identity, existing RUM adapter.
const fs=require('node:fs'),path=require('node:path');
const base=fs.existsSync(path.join(__dirname,'analytics.cjs'))?__dirname:path.resolve(__dirname,'../src');
const {AnalyticsService}=require(path.join(base,'analytics.cjs'));
const {projectSummary,dataset}=require(path.join(base,'analytics-model.cjs'));
const {collect}=require(path.join(base,'analytics-source.cjs'));
const Cache=require(path.join(base,'view-cache.cjs'));
async function execute(root,action,days,expectedActor){
 let mayHaveWritten=false,expected=null;
 const service=new AnalyticsService(root,{onChange:value=>{if(['writing','verifying'].includes(value.syncPhase))mayHaveWritten=true;},collect:async(...args)=>{
  const summary=await collect(...args),layer=dataset(projectSummary(summary,days),days).layers[0];
  expected={days,updatedAt:layer.updatedAt,current:layer.current,pages:layer.pages,channels:layer.channels,goals:layer.goals};return summary;
 }});
 try{
  if(action==='sync'){
   const actor=await service.actor();
   if(!expectedActor||actor!==expectedActor||Cache.localIdentity()!==actor)throw Error('analytics_identity_changed');
  }
  const result=action==='sync'?await service.sync(days):await service.read(days);
  if(!['ok'].includes(result.phase)||result.stale!==false)throw Error(result.error||'analytics_live_read_failed');
  return {ok:true,snapshot:result};
 }catch(error){return {ok:false,error:/^analytics_[A-Za-z0-9._-]{1,128}$/.test(error.message)?error.message:'analytics_operation_failed',mayHaveWritten,expected};}
}
async function main(){
 const [root,action,rawDays,expectedActor]=process.argv.slice(2),days=Number(rawDays);
 if(root!==path.join(process.env.HOME,'Library/Application Support/ERC Studio')||!['read','sync'].includes(action)||![7,14,30,90].includes(days))throw Error('analytics_request_invalid');
 const result=await execute(root,action,days,expectedActor);process.stdout.write(JSON.stringify(result));if(!result.ok)process.exitCode=1;
}
if(require.main===module)main().catch(()=>{process.stderr.write(JSON.stringify({ok:false,error:'analytics_request_invalid'}));process.exitCode=1;});
module.exports={execute};
