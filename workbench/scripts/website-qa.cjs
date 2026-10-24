'use strict';
// Exercise the actual Electron app and its native child view, not an H5 mock.
const {_electron}=require('/Users/USER/resources/YOUR_7B0895B9F9');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {Store}=require('../src/store.cjs');
const root=path.resolve(__dirname,'..'),out=process.env.STUDIO_WEBSITE_QA_DIR;
if(!out||!path.isAbsolute(out))throw new Error('private_qa_directory_required');
const phase=process.env.STUDIO_WEBSITE_QA_PHASE||'modified';
fs.mkdirSync(out,{recursive:true,mode:0o700});
const state=path.join(out,'isolated-'+phase),store=new Store(state);
if(!store.list().accounts.length)store.add({platform:'douyin',name:'隔离验收账号'});
let application;
async function run(){
 application=await _electron.launch({executablePath:require('electron'),args:[root],env:{...process.env,STUDIO_STATE_DIR:state,STUDIO_QA:'1'}});
 const page=await application.firstWindow(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.waitForSelector('#account-rows tr');
 const nav=page.locator('[data-website-page="site"]');
 if(phase!=='baseline'&&await page.locator('[data-nav="website"]').getAttribute('aria-expanded')!=='true')await page.locator('[data-nav="website"]').click();
 if(phase==='baseline'){
  assert.equal(await nav.count(),0);
  const image=await application.evaluate(async()=>Array.from((await global.__studioQA.win.capturePage()).toPNG()));
  fs.writeFileSync(path.join(out,'baseline.png'),Buffer.from(image),{mode:0o600});
  console.log(JSON.stringify({phase,website_entry:false,account_list_visible:await page.locator('#account-page').isVisible(),errors}));
  await application.close();application=null;return;
 }
 const labels=await page.locator('.nav-label').allTextContents();assert.equal(labels.indexOf('官网'),labels.indexOf('数据')+1);
 const initial=await application.evaluate(()=>global.__studioQA.store.list());
 await nav.click();await page.waitForSelector('#website-page:not([hidden])');
 try{await page.waitForFunction(()=>['ready','error'].includes(document.getElementById('website-status').dataset.phase),null,{timeout:45000});}catch(error){
  console.log(JSON.stringify(await application.evaluate(async()=>{const q=global.__studioQA,wc=q.websitePane.view.webContents;return {website:q.websitePane.state(),url:wc.getURL(),loading:wc.isLoading(),body:await wc.executeJavaScript('(document.body?.innerText||"").slice(0,300)').catch(e=>e.message)};})));
  const bytes=await application.evaluate(async()=>Array.from((await global.__studioQA.win.capturePage()).toPNG()));fs.writeFileSync(path.join(out,'website-load-failure.png'),Buffer.from(bytes));throw error;
 }
 // A main-document finish can be followed by a subframe starting immediately.
 await page.waitForFunction(()=>document.getElementById('website-status').dataset.phase==='ready',null,{timeout:45000});
 const probe=await application.evaluate(async({BrowserWindow})=>{
  const q=global.__studioQA,v=q.websitePane.view,wc=v.webContents;
  return {url:wc.getURL(),title:wc.getTitle(),bounds:v.getBounds(),children:q.win.contentView.children.length,windows:BrowserWindow.getAllWindows().length,preferences:wc.getLastWebPreferences(),partitionSeparate:wc.session!==q.makeView(q.store.list().accounts[0]).webContents.session,
   remoteBridge:await wc.executeJavaScript('typeof window.studio'),body:await wc.executeJavaScript('(document.body?.innerText||"").slice(0,1000)')};
 });
 if(phase==='layout-baseline'){
  assert.equal(await page.locator('.topbar').isVisible(),true);assert.equal(await page.locator('.website-toolbar').isVisible(),true);assert.ok(probe.bounds.y>100);
  console.log(JSON.stringify({phase,topbar_visible:true,website_toolbar_visible:true,website_bounds:probe.bounds}));await application.close();application=null;return;
 }
 assert.equal(new URL(probe.url).hostname,'example.invalid');assert.equal(probe.children,1);assert.equal(probe.windows,1);assert.ok(probe.bounds.height>200);assert.equal(probe.bounds.y,0);
 assert.equal(await page.locator('.topbar').isVisible(),false);assert.equal(await page.locator('.website-toolbar').isVisible(),false);assert.equal(await page.locator('#website-status').isVisible(),false);assert.equal(await page.locator('.rail').isVisible(),true);
 assert.equal(probe.preferences.nodeIntegration,false);assert.equal(probe.preferences.contextIsolation,true);assert.equal(probe.preferences.sandbox,true);
 assert.equal(probe.remoteBridge,'undefined');assert.equal(probe.partitionSeparate,true);assert.ok(probe.body.length>50);
 const capture=async name=>{const bytes=await application.evaluate(async()=>Array.from((await global.__studioQA.win.capturePage()).toPNG()));const file=path.join(out,name+'.png');fs.writeFileSync(file,Buffer.from(bytes),{mode:0o600});return file;};
 const screenshots=[await capture('website-in-app')];
 // BrowserWindow.capturePage captures the local shell; record the native web
 // contents separately. Full compositor visibility is checked in the built App.
 const remote=await application.evaluate(async()=>Array.from((await global.__studioQA.websitePane.view.webContents.capturePage()).toPNG()));
 const remoteFile=path.join(out,'website-webview.png');fs.writeFileSync(remoteFile,Buffer.from(remote),{mode:0o600});screenshots.push(remoteFile);
 await page.locator('[data-nav="account"]').click();await page.waitForSelector('#account-page:not([hidden])');assert.equal(await page.locator('.topbar').isVisible(),true);
 await page.locator('#collapse-rail').click();await nav.click();await page.waitForSelector('#website-page:not([hidden])');
 await page.waitForFunction(()=>document.body.classList.contains('collapsed'));
 // A round-trip IPC lets the resize request complete before reading native bounds.
 await page.evaluate(()=>window.studio.website.state());
 const collapsed=await application.evaluate(()=>global.__studioQA.websitePane.view.getBounds());assert.ok(collapsed.x<probe.bounds.x);
 await page.locator('[data-nav="account"]').click();await page.locator('#collapse-rail').click();await nav.click();
 // Failure feedback appears only on errors; successful content remains edge-to-edge.
 await application.evaluate(()=>global.__studioQA.websitePane.onEvent({phase:'error',message:'官网加载失败，可刷新重试',hostname:'example.invalid',canGoBack:false}));
 await page.locator('.website-toolbar').waitFor({state:'visible'});assert.equal(await page.locator('#website-status').isVisible(),true);
 await application.evaluate(()=>global.__studioQA.websitePane.emit());await page.locator('.website-toolbar').waitFor({state:'hidden'});
 await page.evaluate(()=>window.studio.website.command('reload'));
 await page.waitForFunction(()=>document.getElementById('website-status').dataset.phase==='ready',null,{timeout:45000});
 await page.locator('[data-nav="account"]').click();await page.waitForSelector('#account-page:not([hidden])');
 assert.equal(await application.evaluate(()=>global.__studioQA.win.contentView.children.length),0);
 await page.locator('[data-tab="groups"]').click();assert.equal(await page.locator('#group-page').isVisible(),true);
 await nav.click();await page.waitForSelector('#website-page:not([hidden])');
 assert.equal(await application.evaluate(()=>global.__studioQA.win.contentView.children.length),1);
 await page.evaluate(()=>window.studio.website.command('home'));
 await page.waitForFunction(()=>document.getElementById('website-status').dataset.phase==='ready',null,{timeout:45000});
 screenshots.push(await capture('website-reopened'));
 // Returning to accounts intentionally refreshes metadata timestamps.
 const final=await application.evaluate(()=>global.__studioQA.store.list());assert.deepEqual(final.accounts,initial.accounts);assert.deepEqual(final.groups,initial.groups);assert.deepEqual(errors,[]);
 const result={phase,website_entry_after_data:true,embedded_loaded:true,minimal_layout:true,website_starts_at_top:probe.bounds.y===0,normal_chrome_hidden:true,error_retry_visible:true,account_topbar_restored:true,url:probe.url,title:probe.title,remote_without_bridge:true,isolated_from_account_sessions:true,return_and_reopen:true,sidebar_resize:true,account_data_unchanged:true,errors,screenshots};
 fs.writeFileSync(path.join(out,'website-acceptance.json'),JSON.stringify(result,null,2),{mode:0o600});console.log(JSON.stringify(result));
 await application.close();application=null;
}
run().catch(async e=>{console.error(e.stack);if(application)await application.close().catch(()=>{});process.exitCode=1;});
