'use strict';
// Narrow overlay onto the actual installed app: preserve other agents' modules.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),cp=require('node:child_process'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),APP=path.join(os.homedir(),'Library/Application Support/ERC Studio/Builds/ERC Studio.app');
const LEAVES=['src/home-model.cjs','web/home-glb.js','web/home-ui.js','web/home.css','web/assets/model.glb'];
function once(text,anchor,value){if(text.split(anchor).length!==2)throw new Error('overlay_anchor_changed');return text.replace(anchor,value);}
function patchShared(name,text){
 if(name==='src/main.cjs'&&text.includes("require('./home-model.cjs')")||name==='src/preload.cjs'&&text.includes('homeModel:')||name==='web/index.html'&&text.includes('src="home-glb.js"'))throw new Error('overlay_anchor_changed');
 if(name==='src/main.cjs')return once(once(text,"const {MetadataRefresher}=require('./metadata.cjs');","const {MetadataRefresher}=require('./metadata.cjs');\nconst {readHomeModel}=require('./home-model.cjs');")," bind('studio:home-open',"," bind('studio:home-model',()=>readHomeModel());\n bind('studio:home-open',");
 if(name==='src/preload.cjs')return once(text,"contextBridge.exposeInMainWorld('studio',{\n","contextBridge.exposeInMainWorld('studio',{\n homeModel:()=>ipcRenderer.invoke('studio:home-model'),\n");
 if(name==='web/index.html')return once(once(text,'程序化三维内容占位，尚未加载Tripo模型','内容助手 Tripo 三维模型，可点击转身'),'<script src="home-ui.js"></script>','<script src="home-glb.js"></script><script src="home-ui.js"></script>');
 throw new Error('overlay_file_invalid');
}
function hashes(dir){const result={};function visit(p){for(const entry of fs.readdirSync(p,{withFileTypes:true})){const f=path.join(p,entry.name);if(entry.isDirectory())visit(f);else if(entry.isFile())result[path.relative(dir,f)]=crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');else throw new Error('unexpected_packaged_symlink');}}visit(dir);return result;}
function same(a,b){return JSON.stringify(Object.entries(a).sort())===JSON.stringify(Object.entries(b).sort());}
function command(argv,cwd=root){const p=cp.spawnSync(argv[0],argv.slice(1),{cwd,encoding:'utf8'});const record={argv,cwd,exit_status:p.status,stdout:p.stdout||'',stderr:p.stderr||''};if(p.error||p.status!==0)throw new Error(JSON.stringify(record));return record;}
function prepare(out){
 if(!path.isAbsolute(out)||!fs.existsSync(path.join(out,'delivery/baseline.json')))throw new Error('private_delivery_root_required');const before=JSON.parse(fs.readFileSync(path.join(out,'delivery/baseline.json'),'utf8')),resources=path.join(APP,'Contents/Resources/app');let original=before.resources_sha256,updating=false;const live=hashes(resources);if(!same(live,original)){const prior=path.join(out,'delivery/overlay.json'),accepted=path.join(out,'delivery/accepted-runtime-update.json');if(!fs.existsSync(prior))throw new Error('installed_app_changed');const previous=JSON.parse(fs.readFileSync(prior,'utf8'));if(!same(live,previous.modified)){if(!fs.existsSync(accepted)||!same(live,JSON.parse(fs.readFileSync(accepted,'utf8')).resources_sha256)||previous.touched.some(name=>live[name]!==previous.modified[name]))throw new Error('installed_app_changed');}original=live;updating=true;}
 const candidate=path.join(out,'modified.app');if(fs.existsSync(candidate))throw new Error('candidate_already_exists');const records=[command(['/usr/bin/ditto',APP,candidate])],dest=path.join(candidate,'Contents/Resources/app'),expected={...original};
 if(!updating)for(const name of ['src/main.cjs','src/preload.cjs','web/index.html']){fs.writeFileSync(path.join(dest,name),patchShared(name,fs.readFileSync(path.join(resources,name),'utf8')));expected[name]=crypto.createHash('sha256').update(fs.readFileSync(path.join(dest,name))).digest('hex');}
 for(const name of LEAVES){fs.mkdirSync(path.dirname(path.join(dest,name)),{recursive:true});fs.copyFileSync(path.join(root,name),path.join(dest,name));expected[name]=crypto.createHash('sha256').update(fs.readFileSync(path.join(dest,name))).digest('hex');}
 if(!same(hashes(dest),expected))throw new Error('overlay_bytes_mismatch');for(const name of ['src/main.cjs','src/preload.cjs','web/home-glb.js','web/home-ui.js'])records.push(command([process.execPath,'--check',path.join(dest,name)]));
 records.push(command(['/usr/bin/codesign','--force','--deep','--sign','-',candidate]),command(['/usr/bin/codesign','--verify','--deep','--strict',candidate]));
 const record={candidate,app:APP,original,modified:expected,touched:[...LEAVES,'src/main.cjs','src/preload.cjs','web/index.html'],commands:records,preserve_other_modules:true,updating_existing_model:updating};fs.writeFileSync(path.join(out,'delivery/overlay.json'),JSON.stringify(record,null,2));console.log(JSON.stringify({ok:true,candidate,preserve_other_modules:true,touched:record.touched}));return record;
}
function appTarget(raw=APP){
 const base=path.join(os.homedir(),'Library/Application Support/ERC Studio/Builds');
 if(typeof raw!=='string'||!path.isAbsolute(raw)||path.dirname(raw)!==base||!path.basename(raw).startsWith('ERC Studio')||!raw.endsWith('.app')||fs.lstatSync(raw).isSymbolicLink())throw new Error('app_target_invalid');
 return raw;
}
function install(out){
 const plan=JSON.parse(fs.readFileSync(path.join(out,'delivery/overlay.json'),'utf8')),APP=appTarget(plan.app),resources=path.join(APP,'Contents/Resources/app');if(!same(hashes(resources),plan.original)||!same(hashes(path.join(plan.candidate,'Contents/Resources/app')),plan.modified))throw new Error('app_or_candidate_changed');
 const ps=command(['/bin/ps','-axo','command']).stdout;if(ps.split('\n').some(line=>line.startsWith(path.join(APP,'Contents/MacOS/Electron'))))throw new Error('app_running');
 const backup=path.join(out,fs.existsSync(path.join(out,'pre-install.app'))?'pre-install-'+crypto.randomUUID()+'.app':'pre-install.app');fs.renameSync(APP,backup);try{fs.renameSync(plan.candidate,APP);}catch(e){fs.renameSync(backup,APP);throw e;}
 const record=command(['/usr/bin/codesign','--verify','--deep','--strict',APP]);fs.writeFileSync(path.join(out,'delivery/install.json'),JSON.stringify({ok:true,app:APP,backup,preserve_other_modules:true,commands:[record]},null,2));console.log(JSON.stringify({ok:true,app:APP,backup,preserve_other_modules:true}));
}
module.exports={patchShared,hashes,same,prepare,install,appTarget,LEAVES};
if(require.main===module){try{const [mode,out]=process.argv.slice(2);if(mode==='--prepare')prepare(out);else if(mode==='--install')install(out);else throw new Error('use_prepare_or_install');}catch(e){console.error(e.message);process.exitCode=1;}}
