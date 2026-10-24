'use strict';
const {_electron}=require('/Users/USER/resources/YOUR_7B0895B9F9'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');const {Store}=require('../src/store.cjs');
const root=path.resolve(__dirname,'..'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'studio-qr-check-'));const store=new Store(dir),accounts=['xiaohongshu','kuaishou'].map(platform=>store.add({platform,name:'扫码入口专项验收'}));
const out=path.join(os.homedir(),'Library/Application Support/ContentBaseChanges/20261001-studio-account-matrix/qa');
async function run(){const app=await _electron.launch({executablePath:require('electron'),args:[root],env:{...process.env,STUDIO_STATE_DIR:dir,STUDIO_QA:'1'}});const page=await app.firstWindow();await page.waitForSelector('#account-rows tr');const records=[];
 for(const a of accounts){await app.evaluate((_e,id)=>global.__studioQA.open(id),a.id);await page.waitForTimeout(14000);await page.evaluate(()=>window.studio.command('qr'));await page.waitForTimeout(2500);
  const r=await app.evaluate(async(_e,id)=>{const v=global.__studioQA.views.get(id),wc=v.webContents;return {platform:global.__studioQA.store.get(id).platform,host:new URL(wc.getURL()).hostname,path:new URL(wc.getURL()).pathname,text:await wc.executeJavaScript('(document.body?.innerText||"").slice(0,1400)'),image:Array.from((await wc.capturePage()).toPNG())};},a.id);
  fs.writeFileSync(path.join(out,'platform-'+a.platform+'-qr.png'),Buffer.from(r.image),{mode:0o600});delete r.image;records.push(r);console.log(JSON.stringify(r));}
 await app.close();fs.writeFileSync(path.join(out,'qr-check.json'),JSON.stringify(records,null,2),{mode:0o600});}
run().catch(e=>{console.error(e);process.exitCode=1;});
