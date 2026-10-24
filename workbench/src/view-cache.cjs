'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const PROFILE=process.env.CONTENTBASE_LARK_PROFILE||'';
// Namespace hint only, not an authorization decision. Every remote read and write
// still verifies the CLI actor; cached views are explicitly stale and read-only.
function localIdentity(file=path.join(process.env.LARKSUITE_CLI_CONFIG_DIR||path.join(os.homedir(),'.lark-cli'),'config.json')){
 try{const c=JSON.parse(fs.readFileSync(file,'utf8')),a=c.apps?.find(x=>x.name===PROFILE)||c.apps?.find(x=>x.appId===PROFILE),users=a?.users;
  return a?.appId===PROFILE&&Array.isArray(users)&&users.length===1&&/^ou_[A-Za-z0-9]+$/.test(users[0].userOpenId||'')?users[0].userOpenId:null;
 }catch{return null;}
}
function scope(config,actor){return actor?crypto.createHash('sha256').update(JSON.stringify({profile:PROFILE,baseToken:config?.baseToken,tables:config?.tables,actor})).digest('hex'):null;}
function read(file){try{return JSON.parse(fs.readFileSync(file,'utf8'));}catch{return null;}}
function usable(value,config,actor){return !!actor&&value?._actorId===actor&&value?._cacheScope===scope(config,actor);}
function visible(value){const {_actorId,_cacheScope,...publicValue}=value;return structuredClone({...publicValue,viewKey:_cacheScope||null});}
module.exports={localIdentity,scope,read,usable,visible};
