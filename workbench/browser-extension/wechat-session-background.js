'use strict';
// Permit native provider self-navigation only inside verified Studio tabs. No Cookie/API proxy.
(()=>{
 const studio='https://studio.example.invalid',owners=new Set();let queue=Promise.resolve();
 const isStudio=url=>{try{return new URL(url).origin===studio;}catch{return false;}};
 function sync(add){
  queue=queue.catch(()=>{}).then(async()=>{
   const old=(await chrome.declarativeNetRequest.getSessionRules()).find(r=>r.id===7);
   for(const id of old?.condition?.tabIds||[])owners.add(id);
   if(Number.isInteger(add))owners.add(add);
   for(const id of [...owners]){try{if(!isStudio((await chrome.tabs.get(id)).url))owners.delete(id);}catch{owners.delete(id);}}
   const ids=[...owners].sort((a,b)=>a-b),rule={id:7,priority:220,action:{type:'modifyHeaders',responseHeaders:[{header:'x-frame-options',operation:'remove'},{header:'content-security-policy',operation:'remove'},{header:'content-security-policy-report-only',operation:'remove'}]},condition:{tabIds:ids,requestDomains:['mp.weixin.qq.com'],resourceTypes:['sub_frame']}};
   await chrome.declarativeNetRequest.updateSessionRules({removeRuleIds:[7],addRules:ids.length?[rule]:[]});
   if(!Number.isInteger(add))return {ok:true};
   const current=(await chrome.declarativeNetRequest.getSessionRules()).find(r=>r.id===7),c=current?.condition;
   const exact=(a,b)=>Array.isArray(a)&&a.length===b.length&&b.every(v=>a.includes(v));
   if(!ids.includes(add)||!c||!exact(c.tabIds,ids)||!exact(c.requestDomains,['mp.weixin.qq.com'])||!exact(c.resourceTypes,['sub_frame'])||c.initiatorDomains||c.topDomains){await chrome.declarativeNetRequest.updateSessionRules({removeRuleIds:[7]});return {ok:false,code:'wechat_session_scope_failed'};}
   return {ok:true};
  }).catch(()=>({ok:false,code:'wechat_session_rule_failed'}));return queue;
 }
 chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m?.type!=='studio-wechat-session-check')return false;
  if(sender.url!==chrome.runtime.getURL('publisher-host.html')||sender.frameId<=0||!Number.isInteger(sender.tab?.id)){reply({ok:false,code:'wechat_session_sender_invalid'});return false;}
  sync(sender.tab.id).then(reply);return true;
 });
 chrome.tabs.onUpdated.addListener(id=>{if(owners.has(id))sync();});
 chrome.tabs.onRemoved.addListener(id=>{if(owners.delete(id))sync();});
 sync();
})();
