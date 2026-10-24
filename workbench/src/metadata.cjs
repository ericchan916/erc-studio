'use strict';
const {spawn}=require('node:child_process');
const NAMES={'抖音':'douyin','小红书':'xiaohongshu','哔哩哔哩':'bilibili','B站':'bilibili','快手':'kuaishou','视频号':'channels','微信公众号':'wechat','公众号':'wechat'};
class MetadataError extends Error{constructor(code){super(code);this.code=code;}}
function rowsFromPacket(packet){
 if(packet?.ok===false){const code=packet.error?.code;throw new MetadataError(typeof code==='string'&&/^[a-zA-Z0-9_-]{1,60}$/.test(code)?code:'source_query_failed');}
 if(!packet||packet.ok!==true||!Array.isArray(packet.data))throw new MetadataError('invalid_response');
 const seen=new Set(),rows=[];
 for(const x of packet.data){
  if(!x||typeof x!=='object')throw new MetadataError('invalid_account');
  if(!Object.hasOwn(NAMES,x.platformName))continue;
  if(typeof x.id!=='string'||!x.id||seen.has(x.id)||typeof x.platformAccountName!=='string'||!x.platformAccountName.trim()||x.platformAccountName.length>100)throw new MetadataError('invalid_account');
  seen.add(x.id);let fans=null;
  if(x.fansTotal!==undefined&&x.fansTotal!==null&&x.fansTotal!==''){
   if(!['number','string'].includes(typeof x.fansTotal)||typeof x.fansTotal==='string'&&!/^\d+(?:\.\d+)?$/.test(x.fansTotal.trim()))throw new MetadataError('invalid_fans');
   fans=Number(x.fansTotal);if(!Number.isFinite(fans)||fans<0)throw new MetadataError('invalid_fans');
  }
  const people=Array.isArray(x.operators)?x.operators:[];
  rows.push({sourceId:x.id,platform:NAMES[x.platformName],name:x.platformAccountName.trim(),avatar:x.platformAvatar,fans,owner:typeof x.principal?.name==='string'?x.principal.name.slice(0,100):'',operator:people.map(p=>p&&typeof(p.name||p.nickName)==='string'?(p.name||p.nickName):'').filter(Boolean).join('、').slice(0,100)});
 }
 return rows;
}
function readCLI({timeoutMs=25000,launcher=spawn,signal}={}){
 return new Promise((resolve,reject)=>{
  if(signal?.aborted)return reject(new MetadataError('cancelled'));
  // GUI launch environments may omit Node's directory. No global PATH/config edit.
  const child=launcher(process.env.CONTENTBASE_YXER_CLI||'yxer',['accounts','list','--all','--json'],{detached:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PATH:'/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:'+(process.env.PATH||'')}});
  let stdout='',stderr='',bytes=0,forced=null,finished=false,killTimer;
  function kill(sig){try{process.kill(-child.pid,sig);}catch{try{child.kill(sig);}catch{}}}
  function terminate(code){if(finished||forced)return;forced=code;kill('SIGTERM');killTimer=setTimeout(()=>kill('SIGKILL'),500);killTimer.unref?.();}
  const abort=()=>terminate('cancelled');signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(()=>terminate('timeout'),timeoutMs);timer.unref?.();
  function finish(err,value){if(finished)return;finished=true;clearTimeout(timer);clearTimeout(killTimer);signal?.removeEventListener('abort',abort);err?reject(err):resolve(value);}
  child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
  child.on('error',()=>finish(new MetadataError('cli_unavailable')));
  child.stdout.on('data',data=>{bytes+=Buffer.byteLength(data);if(bytes>4*1024*1024)terminate('output_limit');else stdout+=data;});
  child.stderr.on('data',data=>{if(stderr.length<8000)stderr+=data.toString();});
  child.on('close',code=>{
   if(forced)return finish(new MetadataError(forced));
   if(code!==0){let safe='cli_failed';try{const j=JSON.parse(stderr||stdout),s=j.error?.code;if(typeof s==='string'&&/^[a-zA-Z0-9_-]{1,60}$/.test(s))safe=s;}catch{}return finish(new MetadataError(safe));}
   try{finish(null,JSON.parse(stdout));}catch{finish(new MetadataError('invalid_json'));}
  });
 });
}
class MetadataRefresher{
 constructor(store,{read=readCLI,now=()=>new Date().toISOString(),onChange=()=>{}}={}){this.store=store;this.read=read;this.now=now;this.onChange=onChange;this.inflight=null;this.value={...store.data.metadataRefresh,phase:'idle',source:'yxer_cli',error:null};}
 state(){return structuredClone(this.value);}
 refresh(){
  if(this.inflight)return this.inflight;
  this.value={...this.value,phase:'loading',attemptedAt:this.now(),error:null};
  this.abortController=new AbortController();
  this.inflight=Promise.resolve().then(()=>this.read({signal:this.abortController.signal})).then(packet=>{
   const rows=rowsFromPacket(packet),time=this.now();
   this.store.importMetadata(rows,{reconcile:true,updatedAt:time});
   this.value={...this.value,phase:'ok',lastSuccessAt:time,sourceCount:rows.length,error:null};
   this.store.data.metadataRefresh=this.state();this.store.save();return this.state();
  }).catch(error=>{
   const code=typeof error?.code==='string'&&/^[a-zA-Z0-9_-]{1,60}$/.test(error.code)?error.code:'refresh_failed';
   this.value={...this.value,phase:'error',error:code};
   this.store.data.metadataRefresh=this.state();this.store.save();return this.state();
  }).finally(()=>{this.inflight=null;this.onChange();});
  this.onChange();return this.inflight;
 }
 cancel(){this.abortController?.abort();}
}
module.exports={MetadataError,MetadataRefresher,rowsFromPacket,readCLI};
