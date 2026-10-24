'use strict';
const fs=require('node:fs'),path=require('node:path');
const {boundedRect}=require('./website.cjs');
const TASK_PARTITION='persist:studio-feishu-tasks';
const AUTH_HOSTS=new Set(['login.feishu.cn','accounts.feishu.cn','passport.feishu.cn','open.feishu.cn','applink.feishu.cn','www.feishu.cn','feishu.cn']);
function taskURL(value){
 try{const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password||(u.port&&u.port!=='443')||!/^[-a-z0-9]+\.feishu\.cn$/.test(u.hostname)||!/^\/base\/[A-Za-z0-9]+\/?$/.test(u.pathname))throw 0;return u.href;}catch{throw new Error('task_url_invalid');}
}
function allowedTaskURL(value,target){
 return allowedDocumentURL(value,target,taskURL);
}
function allowedDocumentURL(value,target,validate){
 try{const u=new URL(value),tenant=new URL(validate(target)).hostname;return u.protocol==='https:'&&!u.username&&!u.password&&(!u.port||u.port==='443')&&(u.hostname===tenant||AUTH_HOSTS.has(u.hostname));}catch{return false;}
}
function readTaskConfig(root){
 try{const config=JSON.parse(fs.readFileSync(path.join(root,'task-space.json'),'utf8'));if(config.version!==1)throw 0;return {url:taskURL(config.url)};}catch(error){if(error.code==='ENOENT')return null;throw new Error('task_config_invalid');}
}
class TaskPane{
 constructor({win,WebContentsView,session,config,onEvent,validateURL=taskURL,allowedURL=allowedTaskURL,title='任务',loadingMessage='正在打开飞书公司任务总栏…',configMessage}){Object.assign(this,{win,WebContentsView,session,onEvent,allowedURL,title,loadingMessage});this.target=config?.url?validateURL(config.url):null;this.view=null;this.visible=false;this.popups=new Set();this.phase=this.target?'idle':'unconfigured';this.message=this.target?'':configMessage||(config?.error?'本机任务地址配置有误，请检查 task-space.json':'本机任务地址尚未配置');}
 state(){const wc=this.view?.webContents;let hostname='';try{hostname=wc&&!wc.isDestroyed()?new URL(wc.getURL()||this.target).hostname:'';}catch{}return {phase:this.phase,message:this.message,hostname,configured:!!this.target,canGoBack:!!wc&&!wc.isDestroyed()&&wc.navigationHistory.canGoBack()};}
 emit(){if(this.visible)this.onEvent(this.state());}
 guard(wc){
  const blocked=url=>{let host='未知地址';try{const u=new URL(url);host=u.protocol==='https:'?u.hostname:u.protocol;}catch{}this.phase='blocked';this.message=`飞书跳转待核验（${host}），原页面已保留`;this.emit();};
  for(const name of ['will-navigate','will-redirect'])wc.on(name,(event,url,_inPlace,isMainFrame)=>{if(isMainFrame===false)return;if(!this.allowedURL(url,this.target)){event.preventDefault();blocked(url);}});
  wc.setWindowOpenHandler(({url})=>this.allowedURL(url,this.target)?{action:'allow',overrideBrowserWindowOptions:{parent:this.win,width:1100,height:800,webPreferences:{session:wc.session,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,allowRunningInsecureContent:false}}}:{action:'deny'});
  wc.on('did-create-window',child=>{this.popups.add(child);this.guard(child.webContents);child.on('closed',()=>this.popups.delete(child));});
 }
 create(){
  const ses=this.session.fromPartition(TASK_PARTITION,{cache:true});
  // Native keyboard copy/paste and file inputs remain available. Never grant clipboard reads, camera or microphone automatically.
  ses.setPermissionRequestHandler((wc,permission,callback)=>callback(permission==='clipboard-sanitized-write'&&!!wc&&this.allowedURL(wc.getURL(),this.target)));
  ses.setPermissionCheckHandler((_wc,permission,origin)=>permission==='clipboard-sanitized-write'&&this.allowedURL(origin,this.target));
  const view=new this.WebContentsView({webPreferences:{session:ses,nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,allowRunningInsecureContent:false}}),wc=view.webContents;
  wc.setUserAgent(wc.getUserAgent().replace(/\sElectron\/\S+/g,'').replace(/\scontent-company-workbench\/\S+/g,''));this.guard(wc);
  wc.on('did-start-loading',()=>{this.phase='loading';this.message=this.loadingMessage;this.emit();});
  wc.on('did-finish-load',()=>{this.phase='ready';this.message='飞书页面已加载，身份与权限以原页面为准';this.emit();});
  wc.on('did-stop-loading',()=>{if(this.phase==='loading'&&this.allowedURL(wc.getURL(),this.target)){this.phase='ready';this.message='飞书页面已加载，身份与权限以原页面为准';this.emit();}});
  wc.on('did-navigate-in-page',()=>this.emit());
  wc.on('did-fail-load',(_event,code,_description,_url,isMainFrame)=>{if(code===-3||isMainFrame===false)return;this.phase='error';this.message=`飞书页面加载失败（${code}），登录空间已保留，可重试`;this.emit();});
  wc.on('render-process-gone',()=>{this.phase='error';this.message='飞书页面已退出，登录空间已保留，可重试';this.emit();});
  this.view=view;return view;
 }
 open(){
  this.visible=true;this.win.setTitle?.('ERC Studio · '+this.title);
  if(!this.target){this.emit();return this.state();}
  const view=this.view||this.create();if(!this.win.contentView.children.includes(view))this.win.contentView.addChildView(view);
  view.setBounds(boundedRect({x:220,y:0,width:this.win.getContentSize()[0]-220,height:this.win.getContentSize()[1]},this.win.getContentSize()));
  this.emit();if(!view.webContents.getURL())view.webContents.loadURL(this.target).catch(()=>{});return this.state();
 }
 hide(){if(this.visible){if(this.view&&this.win.contentView.children.includes(this.view))this.win.contentView.removeChildView(this.view);this.visible=false;this.win.setTitle?.('ERC Studio · 账号管理');}}
 bounds(value){if(this.visible&&this.view)this.view.setBounds(boundedRect(value,this.win.getContentSize()));}
 async command(value){if(!['reload','home','back'].includes(value))throw new Error('command_invalid');if(!this.visible||!this.view)throw new Error('task_page_missing');const wc=this.view.webContents;if(value==='reload')wc.reload();else if(value==='home')await wc.loadURL(this.target);else if(wc.navigationHistory.canGoBack())wc.navigationHistory.goBack();return true;}
 close(){this.hide();for(const child of this.popups)if(!child.isDestroyed())child.close();this.popups.clear();if(this.view&&!this.view.webContents.isDestroyed()){this.view.webContents.session.flushStorageData();this.view.webContents.close();}this.view=null;}
}
module.exports={TaskPane,TASK_PARTITION,taskURL,allowedTaskURL,allowedDocumentURL,readTaskConfig};
