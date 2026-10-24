'use strict';
// Narrow shared-App overlay; never rebuild or replace unrelated home/Bar/content resources.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..');
const owned=['src/content/model.cjs','src/content/service.cjs','src/content/catalog.cjs','web/content-catalog.js','web/content-ui.js'];
function once(s,a,b){if(s.split(a).length!==2)throw Error('catalog_overlay_anchor_changed');return s.replace(a,b);}
function hooks(read){let main=read('src/main.cjs'),preload=read('src/preload.cjs'),index=read('web/index.html');if(!index.includes('content-project-controller.js'))throw Error('catalog_overlay_project_pane_required');if(main.includes('studio:content-query'))throw Error('catalog_overlay_already_present');
 main=once(main,"bind('studio:content-read',()=>contentService.refresh());","bind('studio:content-read',()=>contentService.refresh());bind('studio:content-query',input=>contentService.query(input));");
 preload=once(preload,'content:{open:()=>',"content:{query:input=>ipcRenderer.invoke('studio:content-query',input),open:()=>");
 index=once(index,'<script src="content-ui.js"></script>','<script src="content-catalog.js"></script><script src="content-ui.js"></script>');
 return {'src/main.cjs':main,'src/preload.cjs':preload,'web/index.html':index};
}
function fingerprint(dir){const out={};function walk(d){for(const e of fs.readdirSync(d,{withFileTypes:true})){const p=path.join(d,e.name);if(e.isDirectory())walk(p);else out[path.relative(dir,p)]=crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');}}walk(dir);return out;}
function install(from,dest){if(![from,dest].every(p=>typeof p==='string'&&path.isAbsolute(p)&&p.endsWith('.app'))||from===dest||fs.existsSync(dest))throw Error('catalog_overlay_destination_invalid');const resource=p=>path.join(p,'Contents/Resources/app'),before=fingerprint(resource(from)),changes=hooks(n=>fs.readFileSync(path.join(resource(from),n),'utf8'));cp.execFileSync('/usr/bin/ditto',[from,dest]);for(const [n,s]of Object.entries(changes))fs.writeFileSync(path.join(resource(dest),n),s);for(const n of owned)fs.copyFileSync(path.join(root,n),path.join(resource(dest),n));const after=fingerprint(resource(dest)),changed=Object.keys(before).filter(n=>before[n]!==after[n]),added=Object.keys(after).filter(n=>!before[n]);if(changed.some(n=>!owned.includes(n)&&!Object.hasOwn(changes,n))||added.some(n=>!owned.includes(n)))throw Error('catalog_overlay_unrelated_change');for(const n of [...owned,...Object.keys(changes)].filter(n=>/\.[cm]?js$/.test(n)))cp.execFileSync(process.execPath,['--check',path.join(resource(dest),n)]);cp.execFileSync('/usr/bin/codesign',['--force','--deep','--sign','-',dest]);cp.execFileSync('/usr/bin/codesign',['--verify','--deep','--strict',dest]);return {ok:true,from,dest,changed,added,preserved:Object.keys(before).length-changed.length,before,after,profileDataTouched:false};}
if(require.main===module){const args=process.argv.slice(2);console.log(JSON.stringify(install(args[args.indexOf('--from')+1],args[args.indexOf('--dest')+1])));}
module.exports={hooks,install,owned,fingerprint};
