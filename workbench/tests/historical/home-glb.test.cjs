'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {parseGLB,jpegSize,pointerPose}=require('../web/home-glb.js'),{readHomeModel,MODEL_SHA256}=require('../src/home-model.cjs'),{patchShared}=require('../scripts/install-home-model.cjs');
const bytes=fs.readFileSync(path.join(__dirname,'../web/assets/model.glb'));
test('the original supplied GLB is preserved byte-for-byte with embedded JPEG and real geometry',()=>{
 assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),MODEL_SHA256);const m=parseGLB(bytes);assert.equal(m.vertices,40881);assert.equal(m.triangles,44194);assert.deepEqual(jpegSize(m.image),{width:8192,height:8192});assert.ok(m.scale>2&&m.scale<3);assert.ok(m.indices.every(i=>i<m.vertices));
});
test('fixed packaged model IPC returns only verified asset bytes and never caller-selected files',()=>{
 const packet=readHomeModel('/etc/passwd');assert.equal(packet.sha256,MODEL_SHA256);assert.equal(packet.bytes.length,bytes.length);assert.deepEqual([...packet.bytes.slice(0,4)],[103,108,84,70]);assert.deepEqual(Object.keys(packet).sort(),['bytes','sha256']);
});
test('malformed GLB headers, truncated chunks and unsupported external buffers are bounded errors',()=>{
 for(const length of [0,12,20,bytes.length-1])assert.throws(()=>parseGLB(bytes.subarray(0,length)));
 const broken=Buffer.from(bytes);broken.writeUInt32LE(999,4);assert.throws(()=>parseGLB(broken),/glb_header_invalid/);
 const badChunk=Buffer.from(bytes);badChunk.writeUInt32LE(bytes.length,12);assert.throws(()=>parseGLB(badChunk),/glb_chunk_invalid/);
 const n=bytes.readUInt32LE(12),json=JSON.parse(bytes.subarray(20,20+n).toString()),binOffset=20+n,bin=bytes.subarray(binOffset);json.buffers[0].uri='https://fixture.invalid/model.bin';const text=Buffer.from(JSON.stringify(json)),padded=Buffer.concat([text,Buffer.alloc((4-text.length%4)%4,32)]),result=Buffer.alloc(20+padded.length+bin.length);bytes.copy(result,0,0,12);result.writeUInt32LE(result.length,8);result.writeUInt32LE(padded.length,12);result.writeUInt32LE(0x4e4f534a,16);padded.copy(result,20);bin.copy(result,20+padded.length);assert.throws(()=>parseGLB(result),/glb_layout_unsupported/);
});
test('no rig or existing animation is falsely advertised; only rigid transforms and bounded texture upload',()=>{
 const n=bytes.readUInt32LE(12),json=JSON.parse(bytes.subarray(20,20+n));assert.equal(json.skins?.length||0,0);assert.equal(json.animations?.length||0,0);
 const source=fs.readFileSync(path.join(__dirname,'../web/home-glb.js'),'utf8');assert.ok(source.includes('Math.min(2048'));assert.ok(source.includes('Math.min(window.devicePixelRatio||1,1.5)'));assert.ok(source.includes("'(prefers-reduced-motion: reduce)'"));assert.ok(source.includes('document.hidden'));assert.ok(!/fetch\(|XMLHttpRequest|eval\(|new Function/.test(source));
});
test('mouse pose centers and clamps whole-model yaw/pitch instead of inventing skeleton motion',()=>{
 assert.deepEqual(pointerPose(210,160,420,320),{yaw:0,pitch:0});assert.deepEqual(pointerPose(10000,10000,420,320),{yaw:.3,pitch:.08});assert.deepEqual(pointerPose(-10000,-10000,420,320),{yaw:-.3,pitch:-.08});
});
test('narrow deployed overlay preserves analytics and prior IPC/UI when adding model asset hooks',()=>{
 const root=path.resolve(__dirname,'..'),main=fs.readFileSync(path.join(root,'src/main.cjs'),'utf8').replace("const {readHomeModel}=require('./home-model.cjs');\n",'').replace(" bind('studio:home-model',()=>readHomeModel());\n",'');
 const decorated=main+"\n// analytics module retained\nconst fixtureAnalytics='unchanged';\n";const patched=patchShared('src/main.cjs',decorated);assert.ok(patched.endsWith("const fixtureAnalytics='unchanged';\n"));assert.ok(patched.includes("bind('studio:home-model'"));assert.ok(patched.includes('trusted(e)'));assert.throws(()=>patchShared('src/main.cjs',patched),/overlay_anchor_changed/);
 const html='<script src="analytics-ui.js"></script><canvas aria-label="程序化三维内容占位，尚未加载Tripo模型"></canvas><script src="home-ui.js"></script>';const result=patchShared('web/index.html',html);assert.ok(result.includes('analytics-ui.js'));assert.ok(result.includes('home-glb.js'));assert.ok(result.includes('可点击转身'));
});
