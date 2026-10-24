'use strict';
const path=require('node:path'),os=require('node:os');
const {Store}=require('../src/store.cjs');
const {readCLI,rowsFromPacket}=require('../src/metadata.cjs');
const root=process.env.STUDIO_STATE_DIR||path.join(os.homedir(),'Library/Application Support/ERC Studio');
readCLI().then(packet=>console.log(JSON.stringify({ok:true,...new Store(root).importMetadata(rowsFromPacket(packet)),scope:'metadata_only',cookies_imported:false}))).catch(e=>{console.error(JSON.stringify({ok:false,error:e.code||'metadata_failed'}));process.exitCode=1;});
