'use strict';
// Personal GLB assets are excluded. A user may supply a private model explicitly.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const MODEL_SHA256=process.env.CONTENTBASE_MODEL_SHA256||'';
function readHomeModel(){
 const file=process.env.CONTENTBASE_MODEL_FILE;
 if(!file||!path.isAbsolute(file)||! /^[a-f0-9]{64}$/.test(MODEL_SHA256))throw new Error('home_model_unconfigured');
 if(fs.lstatSync(file).isSymbolicLink())throw new Error('home_model_integrity_invalid');
 const size=fs.statSync(file).size;if(size<20||size>32*1024*1024)throw new Error('home_model_size_invalid');
 const bytes=fs.readFileSync(file);
 if(bytes.toString('ascii',0,4)!=='glTF'||bytes.readUInt32LE(4)!==2||bytes.readUInt32LE(8)!==bytes.length||crypto.createHash('sha256').update(bytes).digest('hex')!==MODEL_SHA256)throw new Error('home_model_integrity_invalid');
 return {bytes:new Uint8Array(bytes),sha256:MODEL_SHA256};
}
module.exports={readHomeModel,MODEL_SHA256};
