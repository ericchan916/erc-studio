'use strict';
async function allowStudioFeishuCookies(){
  await chrome.contentSettings.cookies.set({primaryPattern:'https://*.feishu.cn/*',secondaryPattern:'https://studio.example.invalid/*',setting:'allow',scope:'regular'});
  await chrome.contentSettings.cookies.set({primaryPattern:'https://www.yixiaoer.cn/*',secondaryPattern:'https://studio.example.invalid/*',setting:'allow',scope:'regular'});
  for(const domain of ['mp.weixin.qq.com','x.com'])await chrome.contentSettings.cookies.set({primaryPattern:'https://'+domain+'/*',secondaryPattern:'https://studio.example.invalid/*',setting:'allow',scope:'regular'});
}
chrome.runtime.onInstalled.addListener(()=>allowStudioFeishuCookies());
chrome.runtime.onStartup.addListener(()=>allowStudioFeishuCookies());
allowStudioFeishuCookies();

// Only the local extension's WeChat child document. No network/body/header proxy.
const wechatContainerRule=chrome.declarativeNetRequest.updateSessionRules({removeRuleIds:[4],addRules:[{
 id:4,priority:200,action:{type:'modifyHeaders',responseHeaders:[
  {header:'x-frame-options',operation:'remove'},
  {header:'content-security-policy',operation:'remove'},
  {header:'content-security-policy-report-only',operation:'remove'}
 ]},condition:{initiatorDomains:[chrome.runtime.id],requestDomains:['mp.weixin.qq.com'],resourceTypes:['sub_frame']}
}]}).then(()=>({ok:true}),()=>({ok:false,code:'wechat_container_rule_failed'}));
// Bind document exceptions to verified Studio tab IDs, not navigation initiators.
const xStudioOrigin='https://studio.example.invalid',xOwnerTabs=new Set();let xRuleQueue=Promise.resolve();
const xStudioURL=url=>{try{return new URL(url).origin===xStudioOrigin;}catch{return false;}};
function syncXTabRules(addTabId){
 xRuleQueue=xRuleQueue.catch(()=>{}).then(async()=>{
  const previous=(await chrome.declarativeNetRequest.getSessionRules()).find(r=>r.id===5);
  for(const id of previous?.condition?.tabIds||[])xOwnerTabs.add(id);
  if(Number.isInteger(addTabId))xOwnerTabs.add(addTabId);
  for(const id of [...xOwnerTabs]){try{const tab=await chrome.tabs.get(id);if(!xStudioURL(tab.url))xOwnerTabs.delete(id);}catch{xOwnerTabs.delete(id);}}
  const ids=[...xOwnerTabs].sort((a,b)=>a-b),rule={id:5,priority:210,action:{type:'modifyHeaders',responseHeaders:[{header:'x-frame-options',operation:'remove'},{header:'content-security-policy',operation:'remove'},{header:'content-security-policy-report-only',operation:'remove'}]},condition:{tabIds:ids,requestDomains:['x.com'],resourceTypes:['sub_frame']}};
  await chrome.declarativeNetRequest.updateSessionRules({removeRuleIds:[5],addRules:ids.length?[rule]:[]});
  if(!Number.isInteger(addTabId))return {ok:true};
  const registered=(await chrome.declarativeNetRequest.getSessionRules()).find(r=>r.id===5),c=registered?.condition;
  const exact=(a,b)=>Array.isArray(a)&&a.length===b.length&&b.every(v=>a.includes(v));
  if(!ids.includes(addTabId)||!c||!exact(c.tabIds,ids)||!exact(c.requestDomains,['x.com'])||!exact(c.resourceTypes,['sub_frame'])||c.initiatorDomains||c.topDomains){await chrome.declarativeNetRequest.updateSessionRules({removeRuleIds:[5]});return {ok:false,code:'x_refresh_scope_failed'};}
  return {ok:true};
 }).catch(()=>({ok:false,code:'x_refresh_rule_failed'}));return xRuleQueue;
}
if(chrome.tabs?.onUpdated)chrome.tabs.onUpdated.addListener(id=>{if(xOwnerTabs.has(id))syncXTabRules();});
if(chrome.tabs?.onRemoved)chrome.tabs.onRemoved.addListener(id=>{if(xOwnerTabs.delete(id))syncXTabRules();});
syncXTabRules();
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(message?.type==='studio-x-refresh-check'){
  const trusted=sender.frameId===0&&Number.isInteger(sender.tab?.id)&&xStudioURL(sender.url);
  if(!trusted){respond({ok:false,code:'x_refresh_sender_invalid'});return false;}
  syncXTabRules(sender.tab.id).then(respond);return true;
 }

 if(message?.type!=='studio-wechat-host-check')return false;
 if(sender.url!==chrome.runtime.getURL('publisher-host.html')){respond({ok:false,code:'container_sender_invalid'});return false;}
 wechatContainerRule.then(respond);return true;
});

if(typeof importScripts==='function')importScripts('x-api-relay-background.js');
if(typeof importScripts==='function')importScripts('wechat-session-background.js');
