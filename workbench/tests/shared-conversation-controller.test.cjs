'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {ConversationController}=require('../web/conversation-controller.js');
const {ConversationState}=require('../web/conversation-state.js');
const tick=()=>new Promise(r=>setImmediate(r));
function fixture(state='waiting_input'){
 const cid=crypto.randomUUID(),jid=crypto.randomUUID(),mid=crypto.randomUUID(),calls=[],timers=new Map();let override=null,binding={conversation_id:'other',revision:3},job={job_id:jid,conversation_id:cid,state,notice:'原问题',interaction:{kind:'question',question:'原问题',options:['A','B']}};
 const member={principal_id:'a',display_name:'A',capabilities:{shared_context:true}},meta={conversation_id:cid,title:'old',version:1,archived_at:null,deleted_at:null};
 const api=async(op,data={})=>{calls.push({op,data:structuredClone(data)});if(override){const result=override(op,data);if(result)return result;}
  if(op==='detail')return {...meta};if(op==='conversations')return {items:[{...meta}],revision:1,next_cursor:null};
  if(op==='messages')return {items:[{message_id:mid,seq:1000000,role:'user',text:'first',job_id:jid,job_state:job.state}],version:meta.version,conversation:{...meta}};
  if(op==='job')return {...job};if(op==='active')return {...binding};
  if(op==='continue'){binding={conversation_id:data.conversation_id,revision:data.expected_revision+1};return {...binding};}
  if(op==='respond'){assert.equal(data.job_id,jid);job.state='queued';return {job_id:jid,conversation_id:cid,message_id:mid,state:'queued'};}
  if(op==='recover'){job.state='succeeded';return {...job,recovered:true};}
  if(op==='cancel'){job.state='cancelled';return {...job};}
  throw Error('unexpected '+op);
 };
 const controller=new ConversationController({api,uuid:crypto.randomUUID,schedule:fn=>{const id=crypto.randomUUID();timers.set(id,fn);return id;},unschedule:id=>timers.delete(id)});controller.identity(member);
 return {controller,cid,jid,mid,calls,member,meta,job,override:fn=>override=fn,binding:()=>binding,close:()=>controller.leave()};
}
test('viewing a historical conversation never changes shared binding; explicit continue does',async()=>{
 const f=fixture('succeeded');try{await f.controller.select(f.cid);assert.equal(f.calls.filter(x=>['active','continue'].includes(x.op)).length,0);await f.controller.continueConversation();const command=f.calls.find(x=>x.op==='continue');assert.equal(command.data.expected_revision,3);assert.equal(command.data.conversation_id,f.cid);assert.equal(f.binding().conversation_id,f.cid);}finally{f.close();}
});
test('uncertain continue retries reuse original CAS and idempotency even after a remote switch',async()=>{
 const f=fixture('succeeded');try{await f.controller.select(f.cid);let fail=true;f.override(op=>op==='continue'&&fail?Promise.reject(Error('offline')):null);await assert.rejects(()=>f.controller.continueConversation());const original=f.calls.find(x=>x.op==='continue').data;fail=false;await f.controller.continueConversation();assert.deepEqual(f.calls.filter(x=>x.op==='continue').at(-1).data,original);}finally{f.close();}
});
test('waiting input sends to original job, never the start/send endpoint',async()=>{
 const f=fixture();try{await f.controller.select(f.cid);await tick();await f.controller.send('A');const submitted=f.calls.find(x=>x.op==='respond');assert.equal(submitted.data.job_id,f.jid);assert.equal(submitted.data.text,'A');assert.ok(!f.calls.some(x=>['start','send','retry'].includes(x.op)));assert.equal(f.controller.respondRequests.size,0);}finally{f.close();}
});
test('uncertain input response retains exact original text/client id/key and does not submit edited text',async()=>{
 const f=fixture();try{await f.controller.select(f.cid);await tick();let fail=true;f.override(op=>op==='respond'&&fail?Promise.reject(Error('offline')):null);await assert.rejects(()=>f.controller.respond('A'));const original=f.calls.find(x=>x.op==='respond').data;fail=false;await f.controller.respond('B');assert.deepEqual(f.calls.filter(x=>x.op==='respond').at(-1).data,original);assert.equal(f.controller.state.currentDraft,'B');}finally{f.close();}
});
test('accepted control with failed reading clears admission envelope and tracks same job',async()=>{
 const f=fixture();try{await f.controller.select(f.cid);await tick();f.override(op=>op==='messages'?Promise.reject(Error('read offline')):null);await assert.rejects(()=>f.controller.respond('A'));assert.equal(f.controller.respondRequests.size,0);assert.equal(f.controller.currentJob().job_id,f.jid);assert.equal(f.controller.currentJob().state,'queued');assert.match(f.controller.status,/已受理/);}finally{f.close();}
});
test('late original response after identity change cannot restore old private state',async()=>{
 const f=fixture();try{await f.controller.select(f.cid);await tick();let finish;f.override(op=>op==='respond'?new Promise(r=>finish=r):null);const pending=f.controller.respond('A');await tick();f.controller.identity({principal_id:'b',display_name:'B',capabilities:{shared_context:true}});finish({job_id:f.jid,conversation_id:f.cid,state:'queued'});assert.equal(await pending,false);assert.equal(f.controller.state.selected,null);assert.equal(f.controller.state.messages.length,0);assert.equal(f.controller.respondRequests.size,0);}finally{f.close();}
});
test('oldest waiting round remains actionable when later questions are queued',()=>{
 const s=new ConversationState();s.identity({principal_id:'a'});s.select('cid');s.acceptMessages({items:[{message_id:'q1',seq:1,job_id:'waiting',job_state:'waiting_confirmation'},{message_id:'q2',seq:2,job_id:'later',job_state:'queued'}]},s.capture());assert.equal(s.jobs.get('cid').job_id,'waiting');
});
test('authorization wait cannot be treated as a normal new message and can be cancelled',async()=>{
 const f=fixture('waiting_authorization');try{await f.controller.select(f.cid);await tick();assert.equal(await f.controller.send('do not start new job'),false);assert.ok(!f.calls.some(x=>x.op==='respond'||x.op==='send'));assert.equal(await f.controller.cancel(),true);assert.equal(f.controller.currentJob().state,'cancelled');}finally{f.close();}
});
test('unknown shared execution is recovered only from original receipt, never retry',async()=>{
 const f=fixture('execution_unknown');try{await f.controller.select(f.cid);await f.controller.recover();assert.equal(f.calls.filter(x=>x.op==='recover').length,1);assert.ok(!f.calls.some(x=>x.op==='retry'||x.op==='respond'||x.op==='send'));assert.equal(f.controller.currentJob().state,'succeeded');}finally{f.close();}
});
test('unresolved imported history is viewable but has no continue/send/response operation',async()=>{
 const f=fixture();try{f.meta.readonly_reason='imported_history_pending';await f.controller.select(f.cid);assert.equal(f.controller.readOnly(),true);assert.equal(await f.controller.send('A'),false);assert.equal(await f.controller.continueConversation(),false);assert.equal(await f.controller.respond('A'),false);}finally{f.close();}
});
test('default host timers are invoked without the controller as their receiver',()=>{
 const originalSet=globalThis.setTimeout,originalClear=globalThis.clearTimeout,receivers=[];
 try{globalThis.setTimeout=function(){receivers.push(this);return 123;};globalThis.clearTimeout=function(){receivers.push(this);};const c=new ConversationController({api:async()=>({})});assert.equal(c.schedule(()=>{},1),123);c.unschedule(123);assert.ok(receivers.every(receiver=>receiver!==c));}
 finally{globalThis.setTimeout=originalSet;globalThis.clearTimeout=originalClear;}
});
