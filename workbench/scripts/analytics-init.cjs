'use strict';
const path=require('node:path'),os=require('node:os');const {initialize}=require('../src/analytics.cjs');
const root=process.env.STUDIO_STATE_DIR||path.join(os.homedir(),'Library/Application Support/ERC Studio');
initialize(root).then(config=>console.log(JSON.stringify({ok:true,tables:Object.keys(config.tables).length}))).catch(error=>{console.error(JSON.stringify({ok:false,error:error.message,signal:error.signal}));process.exitCode=1;});
