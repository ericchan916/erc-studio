'use strict';
// Read-only refresh for the browser gateway. Reuses the native content mapping
// and fixed CLI identity, but never exposes write/model actions to the browser.
const fs=require('node:fs'),path=require('node:path');
const local=path.resolve(__dirname,'../src'),deployed=__dirname;
const base=fs.existsSync(path.join(deployed,'content/client.cjs'))?deployed:local;
const {atomic,loadConfig,ContentClient}=require(path.join(base,'content/client.cjs'));
const M=require(path.join(base,'content/model.cjs'));
const Cache=require(path.join(base,'view-cache.cjs'));

function read(file,fallback){try{return JSON.parse(fs.readFileSync(file,'utf8'));}catch(error){if(error.code==='ENOENT')return fallback;throw error;}}
function publicJobs(root,actor){return read(path.join(root,'content-jobs.json'),[]).filter(j=>j.requesterId===actor).slice(-100).map(({id,kind,projectId,state,error,model,startedAt,finishedAt,pendingResult})=>({id,kind,projectId,state,error,model,startedAt,finishedAt,hasPendingResult:!!pendingResult}));}
async function refresh(root){
 const config=loadConfig(root);if(!config)throw Error('content_config_missing');
 const client=new ContentClient(config),hint=Cache.localIdentity(),actor=await client.actor();
 if(!hint||hint!==actor.openId)throw Error('content_identity_changed');
 const [topicSchema,projectSchema,topicRows,projectRows]=await Promise.all([client.schema('topics'),client.schema('projects'),client.records('topics'),client.records('projects')]);
 const schemaReady=M.topicExtra.every(f=>topicSchema.some(x=>x.name===f.name))&&M.projectExtra(config.tables.topics).every(f=>projectSchema.some(x=>x.name===f.name));
 const topics=topicRows.map(M.topic),projects=projectRows.map(M.project),readAt=new Date().toISOString();
 const snapshot={phase:'ok',_actorId:actor.openId,_cacheScope:Cache.scope(config,actor.openId),topics:topics.filter(t=>t.direction||t.source),projects:projects.filter(p=>p.title),jobs:[],forms:M.FORMS,stages:M.STAGES,accounts:M.ACCOUNTS,platforms:M.PLATFORMS,refreshing:false,message:schemaReady?'数据来自飞书原表':'原表可读；内容字段尚未初始化，当前为只读',schemaReady,schemas:{projects:projectSchema},stale:false,readAt,emptyTopics:topics.filter(t=>!t.direction&&!t.source).length,actor:{name:actor.name,canWrite:actor.canWrite&&schemaReady,canApprove:actor.canApprove&&schemaReady}};
 atomic(path.join(root,'content-cache.json'),snapshot);
 return {...Cache.visible(snapshot),jobs:publicJobs(root,actor.openId)};
}
async function main(){const root=path.resolve(process.argv[2]||'');if(!path.isAbsolute(root)||root!==path.join(process.env.HOME,'Library/Application Support/ERC Studio'))throw Error('content_root_invalid');process.stdout.write(JSON.stringify(await refresh(root)));}
if(require.main===module)main().catch(error=>{process.stderr.write(JSON.stringify({ok:false,error:/^content_[a-z_]+$/.test(error.message)?error.message:'content_refresh_failed'}));process.exitCode=1;});
module.exports={refresh};
