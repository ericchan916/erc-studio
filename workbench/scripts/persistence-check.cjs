'use strict';
const {_electron}=require('/Users/USER/resources/YOUR_7B0895B9F9');const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict');const {Store}=require('../src/store.cjs');
const root=path.resolve(__dirname,'..'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'studio-persistence-'));const store=new Store(dir);const a=store.add({platform:'douyin',name:'同平台隔离一'}),b=store.add({platform:'douyin',name:'同平台隔离二'});
const server=http.createServer((_req,res)=>{res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>本地持久化测试</title><p>仅用于隔离验收</p>');});
async function run(){await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 const launch=()=>_electron.launch({executablePath:require('electron'),args:[root],env:{...process.env,STUDIO_STATE_DIR:dir,STUDIO_QA:'1',STUDIO_QA_ORIGIN:origin}});
 let app=await launch();await (await app.firstWindow()).waitForSelector('#account-rows tr');
 for(const account of [a,b])await app.evaluate(async(_electron,{account,origin})=>{const v=global.__studioQA.makeView(account);await v.webContents.loadURL(origin);},{account,origin});
 await app.evaluate(async(_electron,{id,origin})=>{const wc=global.__studioQA.views.get(id).webContents;await wc.executeJavaScript('localStorage.setItem("studio-fixture","first-account-only")');await wc.session.cookies.set({url:origin,name:'studio-fixture',value:'first-account-only',expirationDate:Date.now()/1000+3600});wc.session.flushStorageData();await wc.session.cookies.flushStore();},{id:a.id,origin});
 const before=await app.evaluate(async(_electron,{id,origin})=>{const wc=global.__studioQA.views.get(id).webContents;return {local:await wc.executeJavaScript('localStorage.getItem("studio-fixture")'),cookies:(await wc.session.cookies.get({url:origin,name:'studio-fixture'})).length};},{id:b.id,origin});assert.equal(before.local,null);assert.equal(before.cookies,0);
 await app.close();app=await launch();await (await app.firstWindow()).waitForSelector('#account-rows tr');
 for(const account of [a,b])await app.evaluate(async(_electron,{account,origin})=>{const v=global.__studioQA.makeView(account);await v.webContents.loadURL(origin);},{account,origin});
 const results=[];for(const account of [a,b])results.push(await app.evaluate(async(_electron,{id,origin})=>{const wc=global.__studioQA.views.get(id).webContents;return {local:await wc.executeJavaScript('localStorage.getItem("studio-fixture")'),cookies:await wc.session.cookies.get({url:origin,name:'studio-fixture'})};},{id:account.id,origin}));
 assert.equal(results[0].local,'first-account-only');assert.equal(results[0].cookies[0].value,'first-account-only');assert.equal(results[1].local,null);assert.equal(results[1].cookies.length,0);await app.close();server.close();
 console.log(JSON.stringify({ok:true,same_platform_two_accounts:true,cookies_and_local_storage_survive_restart:true,no_cross_account_storage:true,scope:'local_fixture_not_platform_login'}));
}
run().catch(e=>{server.close();console.error(e);process.exitCode=1;});
