'use strict';
// Analytics-only overlay: retain other worktrees' renderer, main process and dependencies.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),args=process.argv.slice(2),value=flag=>args[args.indexOf(flag)+1];
const from=value('--from'),dest=value('--dest');
if(!args.includes('--from')||!args.includes('--dest')||![from,dest].every(p=>path.isAbsolute(p)&&p.endsWith('.app'))||from===dest||fs.existsSync(dest))throw new Error('analytics_overlay_destination_invalid');
const resources=p=>path.join(p,'Contents/Resources/app'),owned=['src/analytics.cjs','src/analytics-model.cjs','src/analytics-schema.cjs','src/analytics-source.cjs','web/analytics-ui.js','web/analytics.css'];
const hook='web/content-ui.js',anchor='window.hideStudioContent=()=>{root.hidden=true;',replacement='window.hideStudioContent=()=>{if(root.hidden)return;root.hidden=true;';
let content=fs.readFileSync(path.join(resources(from),hook),'utf8');if(content.includes(replacement)){}else if(content.split(anchor).length===2)content=content.replace(anchor,replacement);else throw new Error('analytics_overlay_content_hook_changed');
const fingerprint=p=>{const out={};function walk(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true})){const f=path.join(dir,e.name);if(e.isDirectory())walk(f);else out[path.relative(resources(p),f)]=crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');}}walk(resources(p));return out;};
const anchors={'src/main.cjs':["new AnalyticsService","bind('studio:analytics-read'","bind('studio:analytics-sync'",'win.loadFile(uiFile)'],'src/preload.cjs':["invoke('studio:analytics-read'","invoke('studio:analytics-sync'"],'web/index.html':['src="analytics-ui.js"','../node_modules/echarts/dist/echarts.min.js']};
for(const [name,values]of Object.entries(anchors)){const text=fs.readFileSync(path.join(resources(from),name),'utf8');for(const anchor of values)if(!text.includes(anchor))throw new Error('analytics_overlay_anchor_missing');}
const before=fingerprint(from);for(const name of owned){if(!before[name])throw new Error('analytics_overlay_module_missing');if(name.endsWith('.js')||name.endsWith('.cjs'))cp.execFileSync(process.execPath,['--check',path.join(root,name)]);}
cp.execFileSync('/usr/bin/ditto',[from,dest]);for(const name of owned)fs.copyFileSync(path.join(root,name),path.join(resources(dest),name));
fs.writeFileSync(path.join(resources(dest),hook),content);
fs.cpSync(path.join(root,'src/tencent-rum'),path.join(resources(dest),'src/tencent-rum'),{recursive:true});
const after=fingerprint(dest),changed=Object.keys(before).filter(n=>before[n]!==after[n]);if(changed.some(n=>!owned.includes(n)&&n!==hook)||Object.keys(after).filter(n=>!before[n]).some(n=>!n.startsWith('src/tencent-rum/')))throw new Error('analytics_overlay_unrelated_change');
cp.execFileSync('/usr/bin/codesign',['--force','--deep','--sign','-',dest]);cp.execFileSync('/usr/bin/codesign',['--verify','--deep','--strict',dest]);
console.log(JSON.stringify({ok:true,from,dest,changedExisting:changed,added:Object.keys(after).filter(n=>!before[n]),preservedExisting:Object.keys(before).length-changed.length,before,after,profileDataTouched:false}));
