'use strict';
const WEBSITE_URL=process.env.CONTENTBASE_WEBSITE_URL||'';
const WEBSITE_PARTITION='persist:studio-website';
function allowedWebsiteURL(value){
 try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password&&(!url.port||url.port==='443')&&!!WEBSITE_URL&&[new URL(WEBSITE_URL).hostname,'www.'+new URL(WEBSITE_URL).hostname.replace(/^www\./,'')].includes(url.hostname);}catch{return false;}
}
function boundedRect(bounds,size){
 if(!bounds||!['x','y','width','height'].every(k=>Number.isFinite(bounds[k])))throw new Error('bounds_invalid');
 const x=Math.max(0,Math.min(Math.round(bounds.x),size[0]-1)),y=Math.max(0,Math.min(Math.round(bounds.y),size[1]-1));
 return {x,y,width:Math.max(1,Math.min(Math.round(bounds.width),size[0]-x)),height:Math.max(1,Math.min(Math.round(bounds.height),size[1]-y))};
}
class WebsitePane{
 constructor({win,WebContentsView,session,onEvent}){Object.assign(this,{win,WebContentsView,session,onEvent});this.view=null;this.visible=false;this.phase='idle';this.message='';}
 state(){const wc=this.view?.webContents;return {phase:this.phase,message:this.message,hostname:WEBSITE_URL?(wc&&!wc.isDestroyed()?new URL(wc.getURL()||WEBSITE_URL).hostname:new URL(WEBSITE_URL).hostname):'',canGoBack:!!wc&&!wc.isDestroyed()&&wc.navigationHistory.canGoBack()};}
 emit(){if(this.visible)this.onEvent(this.state());}
 create(){
  const ses=this.session.fromPartition(WEBSITE_PARTITION,{cache:true});
  ses.setPermissionRequestHandler((_wc,_permission,callback)=>callback(false));ses.setPermissionCheckHandler(()=>false);
  const view=new this.WebContentsView({webPreferences:{session:ses,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,allowRunningInsecureContent:false}}),wc=view.webContents;
  wc.setUserAgent(wc.getUserAgent().replace(/\sElectron\/\S+/g,'').replace(/\scontent-company-workbench\/\S+/g,''));
  const blocked=()=>{this.message='此链接不属于官网，已保留当前页面';this.emit();};
  for(const name of ['will-navigate','will-redirect'])wc.on(name,(event,url)=>{if(!allowedWebsiteURL(url)){event.preventDefault();blocked();}});
  wc.setWindowOpenHandler(({url})=>{if(allowedWebsiteURL(url))wc.loadURL(url).catch(()=>{});else blocked();return {action:'deny'};});
  wc.on('did-start-loading',()=>{this.phase='loading';this.message='官网加载中…';this.emit();});
  wc.on('did-finish-load',()=>{this.phase='ready';this.message='官网已加载';this.emit();});
  // Chromium may start loading a subframe after the main document finishes.
  wc.on('did-stop-loading',()=>{if(this.phase==='loading'&&allowedWebsiteURL(wc.getURL())){this.phase='ready';this.message='官网已加载';this.emit();}});
  wc.on('did-navigate-in-page',()=>this.emit());
  wc.on('did-fail-load',(_event,code,_description,_url,isMainFrame)=>{if(code===-3||isMainFrame===false)return;this.phase='error';this.message=`官网加载失败（${code}），可刷新重试`;this.emit();});
  wc.on('render-process-gone',()=>{this.phase='error';this.message='官网页面已退出，可刷新重试';this.emit();});
  this.view=view;return view;
 }
 open(){if(!WEBSITE_URL){this.visible=true;this.phase='unconfigured';this.message='本人官网地址尚未配置 CONTENTBASE_WEBSITE_URL';this.emit();return this.state();}const view=this.view||this.create();if(!this.visible){this.win.contentView.addChildView(view);this.visible=true;view.setBounds(boundedRect({x:220,y:157,width:this.win.getContentSize()[0]-220,height:this.win.getContentSize()[1]-157},this.win.getContentSize()));}this.emit();if(!view.webContents.getURL())view.webContents.loadURL(WEBSITE_URL).catch(()=>{});return this.state();}
 hide(){if(this.visible){this.win.contentView.removeChildView(this.view);this.visible=false;}}
 bounds(value){if(this.visible)this.view.setBounds(boundedRect(value,this.win.getContentSize()));}
 async command(value){if(!this.visible||!this.view)throw new Error('website_missing');const wc=this.view.webContents;
  if(value==='reload')wc.reload();else if(value==='home')await wc.loadURL(WEBSITE_URL);else if(value==='back'){if(wc.navigationHistory.canGoBack())wc.navigationHistory.goBack();}else throw new Error('command_invalid');return true;
 }
 close(){this.hide();if(this.view&&!this.view.webContents.isDestroyed()){this.view.webContents.session.flushStorageData();this.view.webContents.close();}this.view=null;}
}
module.exports={WebsitePane,WEBSITE_URL,WEBSITE_PARTITION,allowedWebsiteURL,boundedRect};
