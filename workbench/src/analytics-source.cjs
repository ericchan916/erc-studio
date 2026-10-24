'use strict';
const path=require('node:path');const {pathToFileURL}=require('node:url');
let providerPromise;
async function collect(config,stateRoot,days){
 if(!providerPromise)providerPromise=(async()=>{
  const root=config.sourceRoot;if(!path.isAbsolute(root||''))throw new Error('analytics_source_path_invalid');
  const load=relative=>import(pathToFileURL(path.join(root,relative)).href);
  const {loadPrivateAnalyticsConfig}=await load('server/modules/privateAnalyticsConfig.mjs');
  const {createTencentRumAnalyticsProviderFromEnvironment}=await import(pathToFileURL(path.join(__dirname,'tencent-rum/tencentRumAnalyticsProvider.mjs')).href);
  const privateConfig=await loadPrivateAnalyticsConfig({filePath:path.join(root,'.env.analytics.local'),environment:{}});
  return createTencentRumAnalyticsProviderFromEnvironment(privateConfig);
 })().catch(e=>{providerPromise=null;throw e;});
 const provider=await providerPromise;return provider.getSummary({days,now:new Date()});
}
module.exports={collect};
