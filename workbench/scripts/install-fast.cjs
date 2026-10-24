'use strict';
// Cache-first overlay for a shared bundle: preserve all non-performance modules.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),args=process.argv.slice(2),value=flag=>args[args.indexOf(flag)+1],from=value('--from'),dest=value('--dest');
if(!args.includes('--from')||!args.includes('--dest')||![from,dest].every(p=>path.isAbsolute(p)&&p.endsWith('.app'))||from===dest||fs.existsSync(dest))throw Error('fast_overlay_destination_invalid');
const resources=p=>path.join(p,'Contents/Resources/app'),read=n=>fs.readFileSync(path.join(resources(from),n),'utf8');
function fingerprint(app){const out={};function walk(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true})){const p=path.join(dir,e.name);if(e.isDirectory())walk(p);else out[path.relative(resources(app),p)]=crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');}}walk(resources(app));return out;}
function replace(s,a,b){const oldCount=s.split(a).length-1,newCount=s.split(b).length-1;if(newCount===1&&oldCount<=1)return s;if(oldCount!==1||newCount!==0)throw Error('fast_overlay_anchor_changed');return s.replace(a,b);}
let main=read('src/main.cjs'),preload=read('src/preload.cjs');
main=replace(main,"return contentService.refresh();});bind('studio:content-read'", "return contentService.open?.()||contentService.refresh();});bind('studio:content-read'");
main=replace(main,"bind('studio:analytics-read',days=>analyticsService.read(days));", "bind('studio:analytics-open',days=>analyticsService.open(days));bind('studio:analytics-read',days=>analyticsService.read(days));");
main=replace(main,'win.loadFile(uiFile);', "win.webContents.once('did-finish-load',()=>{contentService.open?.();analyticsService.open(14);});win.loadFile(uiFile);");
preload=replace(preload,'analytics:{read:days=>', "analytics:{open:days=>ipcRenderer.invoke('studio:analytics-open',days),read:days=>");
const hooks={'src/main.cjs':main,'src/preload.cjs':preload};
if(!read('web/index.html').includes('content-catalog.js'))throw Error('fast_overlay_catalog_required');
if(!read('web/index.html').includes('content-project-controller.js'))throw Error('fast_overlay_project_pane_required');
const owned=['src/view-cache.cjs','src/content/service.cjs','src/content/model.cjs','src/content/catalog.cjs','web/content-catalog.js','src/analytics.cjs','web/content-ui.js','web/analytics-ui.js'];
for(const f of owned)cp.execFileSync(process.execPath,['--check',path.join(root,f)]);
const before=fingerprint(from);cp.execFileSync('/usr/bin/ditto',[from,dest]);
for(const [name,text]of Object.entries(hooks))fs.writeFileSync(path.join(resources(dest),name),text);
for(const name of owned)fs.copyFileSync(path.join(root,name),path.join(resources(dest),name));
for(const name of Object.keys(hooks))cp.execFileSync(process.execPath,['--check',path.join(resources(dest),name)]);
const after=fingerprint(dest),changed=Object.keys(before).filter(n=>before[n]!==after[n]),added=Object.keys(after).filter(n=>!before[n]);
if(changed.some(n=>!owned.includes(n)&&!Object.hasOwn(hooks,n))||added.some(n=>n!=='src/view-cache.cjs'))throw Error('fast_overlay_unrelated_change');
cp.execFileSync('/usr/bin/codesign',['--force','--deep','--sign','-',dest]);cp.execFileSync('/usr/bin/codesign',['--verify','--deep','--strict',dest]);
console.log(JSON.stringify({ok:true,from,dest,changed,added,preservedExisting:Object.keys(before).length-changed.length,before,after,profileDataTouched:false}));
