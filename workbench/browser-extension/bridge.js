'use strict';
(()=>{function announce(){document.documentElement.dataset.cyberFeishuEmbed='enabled';
document.dispatchEvent(new CustomEvent('cyber-feishu-embed-ready'));

document.documentElement.dataset.cyberYxerEmbed='enabled';
document.documentElement.dataset.cyberYxerEmbedVersion='0.3.2';
document.dispatchEvent(new CustomEvent('cyber-yxer-embed-ready'));

document.documentElement.dataset.cyberPublisherEmbed='enabled';
document.dispatchEvent(new CustomEvent('cyber-publisher-embed-ready'));

document.documentElement.dataset.cyberWechatContainerUrl=chrome.runtime.getURL('publisher-host.html');
chrome.runtime.sendMessage({type:'studio-x-refresh-check'}).then(result=>{document.documentElement.dataset.cyberXRefreshBridge=result?.ok===true?'enabled':'failed';document.dispatchEvent(new CustomEvent('cyber-x-refresh-ready'));}).catch(()=>{document.documentElement.dataset.cyberXRefreshBridge='failed';});
}
if(document.documentElement)announce();else{const observer=new MutationObserver(()=>{if(document.documentElement){observer.disconnect();announce();}});observer.observe(document,{childList:true,subtree:true});}})();
