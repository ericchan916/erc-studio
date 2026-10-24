'use strict';
const path=require('node:path'),os=require('node:os');const {AnalyticsService}=require('../src/analytics.cjs');
const root=process.env.STUDIO_STATE_DIR||path.join(os.homedir(),'Library/Application Support/ERC Studio');
const service=new AnalyticsService(root,{onChange:state=>console.log(JSON.stringify({phase:state.syncPhase}))});
service.sync(Number(process.argv[2]||14)).then(data=>console.log(JSON.stringify({ok:true,days:data.days,layers:data.layers.map(x=>({source:x.id,pv:x.current.pv,uv:x.current.uv})),counts:data.syncCounts}))).catch(error=>{console.error(JSON.stringify({ok:false,error:error.message}));process.exitCode=1;});
