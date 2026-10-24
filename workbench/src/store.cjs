'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {platform}=require('./platforms.cjs');
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const STATES=new Set(['unverified','authorized','login_required','unknown','verification']);
function text(v,max=100){if(typeof v!=='string')throw new Error('text_invalid');const s=v.trim();if(!s||s.length>max)throw new Error('text_invalid');return s;}
function atomic(file,value){const tmp=file+'.tmp-'+crypto.randomUUID();fs.writeFileSync(tmp,JSON.stringify(value,null,2),{mode:0o600});fs.renameSync(tmp,file);fs.chmodSync(file,0o600);}
class Store{
 constructor(root){this.root=root;fs.mkdirSync(root,{recursive:true,mode:0o700});fs.chmodSync(root,0o700);this.file=path.join(root,'accounts.json');
  this.data=fs.existsSync(this.file)?JSON.parse(fs.readFileSync(this.file,'utf8')):{version:1,accounts:[],groups:[]};
  if(this.data.version!==1||!Array.isArray(this.data.accounts)||!Array.isArray(this.data.groups))throw new Error('state_invalid');
  const seen=new Set();for(const a of this.data.accounts){platform(a.platform);if(!UUID.test(a.id)||seen.has(a.id)||a.partition!==`persist:studio-${a.id}`)throw new Error('state_invalid');seen.add(a.id);}
  if(!fs.existsSync(this.file))this.save();
 }
 save(){atomic(this.file,this.data);}
 list(){return structuredClone(this.data);}
 get(id){if(!UUID.test(String(id)))throw new Error('account_id_invalid');const a=this.data.accounts.find(x=>x.id===id);if(!a)throw new Error('account_missing');return a;}
 add(value){platform(value.platform);const name=text(value.name),id=crypto.randomUUID();
  const a={id,platform:value.platform,name,partition:`persist:studio-${id}`,group:value.group?text(value.group):'',owner:value.owner?text(value.owner):'',operator:value.operator?text(value.operator):'',avatar:null,fans:null,fansUpdatedAt:null,status:'unverified',statusReason:'尚未在本机登录',checkedAt:null,source:'local',createdAt:new Date().toISOString()};
  this.data.accounts.push(a);this.save();return structuredClone(a);
 }
 update(id,value){const a=this.get(id);const keys=['name','owner','operator','group'];if(Object.keys(value).some(k=>!keys.includes(k)))throw new Error('field_not_allowed');
  const next={};for(const k of keys)if(k in value)next[k]=value[k]===''?'':text(value[k]);if('name' in next&&!next.name)throw new Error('text_invalid');Object.assign(a,next);this.save();return structuredClone(a);
 }
 observe(id,result){if(!STATES.has(result.status))throw new Error('status_invalid');const a=this.get(id);a.status=result.status;a.statusReason=String(result.reason).slice(0,200);a.checkedAt=new Date().toISOString();this.save();return structuredClone(a);}
 addGroup(name){name=text(name,60);if(!this.data.groups.includes(name)){this.data.groups.push(name);this.save();}return this.list();}
 assignGroup(ids,group){if(!Array.isArray(ids)||!ids.length||ids.length>500)throw new Error('selection_invalid');if(group!==''&&!this.data.groups.includes(group))throw new Error('group_invalid');const accounts=ids.map(id=>this.get(id));for(const a of accounts)a.group=group;this.save();return this.list();}
 importMetadata(rows,{reconcile=false,updatedAt=new Date().toISOString()}={}){let added=0;if(reconcile)for(const a of this.data.accounts)if(a.source==='yxer_metadata')a.metadataPresent=false;
 for(const row of rows){if(!row||!row.sourceId)continue;platform(row.platform);
  const sourceKey=crypto.createHash('sha256').update(String(row.sourceId)).digest('hex');let a=this.data.accounts.find(x=>x.sourceKey===sourceKey);
  if(!a){a=this.add({platform:row.platform,name:row.name,owner:row.owner||'',operator:row.operator||''});a=this.get(a.id);a.sourceKey=sourceKey;a.source='yxer_metadata';added++;}
  a.avatar=typeof row.avatar==='string'&&/^https:\/\//.test(row.avatar)?row.avatar:null;
  a.fans=typeof row.fans==='number'&&Number.isFinite(row.fans)&&row.fans>=0?row.fans:null;a.fansUpdatedAt=updatedAt;a.metadataPresent=true;
 }this.save();return {added,total:this.data.accounts.length};}
}
module.exports={Store,UUID,atomic};
