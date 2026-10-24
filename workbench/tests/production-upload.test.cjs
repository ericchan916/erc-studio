'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{webcrypto,createHash}=require('node:crypto');
const adapter=fs.readFileSync(path.join(__dirname,'../backend/web/browser-adapter.js'),'utf8');
const code=adapter.slice(adapter.indexOf('const productionUploadChunk='),adapter.indexOf('const readonly='));
const id='11111111-1111-4111-8111-111111111111';
function fixture(size,received=0,firstLength=65536,reply=200,limit=524288){
 const bytes=Buffer.alloc(size,7),file=new Blob([bytes]),firstHash=received?createHash('sha256').update(bytes.subarray(0,firstLength)).digest('hex'):null,calls=[],progress=[];
 const box={crypto:webcrypto,AbortController,setTimeout,clearTimeout,request:async()=>({state:'uploading',received,sourceFirstChunkSha256:firstHash}),fetch:async(url,options)=>{calls.push({url,offset:Number(options.headers['X-Upload-Offset']),size:options.body.size});if(reply!==200||options.body.size>limit)return {ok:false,status:reply!==200?reply:413,json:async()=>{throw SyntaxError('HTML response')}};assert.equal(calls.at(-1).offset,received);assert(options.body.size<=524288);received+=options.body.size;return {ok:true,status:200,json:async()=>({state:'uploading',received})};},uuid:()=>id};
 vm.createContext(box);vm.runInContext(code+';globalThis.upload=production.upload',box);
 return {file,calls,progress,upload:()=>box.upload(id,file,(n,total)=>progress.push([n,total]))};
}
test('route-specific upload uses 512 KiB instead of thousands of tiny requests',async()=>{const x=fixture(1200000),result=await x.upload();assert.equal(result.received,1200000);assert.deepEqual(x.calls.map(c=>c.size),[524288,524288,151424]);assert.equal(x.progress.at(-1)[0],1200000);});
test('legacy 64 KiB proxy fallback follows one definite 413 on the same job and offset',async()=>{const x=fixture(200000,0,65536,200,65536),result=await x.upload();assert.equal(result.received,200000);assert.deepEqual(x.calls.map(c=>c.size),[200000,65536,65536,65536,3392]);assert.equal(x.calls[0].offset,x.calls[1].offset);assert(x.calls.every(c=>c.url.includes(id)));});
test('resumes the same file after new 64 KiB or historic 512 KiB first chunk',async()=>{for(const first of [65536,524288]){const x=fixture(600000,first,first),result=await x.upload();assert.equal(result.received,600000);assert.equal(x.calls[0].offset,first);}});
test('HTML 413 on the bounded fallback is reported without an unlimited retry',async()=>{const x=fixture(70000,0,65536,413);await assert.rejects(x.upload(),/HTTP 413/);assert.equal(x.calls.length,2);});
test('other HTTP failures keep the same task and are not automatically replayed',async()=>{const x=fixture(70000,0,65536,503);await assert.rejects(x.upload(),/HTTP 503/);assert.equal(x.calls.length,1);});
