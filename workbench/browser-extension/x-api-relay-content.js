'use strict';
(()=>{
 if(window.top===window.self||location.origin!=='https://x.com')return;
 let active=false,lastGesture=-Infinity;
 for(const type of ['pointerdown','keydown','submit'])window.addEventListener(type,event=>{if(event.isTrusted)lastGesture=performance.now();},true);
 chrome.runtime.sendMessage({type:'studio-x-api-ready'}).then(r=>{active=r?.ok===true;window.postMessage({type:'studio-x-api-ready',active,metadata:r?.metadata},'https://x.com');}).catch(()=>{});
 window.addEventListener('message',async event=>{
  if(!active||event.source!==window||event.origin!=='https://x.com'||event.data?.type!=='studio-x-api-timeline-request')return;
  const m=event.data;if(typeof m.id!=='string'||!/^x-api-[a-z0-9-]{1,90}$/.test(m.id))return;
  let result;try{result=await chrome.runtime.sendMessage({type:'studio-x-api-timeline',path:m.path,headers:m.headers,method:m.method,body:m.body,gesture:performance.now()-lastGesture<10000});}catch{result={ok:false,code:'relay_disconnected'};}
  window.postMessage({type:'studio-x-api-timeline-response',id:m.id,result},'https://x.com');
 });
})();
