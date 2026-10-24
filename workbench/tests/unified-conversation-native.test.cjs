function privateRoot(prefix){const directory=fs.mkdtempSync(prefix);fs.writeFileSync(path.join(directory,'conversation-space.json'),JSON.stringify({version:1,url:'http://127.0.0.1:8844/'}),{mode:0o600});return directory;}
'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const {ConversationClient}=require('../src/conversation.cjs');
function fixture(logged=true){
 const root=privateRoot(path.join(os.tmpdir(),'studio-unified-native-')),calls=[];
 const client=new ConversationClient({stateRoot:root,safeStorage:{},openExternal:()=>{throw Error('unexpected external URL');},transport:async(origin,route,method,body,token,key)=>{calls.push({origin,route,method,body,token,key});return {conversation_id:body?route.split('/')[3]:null,revision:body?body.expected_revision+1:0};}});
 if(logged){client.loaded=true;client.token='fixture-native-session';}
 return {client,calls,close(){client.dispose();fs.rmSync(root,{recursive:true,force:true});}};
}
test('native active lookup keeps credentials in trusted transport',async()=>{
 const f=fixture();try{const result=await f.client.dispatch({op:'active'});assert.equal(result.ok,true);assert.equal(f.calls.at(-1).route,'/v1/conversations/active');assert.equal(f.calls.at(-1).token,'fixture-native-session');assert.equal(JSON.stringify(result).includes('fixture-native-session'),false);}finally{f.close();}
});
test('native continue is a fixed versioned route, not an identity or executor escape hatch',async()=>{
 const f=fixture(),id=crypto.randomUUID(),key=crypto.randomUUID();try{
  const result=await f.client.dispatch({op:'continue',conversation_id:id,expected_revision:4,key});assert.equal(result.ok,true);
  const call=f.calls.at(-1);assert.equal(call.route,'/v1/conversations/'+id+'/continue');assert.equal(call.method,'POST');assert.deepEqual(call.body,{expected_revision:4});assert.equal(call.key,key);
  for(const extra of [{owner:'other'},{token:'YOUR_TEST_SECRET'},{model:'admin'},{executor:'shell'},{url:'http://host/'}])assert.equal((await f.client.dispatch({op:'continue',conversation_id:id,expected_revision:4,key,...extra})).ok,false);
  for(const revision of [-1,1.2,'4',true])assert.equal((await f.client.dispatch({op:'continue',conversation_id:id,expected_revision:revision,key})).ok,false);
 }finally{f.close();}
});
test('unlogged native active and continue do not contact the backend',async()=>{
 const f=fixture(false);try{for(const input of [{op:'active'},{op:'continue',conversation_id:crypto.randomUUID(),expected_revision:0,key:crypto.randomUUID()}]){const result=await f.client.dispatch(input);assert.equal(result.ok,false);assert.equal(result.error.code,'authentication_required');}assert.equal(f.calls.length,0);}finally{f.close();}
});
test('native respond and receipt recovery are fixed original-job routes with strict fields',async()=>{
 const f=fixture(),job=crypto.randomUUID(),client=crypto.randomUUID(),key=crypto.randomUUID();try{
  assert.equal((await f.client.dispatch({op:'respond',job_id:job,text:'本人原文',client_message_id:client,key})).ok,true);
  const call=f.calls.at(-1);assert.equal(call.route,'/v1/jobs/'+job+'/respond');assert.deepEqual(call.body,{text:'本人原文',client_message_id:client});assert.equal(call.key,key);
  assert.equal((await f.client.dispatch({op:'recover',job_id:job,key})).ok,true);assert.equal(f.calls.at(-1).route,'/v1/jobs/'+job+'/recover');assert.deepEqual(f.calls.at(-1).body,{});
  for(const op of ['respond','recover'])for(const extra of [{owner:'other'},{url:'http://host/'},{token:'YOUR_TEST_SECRET'},{executor:'shell'}])assert.equal((await f.client.dispatch({op,job_id:job,key,...(op==='respond'?{text:'A',client_message_id:client}:{}),...extra})).ok,false);
  for(const text of ['',true,'x'.repeat(16001)])assert.equal((await f.client.dispatch({op:'respond',job_id:job,text,client_message_id:client,key})).ok,false);
 }finally{f.close();}
});
test('unlogged respond and recovery never contact an execution endpoint',async()=>{
 const f=fixture(false);try{for(const op of ['respond','recover']){const input={op,job_id:crypto.randomUUID(),key:crypto.randomUUID(),...(op==='respond'?{text:'A',client_message_id:crypto.randomUUID()}:{})};assert.equal((await f.client.dispatch(input)).error.code,'authentication_required');}assert.equal(f.calls.length,0);}finally{f.close();}
});
