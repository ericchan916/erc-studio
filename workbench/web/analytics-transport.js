'use strict';
// Browser-only transport. Accepted sync jobs are polled, never resubmitted after
// an observation failure; an uncertain POST keeps its original idempotency key.
const StudioAnalyticsTransport=(()=>{
 function create({request,emit,uuid,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}){
  const reads=new Map(),watching=new Map();let epoch=0,submission=null,accepted=null,syncing=null;
  const fail=(code,message)=>Object.assign(Error(message||code),{code});
  const valid=days=>{if(![7,14,30,90].includes(days))throw fail('invalid_request');};
  const current=ticket=>{if(ticket!==epoch)throw fail('analytics_identity_changed','登录身份已变化；原同步保留在后端。');};
  const event=(value,ticket)=>{if(ticket===epoch)emit(value);};
  function read(days){valid(days);if(reads.has(days))return reads.get(days);const ticket=epoch;
   const promise=request('/v1/workspace/snapshot?kind=analytics&days='+days+'&refresh=1').then(value=>{current(ticket);return value;}).finally(()=>{if(reads.get(days)===promise)reads.delete(days);});reads.set(days,promise);return promise;
  }
  function watch(job,ticket){
   if(watching.has(job.id))return watching.get(job.id);
   const promise=(async()=>{
    while(true){current(ticket);
     if(job.state==='succeeded'){if(!job.snapshot||job.snapshot.stale!==false||job.snapshot.phase!=='ok')throw fail('analytics_protocol_invalid');if(accepted?.id===job.id)accepted=null;event({snapshot:job.snapshot},ticket);event({syncPhase:'idle'},ticket);return job.snapshot;}
     if(job.state==='failed'){if(accepted?.id===job.id)accepted=null;throw fail(job.error||'analytics_operation_failed','本次同步未完成：'+(job.error||'统计服务异常')+'；原数据保留。');}
     if(job.state==='execution_unknown')throw fail('analytics_sync_unknown','原同步结果待核验；下次点击只核验原任务，不重复同步。');
     if(!['accepted','running'].includes(job.state))throw fail('analytics_protocol_invalid');
     event({syncPhase:'collecting',syncDays:job.days},ticket);await sleep(1000);current(ticket);job=await request('/v1/analytics/jobs/'+job.id);
    }
   })().catch(error=>{event({syncPhase:'error',syncError:error.message},ticket);throw error;}).finally(()=>{if(watching.get(job.id)===promise)watching.delete(job.id);});watching.set(job.id,promise);return promise;
  }
  async function open(days){valid(days);const ticket=epoch;const [snapshot,state]=await Promise.all([request('/v1/workspace/snapshot?kind=analytics&days='+days),request('/v1/analytics/state')]);current(ticket);snapshot.canSync=state.canSync;
   if(state.activeJob){accepted=state.activeJob;snapshot.syncPending=true;void watch(state.activeJob,ticket).catch(()=>{});}return snapshot;
  }
  function sync(days){valid(days);if(syncing){if(syncing.days!==days)return Promise.reject(fail('analytics_sync_busy','已有官网同步正在执行。'));return syncing.promise;}
   const ticket=epoch;const promise=(async()=>{
    const state=await request('/v1/analytics/state');current(ticket);if(!state.canSync)throw fail('analytics_sync_permission_denied','仅本人管理员可同步RUM；其他成员可刷新原表。');
    let job=accepted?await request('/v1/analytics/jobs/'+accepted.id):state.activeJob;current(ticket);
    if(job?.state==='execution_unknown'){job=await request('/v1/analytics/jobs/'+job.id+'/verify',{method:'POST',body:{},key:uuid()});current(ticket);return watch(job,ticket);}
    if(job){if(job.days!==days)throw fail('analytics_sync_busy','另一个统计窗口正在同步，请等待原任务。');accepted=job;return watch(job,ticket);}
    if(submission&&submission.days!==days)throw fail('analytics_sync_unknown','上次同步受理回执尚未确认，请返回原统计窗口核验。');
    submission||={days,key:uuid()};job=await request('/v1/analytics/sync',{method:'POST',body:{days},key:submission.key});current(ticket);submission=null;accepted=job;return watch(job,ticket);
   })().finally(()=>{if(syncing?.promise===promise)syncing=null;});syncing={days,promise};return promise;
  }
  function reset(){epoch++;reads.clear();watching.clear();submission=null;accepted=null;syncing=null;}
  return {open,read,sync,reset};
 }
 return {create};
})();
if(typeof module==='object'&&module.exports)module.exports=StudioAnalyticsTransport;else window.StudioAnalyticsTransport=StudioAnalyticsTransport;
