'use strict';
(()=>{
 if(window.top===window.self||location.origin!=='https://x.com')return;
 let enabled=false,dead=false;const requests=new Map(),logged=new Set();
 const readOps=new Set(["HomeTimeline","HomeLatestTimeline","UserByScreenName","UserSportsProfile","UserOriginalsTimeline","ExplorePage","PinnedTimelines","ExploreSidebar","SidebarUserRecommendations","useStoryTopicQuery","NFLSchedule","SportsLeagueFollow","FetchDraftTweets","FetchScheduledTweets","ViewerBadgeCounts","getAltTextPromptPreference","useSubscriptionsPaymentFailureQuery","useFetchProductSubscriptionsQuery","DataSaverMode","CreatorStudioTabBarItemQuery"]),readPaths=new Set(["/1.1/account/settings.json","/1.1/search/typeahead.json"]);
 function readPath(path){const match=/^\/graphql\/[A-Za-z0-9_-]+\/([A-Za-z0-9_]+)$/.exec(path);return match?readOps.has(match[1]):readPaths.has(path);}

 window.addEventListener('message',event=>{
  if(event.source!==window||event.origin!=='https://x.com')return;const m=event.data;
  if(m?.type==='studio-x-api-ready'){enabled=m.active===true;if(['lax','strict','no_restriction','unspecified'].includes(m.metadata?.sameSite))console.info('STUDIO_X_CSRF_METADATA',m.metadata.sameSite);return;}
  if(m?.type==='studio-x-api-timeline-response'&&requests.has(m.id)){const pending=requests.get(m.id);requests.delete(m.id);clearTimeout(pending.timer);pending.resolve(m.result);}
 });
 window.addEventListener('pagehide',()=>{dead=true;for(const p of requests.values())clearTimeout(p.timer);requests.clear();});
 const NativeXHR=window.XMLHttpRequest;
 class NativeTimelineXHR extends NativeXHR{
  open(method,url,...rest){if(this._studioNative)this._studioNative.generation++;for(const key of ['readyState','status','statusText','responseURL','responseText','response','getResponseHeader','getAllResponseHeaders'])if(Object.hasOwn(this,key))delete this[key];this._studioNative={method:String(method).toUpperCase(),url:String(url),headers:[],generation:0};return super.open(method,url,...rest);}
  setRequestHeader(name,value){this._studioNative?.headers.push([String(name),String(value)]);return super.setRequestHeader(name,value);}
  send(body){const state=this._studioNative;let url;try{url=new URL(state?.url,location.href);}catch{return super.send(body);}
   if(dead||!enabled||!['GET','POST'].includes(state?.method)||url.origin!=='https://api.x.com'||!/^\/(?:graphql|1\.1|2)\//.test(url.pathname)||(state.method==='POST'&&body!=null&&typeof body!=='string'))return super.send(body);
   const id='x-api-'+crypto.randomUUID(),generation=++state.generation;
   this.dispatchEvent(new ProgressEvent('loadstart'));
   const response=new Promise(resolve=>{const timer=setTimeout(()=>{requests.delete(id);resolve({ok:false,code:'relay_timeout'});},22000);requests.set(id,{resolve,timer});});
   window.postMessage({type:'studio-x-api-timeline-request',id,path:url.pathname+url.search,headers:state.headers,method:state.method,body:state.method==='POST'?body:undefined},'https://x.com');
   response.then(r=>{
    if(dead||generation!==state.generation)return;
    const failed=!r?.ok,text=failed?'':r.text,status=failed?0:r.status;
    for(const [key,value]of Object.entries({readyState:4,status,statusText:failed?'':r.statusText,responseURL:url.href,responseText:text,response:this.responseType==='json'?(()=>{try{return JSON.parse(text);}catch{return null;}})():text}))Object.defineProperty(this,key,{configurable:true,get:()=>value});
    this.getResponseHeader=name=>String(name).toLowerCase()==='content-type'?(r.contentType||null):null;this.getAllResponseHeaders=()=>r.contentType?'content-type: '+r.contentType+'\r\n':'';
    const diagnostic=r?.diagnostic?{status:Number(r.diagnostic.status),codes:(r.diagnostic.codes||[]).filter(Number.isInteger),csrfHeaderPresent:r.diagnostic.csrfHeaderPresent===true}:{status:0,code:String(r?.code||'unknown')};const route=url.pathname.replace(/^\/graphql\/[^/]+\//,'/graphql/:id/').replace(/[0-9]{5,}/g,':id'),logKey=state.method+' '+route+' '+diagnostic.status+' '+(diagnostic.code||'');if(!logged.has(logKey)&&logged.size<512){logged.add(logKey);console.info('STUDIO_X_API_RELAY_RESULT',state.method,route,JSON.stringify(diagnostic));}
    this.dispatchEvent(new Event('readystatechange'));this.dispatchEvent(new ProgressEvent(failed?'error':'load'));this.dispatchEvent(new ProgressEvent('loadend'));
   });
  }
  abort(){if(this._studioNative)this._studioNative.generation++;return super.abort();}
 }
 window.XMLHttpRequest=NativeTimelineXHR;
})();
