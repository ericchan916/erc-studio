'use strict';
// Checks a future design contract. This is not a running authentication/API/sandbox test.
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),api=JSON.parse(fs.readFileSync(path.join(root,'conversation-api.openapi.json'),'utf8')),design=fs.readFileSync(path.join(root,'HOME_CONVERSATION_DESIGN.md'),'utf8');
const operations=Object.entries(api.paths).flatMap(([url,item])=>Object.entries(item).filter(([method])=>['get','post'].includes(method)).map(([method,operation])=>({url,method,operation,item})));
test('design contract: implementation status and pending capability probes remain explicit',()=>{
 assert.equal(api.openapi,'3.1.0');assert.equal(api['x-status'],'implemented_chat_only_gateway');for(const name of ['x-provider-pkce-verified','x-member-cli-credential-routing-verified','x-device-member-binding-verified'])assert.equal(api[name],false);assert.equal(api['x-client-challenge-is-not-member-identity'],true);assert.equal(api['x-runtime-tool-restriction-verified'],true);assert.equal(api['x-os-execution-sandbox'],false);assert.equal(api['x-tool-policy-default'],'chat_only_no_business_tools');assert.match(design,/尚未实现或上线/);
});
test('design contract: every local schema reference and required property is resolvable',()=>{
 const walk=value=>{if(!value||typeof value!=='object')return;if(value.$ref){assert.ok(value.$ref.startsWith('#/'));let cursor=api;for(const key of value.$ref.slice(2).split('/'))cursor=cursor[key];assert.ok(cursor,value.$ref);}if(value.required&&value.properties)for(const key of value.required)assert.ok(Object.hasOwn(value.properties,key),key);for(const child of Object.values(value))walk(child);};walk(api);
});
test('design contract: conversation/job/me endpoints require member session and ownership guards',()=>{
 for(const {url,operation} of operations){if(url.startsWith('/v1/auth/login/')||url.startsWith('/v1/auth/feishu/')||url==='/health'||url==='/v1/auth/members')continue;assert.deepEqual(operation.security,[{GatewaySession:[]},{GatewayCookie:[]}]);assert.ok(operation['x-authorization-scope'].includes('tenant_and_owner_match'));assert.ok(operation['x-authorization-scope'].includes('member_active'));assert.ok(operation.responses['404']);}assert.equal(api.components.securitySchemes.GatewaySession.scheme,'bearer');
});
test('design contract: client requests have allowlists and no caller-controlled actor/profile/path',()=>{
 for(const name of ['LoginStartRequest','LoginPollRequest','OAuthStartRequest','OAuthPollRequest','NewConversationRequest','MessageRequest']){const schema=api.components.schemas[name];assert.equal(schema.additionalProperties,false);for(const forbidden of ['owner_id','tenant_key','user_id','role','profile','file_path','tool','model'])assert.ok(!Object.hasOwn(schema.properties,forbidden),name+':'+forbidden);}assert.equal(api.components.schemas.MessageRequest.properties.text.maxLength,16000);
});
test('design contract: all authenticated POSTs are idempotent and message accept means queued job',()=>{
 for(const {method,operation} of operations){if(method!=='post'||!operation.security.length)continue;assert.ok(operation.parameters.some(p=>p.name==='Idempotency-Key'&&p.in==='header'&&p.required));assert.ok(operation.responses['409']);}const send=operations.find(x=>x.operation.operationId==='sendMessage').operation;assert.ok(send.responses['202']);assert.ok(!send.responses['200']);assert.match(send.summary,/不是执行成功/);
});
test('design contract: auth handoff binds a challenge, callback is one-time state, credentials stay off callback/query',()=>{
 assert.equal(api.components.schemas.LoginStartRequest.properties.client_challenge.pattern,'^[a-f0-9]{64}$');assert.ok(api.components.schemas.LoginPollRequest.required.includes('client_verifier'));assert.ok(api.components.schemas.LoginPollRequest.required.includes('code'));const callback=api.paths['/v1/auth/feishu/callback'].get;assert.equal(callback['x-implemented'],false);assert.ok(callback.responses['503']);for(const {operation} of operations)for(const param of operation.parameters||[])if(param.in==='query')assert.ok(!/token|credential|verifier/.test(param.name));
});
test('design contract: SSE permits status/final/ref/error, not thought/delta; unknown execution is a first-class state',()=>{
 assert.deepEqual(api.components.schemas.Event.properties.type.enum,['status','final','result_ref','error']);assert.equal(api.components.schemas.Event.allOf.length,4);const response=api.paths['/v1/jobs/{job_id}/events'].get.responses['200'];assert.ok(response.content['text/event-stream']);const states=api.components.schemas.Job.properties.state.enum;for(const state of ['waiting_authorization','waiting_confirmation','cancel_requested','execution_unknown'])assert.ok(states.includes(state));assert.match(design,/不自动重放/);
});
test('design contract: bounded pagination, unique operation IDs and scoped path IDs',()=>{
 assert.equal(new Set(operations.map(x=>x.operation.operationId)).size,operations.length);for(const {url,item,operation} of operations){const parameters=[...(item.parameters||[]),...(operation.parameters||[])];for(const name of [...url.matchAll(/\{([^}]+)\}/g)].map(x=>x[1]))assert.ok(parameters.some(p=>p.in==='path'&&p.name===name&&p.required&&p.schema.format==='uuid'));for(const p of parameters)if(p.name==='limit')assert.equal(p.schema.maximum,100);}assert.match(design,/chat_only/);assert.match(design,/P0/);
});

test('implemented scan contract uses official device flow with no caller supplied identity',()=>{assert.equal(api['x-auth-mode'],'feishu_device_oauth_and_private_otp');assert.ok(api.paths['/v1/auth/feishu/start'].post.responses['201']);assert.deepEqual(api.components.schemas.OAuthPollRequest.required,['login_id','client_verifier']);assert.ok(!api.components.schemas.OAuthStartResponse.properties.device_code);});
