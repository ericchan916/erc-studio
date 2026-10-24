'use strict';
// Provider-origin request transport. Only ct0 is read with explicit user approval; auth_token and Cookie stores stay untouched.
(()=>{
 const studio='https://studio.example.invalid',api='https://api.x.com';
 const readOps=new Set(["HomeTimeline","HomeLatestTimeline","UserByScreenName","UserSportsProfile","UserOriginalsTimeline","ExplorePage","PinnedTimelines","ExploreSidebar","SidebarUserRecommendations","useStoryTopicQuery","NFLSchedule","SportsLeagueFollow","FetchDraftTweets","FetchScheduledTweets","ViewerBadgeCounts","getAltTextPromptPreference","useSubscriptionsPaymentFailureQuery","useFetchProductSubscriptionsQuery","DataSaverMode","CreatorStudioTabBarItemQuery"]),readPaths=new Set(["/1.1/account/settings.json","/1.1/search/typeahead.json"]);
 function readPath(path){const match=/^\/graphql\/[A-Za-z0-9_-]+\/([A-Za-z0-9_]+)$/.exec(path);return match?readOps.has(match[1]):readPaths.has(path);}
 const nativeCleanup=chrome.declarativeNetRequest.updateSessionRules({removeRuleIds:[6]});
 const requestHeaders=new Set(['accept','authorization','content-type','x-csrf-token','x-twitter-active-user','x-twitter-auth-type','x-twitter-client-language','x-client-transaction-id']);
 async function eligible(sender){try{if(sender.frameId<=0||!Number.isInteger(sender.tab?.id)||new URL(sender.url).origin!=='https://x.com')return false;return new URL((await chrome.tabs.get(sender.tab.id)).url).origin===studio;}catch{return false;}}
 chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(!['studio-x-api-ready','studio-x-api-timeline'].includes(m?.type))return false;
  (async()=>{
   if(!await eligible(sender)){reply({ok:false,code:'not_eligible'});return;}
   if(m.type==='studio-x-api-ready'){await nativeCleanup;reply({ok:true});return;}
   if(typeof m.path!=='string'||m.path.length>32768||!m.path.startsWith('/')){reply({ok:false,code:'route_denied'});return;}
   const url=new URL(m.path,api),method=m.method||'GET';
   const graphql=/^\/graphql\/[A-Za-z0-9_-]+\/[A-Za-z0-9_]+$/.test(url.pathname),rest=/^\/(?:1\.1|2)\/[A-Za-z0-9_.,%/-]+$/.test(url.pathname);
   if(url.origin!==api||url.username||url.password||url.hash||(!graphql&&!rest)||!['GET','POST'].includes(method)){reply({ok:false,code:'route_denied'});return;}
   if(method==='GET'&&['CreateTweet','DeleteTweet','FavoriteTweet','UnfavoriteTweet','CreateRetweet','DeleteRetweet','CreateBookmark','DeleteBookmark'].includes(url.pathname.split('/').at(-1))){reply({ok:false,code:'route_denied'});return;}
   if(method==='POST'&&!readPath(url.pathname)&&m.gesture!==true){reply({ok:false,code:'manual_action_required'});return;}
   if(method==='POST'&&m.body!=null&&(typeof m.body!=='string'||m.body.length>2*1024*1024)){reply({ok:false,code:'body_denied'});return;}
   if(!Array.isArray(m.headers)||m.headers.length>30){reply({ok:false,code:'headers_denied'});return;}
   const headers={};for(const pair of m.headers){if(!Array.isArray(pair)||pair.length!==2||typeof pair[0]!=='string'||typeof pair[1]!=='string'||pair[1].length>8192)throw Error('invalid');const name=pair[0].toLowerCase();if(requestHeaders.has(name))headers[name]=pair[1];}
   // Match X's standard CSRF header to this browser's own current unpartitioned ct0.
   const csrf=await chrome.cookies.get({url:'https://x.com/',name:'ct0'});
   if(!csrf?.value){reply({ok:false,code:'csrf_cookie_missing'});return;}
   headers['x-csrf-token']=csrf.value;
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
   try{const response=await fetch(url.href,{method,body:method==='POST'?m.body:undefined,headers,credentials:'include',redirect:'error',cache:'no-store',signal:controller.signal});
    const text=await response.text();if(text.length>8*1024*1024)throw Error('response_size');
    let codes=[];if(response.status>=400){try{codes=JSON.parse(text).errors?.map(e=>e.code).filter(Number.isInteger).slice(0,5)||[];}catch{}}
    if(!await eligible(sender)){reply({ok:false,code:'not_eligible'});return;}
    reply({ok:true,status:response.status,statusText:response.statusText,text,contentType:response.headers.get('content-type')||'application/json',diagnostic:{status:response.status,codes,csrfHeaderPresent:Boolean(headers['x-csrf-token'])}});
   }finally{clearTimeout(timer);}
  })().catch(()=>reply({ok:false,code:'relay_failed'}));return true;
 });
})();
