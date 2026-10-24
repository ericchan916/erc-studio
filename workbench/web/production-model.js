'use strict';
(function(root){
 const text=value=>typeof value==='string'?value:'';
 function candidates(snapshot){return (Array.isArray(snapshot?.projects)?snapshot.projects:[]).filter(p=>/^rec[A-Za-z0-9]{8,48}$/.test(p.id)&&text(p.title).trim()&&(/视频/.test(text(p.form))||!p.form&&/视频|口播/.test(text(p.videoType)))).map(p=>({id:p.id,title:p.title,accounts:[...new Set((Array.isArray(p.accountNames)&&p.accountNames.length?p.accountNames:[p.account]).filter(x=>typeof x==='string'&&x.trim()))],stage:text(p.stage)||'未登记',requirements:text(p.requirements)}));}
 function filter(items,account,search){const q=text(search).trim().toLocaleLowerCase();return items.filter(p=>(account==='all'||p.accounts.includes(account))&&(!q||p.title.toLocaleLowerCase().includes(q)));}
 function validVideo(file){return !!file&&typeof file.name==='string'&&Number.isSafeInteger(file.size)&&file.size>0&&(/^video\//.test(file.type)||!file.type&&/\.(mp4|mov|m4v|webm)$/i.test(file.name));}
 function filename(name){return text(name).replace(/[\\/\x00-\x1f]/g,'_').slice(0,180)||'成片.mp4';}
 function duration(seconds){if(!Number.isFinite(seconds)||seconds<0)return '时长待读取';return Math.floor(seconds/60).toString().padStart(2,'0')+':'+Math.floor(seconds%60).toString().padStart(2,'0');}
 class Session{
  constructor(){this.scope=null;this.items=[];this.selected=null;this.entries=new Map();this.account='all';this.search='';this.visible=false;this.counter=0;}
  update(snapshot){const scope=text(snapshot?.viewKey)||'unverified',reset=scope!==this.scope||scope==='unverified';const released=[];if(reset){for(const e of this.entries.values())released.push(...this.urls(e));this.entries.clear();this.selected=null;this.scope=scope;this.account='all';this.search='';}this.items=candidates(snapshot);const accounts=this.accounts();if(this.account!=='all'&&!accounts.includes(this.account))this.account='all';if(!this.items.some(p=>p.id===this.selected))this.selected=null;return released;}
  accounts(){return [...new Set(this.items.flatMap(p=>p.accounts))].sort((a,b)=>a.localeCompare(b,'zh-CN'));}
  filtered(){return filter(this.items,this.account,this.search);}
  choose(id){if(!this.items.some(p=>p.id===id))throw Error('视频候选已变化，请刷新后选择');this.selected=id;return this.entry();}
  project(){return this.items.find(p=>p.id===this.selected)||null;}
  entry(){if(!this.project())return null;if(!this.entries.has(this.selected))this.entries.set(this.selected,{source:null,requirement:'',versions:[],active:null});return this.entries.get(this.selected);}
  urls(e){return [e.source?.url,...e.versions.map(v=>v.url)].filter(Boolean);}
  setSource(file,url){if(!validVideo(file)||!text(url).startsWith('blob:'))throw Error('请选择非空视频文件');const e=this.entry();if(!e)throw Error('请先选择视频');const old=e.source?.url;e.source={file,url};return old;}
  addVersion(file,url){if(!validVideo(file)||!text(url).startsWith('blob:'))throw Error('请选择非空成片文件');const e=this.entry();if(!e)throw Error('请先选择视频');if(e.versions.length>=20)throw Error('本页最多临时预览20个成片');const v={id:String(++this.counter),file,url,origin:'本人导入'};e.versions.push(v);e.active=v.id;return v;}
  addGenerated(job,select=true){if(!job||!/^\w{8}-[\w-]{27,40}$/.test(job.id)||job.state!=='succeeded')throw Error('成片任务尚未核验完成');const e=this.entry();if(!e)throw Error('请先选择视频');if(job.projectId&&job.projectId!==this.selected)throw Error('成片不属于当前视频');let v=e.versions.find(v=>v.jobId===job.id);if(!v){v={id:'job:'+job.id,jobId:job.id,file:{name:job.sourceKind==='linked_local_result'?filename(job.outputFileName):'成片-'+(e.versions.length+1)+'.mp4'},url:'/v1/production/jobs/'+job.id+'/video',sourceKind:job.sourceKind,origin:job.sourceKind==='linked_local_result'?'本机成片 · 持久关联':job.workflowVersion==='video-use-codex-cli-v1'?'本机Codex CLI＋video-use':'本机Codex＋Remotion',workflowVersion:job.workflowVersion,qualityStatus:job.qualityStatus||'needs_review',noBgmURL:(job.variants||[]).includes('nobgm')?'/v1/production/jobs/'+job.id+'/video-nobgm':null};e.versions.push(v);}else{v.qualityStatus=job.qualityStatus||v.qualityStatus;}if(select)e.active=v.id;return v;}
  preview(){const e=this.entry();if(!e)return null;const v=e.versions.find(v=>v.id===e.active);return v?{...v,kind:'finished'}:e.source?{...e.source,kind:'source'}:null;}
  requestText(){const p=this.project(),e=this.entry();if(!p||!e?.requirement.trim())throw Error('请选择视频并填写剪辑需求');return ['内容项目：'+p.title,'所属账号：'+p.accounts.join('、'),'飞书记录：'+p.id,'原始素材：'+(e.source?.file.name||'尚未选择'),'剪辑需求：',e.requirement].join('\n');}
  clear(){const urls=[];for(const e of this.entries.values())urls.push(...this.urls(e));this.entries.clear();this.selected=null;return urls;}
 }
 const api={candidates,filter,validVideo,filename,duration,Session};if(typeof module==='object'&&module.exports)module.exports=api;else root.StudioProductionModel=api;
})(typeof globalThis==='object'?globalThis:this);
