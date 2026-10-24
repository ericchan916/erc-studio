'use strict';
const {TaskPane,taskURL}=require('../tasks.cjs');
const {boundedRect}=require('../website.cjs');
function projectURL(config){
 if(!config||!/^tbl[A-Za-z0-9]+$/.test(config.tables?.projects||''))throw Error('content_project_target_invalid');
 const u=new URL(taskURL(config.baseURL));if(config.baseToken&&u.pathname.replace(/\/$/,'')!=='/base/'+config.baseToken)throw Error('content_project_target_invalid');u.search='';u.hash='';u.searchParams.set('table',config.tables.projects);return u.href;
}
class ProjectPane extends TaskPane{
 constructor(options){let config=null,error=false;try{if(options.config)config={url:projectURL(options.config)};}catch{error=true;}
  super({...options,config,title:'内容项目',loadingMessage:'正在打开飞书自媒体视频任务…',configMessage:error?'内容项目原表地址配置有误，原配置已保留':'内容项目原表地址尚未绑定'});
 }
 open(){const state=super.open();if(this.view)this.view.setBounds(boundedRect({x:220,y:70,width:this.win.getContentSize()[0]-220,height:this.win.getContentSize()[1]-70},this.win.getContentSize()));return state;}
}
module.exports={ProjectPane,projectURL};
