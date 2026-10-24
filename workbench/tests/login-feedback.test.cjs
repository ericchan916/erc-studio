'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/home-chat.js'),'utf8');

test('QR refresh reports progress before the network request and exposes retryable failure',()=>{
 const start=source.indexOf('async function startScan()');
 const progress=source.indexOf('正在向飞书申请官方授权二维码…',start);
 const request=source.indexOf("await api('scanStart')",start);
 const failure=source.indexOf('二维码生成失败，请重试。',start);
 const finish=source.indexOf("async function finish(",start);
 assert.ok(start>=0&&progress>start&&request>progress,'progress must render before scanStart waits');
 assert.ok(failure>request&&failure<finish,'failure feedback must stay inside startScan');
 assert.match(source.slice(start,finish),/start\.disabled=true/);
 assert.match(source.slice(start,finish),/start\.disabled=false/);
});
