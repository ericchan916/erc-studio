'use strict';
const {_electron}=require('/Users/USER/resources/YOUR_7B0895B9F9'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {MetadataError}=require('../src/metadata.cjs');
const root=path.resolve(__dirname,'..'),out=path.join(os.homedir(),'Library/Application Support/ContentBaseChanges/20261001-studio-live-refresh/qa'),state=path.join(out,'isolated-state');fs.mkdirSync(out,{recursive:true,mode:0o700});
async function run(){let app;const records={scope:'real_yxer_read_isolated_store',screenshots:[]};
 async function launch(){app=await _electron.launch({executablePath:require('electron'),args:[root],env:{...process.env,STUDIO_STATE_DIR:state,STUDIO_QA:'1',STUDIO_QA_REAL_METADATA:'1'}});const page=await app.firstWindow();page.on('pageerror',e=>{(records.page_errors||=[]).push(e.message);});return page;}
 async function shot(page,name){const file=path.join(out,name+'.png');await page.screenshot({path:file});fs.chmodSync(file,0o600);records.screenshots.push(file);}
 try{
  let page=await launch();await page.waitForFunction(()=>document.getElementById('data-freshness').textContent.startsWith('蚁小二查询成功'),null,{timeout:35000}).catch(async e=>{records.start_failure={refresh:await app.evaluate(()=>global.__studioQA.metadataRefresh.state()),text:await page.locator('#data-freshness').textContent()};throw e;});
  const initial=await app.evaluate(()=>({state:global.__studioQA.metadataRefresh.state(),accounts:global.__studioQA.store.list().accounts}));assert.ok(initial.accounts.length>0);records.initial={count:initial.accounts.length,refresh:initial.state};await shot(page,'01-real-open-refreshed');
  await page.locator('[data-tab="groups"]').click();await page.locator('[data-tab="accounts"]').click();await page.waitForFunction(previous=>{const e=document.getElementById('data-freshness');return e.dataset.phase==='ok'&&e.dataset.updatedAt!==previous;},initial.state.lastSuccessAt,{timeout:35000});
  const second=await app.evaluate(()=>global.__studioQA.metadataRefresh.state());records.reentry=second;assert.notEqual(second.lastSuccessAt,initial.state.lastSuccessAt);await shot(page,'02-real-reentry-refreshed');
  const before=await app.evaluate(()=>global.__studioQA.store.list().accounts.map(a=>({id:a.id,fans:a.fans,partition:a.partition,status:a.status})));await app.evaluate(()=>{global.__studioQA.originalRead=global.__studioQA.metadataRefresh.read;global.__studioQA.metadataRefresh.read=async()=>{throw {code:'timeout'};};});
  await page.locator('[data-tab="groups"]').click();await page.locator('[data-tab="accounts"]').click();await page.waitForFunction(()=>document.getElementById('data-freshness').textContent.includes('数据查询失败'));
  const after=await app.evaluate(()=>global.__studioQA.store.list().accounts.map(a=>({id:a.id,fans:a.fans,partition:a.partition,status:a.status})));assert.deepEqual(after,before);records.failure_preserved_values=true;await shot(page,'03-failure-keeps-values');
  await app.evaluate(()=>{global.__studioQA.metadataRefresh.read=global.__studioQA.originalRead;});await page.locator('#retry-refresh').click();await page.waitForFunction(()=>document.getElementById('data-freshness').textContent.startsWith('蚁小二查询成功'),null,{timeout:35000});records.retry_recovered=true;
  await app.close();page=await launch();await page.waitForFunction(()=>document.getElementById('data-freshness').textContent.startsWith('蚁小二查询成功'),null,{timeout:35000});records.restart=await app.evaluate(()=>global.__studioQA.metadataRefresh.state());await shot(page,'04-restart-new-read');assert.notEqual(records.restart.lastSuccessAt,initial.state.lastSuccessAt);await app.close();records.ok=true;
 }finally{if(app)await app.close().catch(()=>{});fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify(records,null,2),{mode:0o600});}
 console.log(JSON.stringify(records));}
run().catch(e=>{console.error(e.stack);process.exitCode=1;});
