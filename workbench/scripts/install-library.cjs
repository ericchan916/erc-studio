'use strict';
// Narrow shared-App overlay; never rebuild or replace unrelated home/Bar/content resources.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..');
const owned=['src/folder.cjs','src/tasks.cjs','web/task-ui.js','web/bar-ui.js'];
function once(s,a,b){if(s.split(a).length!==2)throw Error('library_overlay_anchor_changed');return s.replace(a,b);}
function hooks(read){let main=read('src/main.cjs'),preload=read('src/preload.cjs');if(main.includes('studio:bar-library-open'))throw Error('library_overlay_already_present');
 main=once(main,"const {TaskPane,readTaskConfig,TASK_PARTITION,allowedTaskURL}=require('./tasks.cjs');","const {TaskPane,readTaskConfig,TASK_PARTITION,allowedTaskURL}=require('./tasks.cjs');\nconst {FolderPane}=require('./folder.cjs');");
 main=once(main,'let websitePane,taskPane;','let websitePane,taskPane,libraryPane;');
 const hide='taskPane?.hide();websitePane?.hide();';if(main.split(hide).length!==3)throw Error('library_overlay_hide_anchor_changed');main=main.replaceAll(hide,'taskPane?.hide();libraryPane?.hide();websitePane?.hide();');
 main=once(main,' const barService=new BarService(stateRoot);',` const barService=new BarService(stateRoot);
 libraryPane=new FolderPane({win,WebContentsView,session,config:barService.config?{url:barService.config.folderURL}:barService.configError?{error:true}:null,onEvent:v=>send('studio:bar-library',v)});
 const openLibrary=()=>{closeView();return libraryPane.open();};
 bind('studio:bar-library-open',openLibrary);bind('studio:bar-library-bounds',v=>libraryPane.bounds(v));bind('studio:bar-library-command',v=>libraryPane.command(v));`);
 main=once(main,"bind('studio:bar-source',async input=>{const url=","bind('studio:bar-source',async input=>{if(input?.kind==='folder')return openLibrary();const url=");
 main=once(main,'taskPane.close();websitePane.close();','taskPane.close();libraryPane.close();websitePane.close();');
 preload=once(preload,"bar:{read:()=>ipcRenderer.invoke('studio:bar-read'),","bar:{library:{open:()=>ipcRenderer.invoke('studio:bar-library-open'),bounds:v=>ipcRenderer.invoke('studio:bar-library-bounds',v),command:v=>ipcRenderer.invoke('studio:bar-library-command',v),onState:callback=>{const fn=(_e,v)=>callback(v);ipcRenderer.on('studio:bar-library',fn);return()=>ipcRenderer.removeListener('studio:bar-library',fn);}},read:()=>ipcRenderer.invoke('studio:bar-read'),");
 return {'src/main.cjs':main,'src/preload.cjs':preload};
}
function fingerprint(dir){const out={};function walk(d){for(const e of fs.readdirSync(d,{withFileTypes:true})){const p=path.join(d,e.name);if(e.isDirectory())walk(p);else out[path.relative(dir,p)]=crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');}}walk(dir);return out;}
function install(from,dest){if(![from,dest].every(p=>typeof p==='string'&&path.isAbsolute(p)&&p.endsWith('.app'))||from===dest||fs.existsSync(dest))throw Error('library_overlay_destination_invalid');const resource=p=>path.join(p,'Contents/Resources/app'),before=fingerprint(resource(from)),changes=hooks(n=>fs.readFileSync(path.join(resource(from),n),'utf8'));cp.execFileSync('/usr/bin/ditto',[from,dest]);for(const [n,s]of Object.entries(changes))fs.writeFileSync(path.join(resource(dest),n),s);for(const n of owned)fs.copyFileSync(path.join(root,n),path.join(resource(dest),n));const after=fingerprint(resource(dest)),changed=Object.keys(before).filter(n=>before[n]!==after[n]),added=Object.keys(after).filter(n=>!before[n]);if(changed.some(n=>!owned.includes(n)&&!Object.hasOwn(changes,n))||added.some(n=>!owned.includes(n)))throw Error('library_overlay_unrelated_change');for(const n of [...owned,...Object.keys(changes)])cp.execFileSync(process.execPath,['--check',path.join(resource(dest),n)]);cp.execFileSync('/usr/bin/codesign',['--force','--deep','--sign','-',dest]);cp.execFileSync('/usr/bin/codesign',['--verify','--deep','--strict',dest]);return {ok:true,from,dest,changed,added,preserved:Object.keys(before).length-changed.length,before,after,profileDataTouched:false};}
if(require.main===module){const args=process.argv.slice(2);console.log(JSON.stringify(install(args[args.indexOf('--from')+1],args[args.indexOf('--dest')+1])));}
module.exports={hooks,install,owned,fingerprint};
