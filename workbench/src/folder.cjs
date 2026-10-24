'use strict';
const {TaskPane,allowedDocumentURL}=require('./tasks.cjs');
function folderURL(value){
 try{const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password||(u.port&&u.port!=='443')||!/^[-a-z0-9]+\.feishu\.cn$/.test(u.hostname)||!/^\/drive\/folder\/[A-Za-z0-9_]+\/?$/.test(u.pathname)||u.search||u.hash)throw 0;return u.href;}catch{throw new Error('folder_url_invalid');}
}
const allowedFolderURL=(value,target)=>allowedDocumentURL(value,target,folderURL);
class FolderPane extends TaskPane{
 constructor(options){super({...options,validateURL:folderURL,allowedURL:allowedFolderURL,title:'活动资料库',loadingMessage:'正在打开飞书活动资料库…',configMessage:options.config?.error?'活动资料库地址配置有误，请检查 bar-space.json':'活动资料库地址尚未配置'});}
}
module.exports={FolderPane,folderURL,allowedFolderURL};
