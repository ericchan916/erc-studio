'use strict';
// Generate private setup slots only. Does not login, copy auth or start a worker.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const root=path.resolve(__dirname,'../..');
const args=process.argv.slice(2);
if(args.length!==2||args[0]!=='--state'){console.error('usage: node workbench/scripts/init-config.cjs --state ABSOLUTE_PRIVATE_DIR');process.exit(2);}
if(!path.isAbsolute(args[1]))throw Error('absolute_private_state_required');
const state=path.resolve(args[1]);if(state===root||state.startsWith(root+path.sep))throw Error('state_must_be_outside_source');
fs.mkdirSync(state,{recursive:true,mode:0o700});if(fs.lstatSync(state).isSymbolicLink())throw Error('state_symlink_rejected');
const templates={
 'task-space.json':{version:1,url:'https://TENANT.feishu.cn/base/YOUR_BASE_TOKEN'},
 'content-space.json':{version:1,profile:'YOUR_PROFILE',baseToken:'YOUR_BASE_TOKEN',baseURL:'https://TENANT.feishu.cn/base/YOUR_BASE_TOKEN',tables:{topics:'YOUR_TOPIC_TABLE_ID',projects:'YOUR_PROJECT_TABLE_ID'},writerIds:['YOUR_USER_OPEN_ID'],approverIds:['YOUR_USER_OPEN_ID']},
 'setup.json':{version:1,configured:false,profile:'YOUR_PROFILE',model_config_file:'YOUR_PRIVATE_MODEL_CONFIG',conversation_backend:'YOUR_PRIVATE_GATEWAY_URL',website_url:'YOUR_WEBSITE_HTTPS_URL'},
};
for(const name of Object.keys(templates))if(fs.existsSync(path.join(state,name)))throw Error('existing_configuration_preserved');
for(const [name,data] of Object.entries(templates))fs.writeFileSync(path.join(state,name),JSON.stringify(data,null,2)+'\n',{mode:0o600,flag:'wx'});
console.log(JSON.stringify({configured:false,files:Object.keys(templates),state,business_calls:0,credentials_copied:false}));
