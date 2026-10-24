'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const script=fs.readFileSync(process.env.STUDIO_ADAPTER_UNDER_TEST||path.join(__dirname,'../backend/web/browser-adapter.js'),'utf8');
const uiPath=process.env.STUDIO_CONTENT_UI_UNDER_TEST||path.join(__dirname,'../web/content-ui.js');
function fixture(enabled=true){
 const elements=new Map(),requests=[],projectStates=[],videoStates=[],articleStates=[],reviewStates=[],contentStates=[];
 class Element{constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={};}append(...xs){for(const x of xs){this.children.push(x);x.parent=this;}}replaceChildren(...xs){this.children=[];this.append(...xs);}setAttribute(k,v){this[k]=v;}remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}}
 const mount=new Element('div');elements.set('content-project-mount',mount);const videoMount=new Element('div');elements.set('content-video-script-mount',videoMount);const articleMount=new Element('div');elements.set('content-article-script-mount',articleMount);const reviewMount=new Element('div');elements.set('data-review-mount',reviewMount);
 const document={body:{classList:{contains:()=>false}},head:new Element('head'),documentElement:{dataset:{cyberFeishuEmbed:enabled?'enabled':''}},readyState:'complete',createElement:t=>new Element(t),getElementById:id=>elements.get(id)};
 const source={phase:'cached',stale:true,topics:[{id:'topic-one',direction:'fixture one'},{id:'topic-two',direction:'fixture two'}],projects:[{id:'project-one'}],jobs:[],forms:[],accounts:[],platforms:{}};
 let generation=0;
 const window={open(){},location:{assign(){}},StudioContentProjectController:require('../web/content-project-controller.js'),StudioAnalyticsTransport:require('../web/analytics-transport.js')};
 vm.runInNewContext(script,{document,window,MutationObserver:class{observe(){}},crypto:{randomUUID:()=> 'fixture'},URL,URLSearchParams,navigator:{},fetch:async url=>{requests.push(url);return {ok:true,headers:{get:()=>''},json:async()=>url.startsWith('/v1/workspace/snapshot?')?{...structuredClone(source),phase:url.includes('refresh=1')?'ok':'cached',stale:!url.includes('refresh=1'),generation:++generation}:{content:'https://tenant.feishu.cn/base/FixtureContent',videoScripts:'https://tenant.feishu.cn/drive/folder/VideoScripts',articleScripts:'https://tenant.feishu.cn/drive/folder/ArticleScripts',reviews:'https://tenant.feishu.cn/base/FixtureReview?table=tblReview'}};}});
 window.studio.content.onState(value=>contentStates.push(value));window.studio.content.project.onState(value=>projectStates.push(value));
 window.studio.content.videoScripts.onState(value=>videoStates.push(value));window.studio.content.articleScripts.onState(value=>articleStates.push(value));window.studio.dataReview.onState(value=>reviewStates.push(value));
 return {window,source,mount,videoMount,articleMount,reviewMount,requests,projectStates,videoStates,articleStates,reviewStates,contentStates};
}
test('project iframe state cannot replace the asynchronously refreshed content snapshot',async()=>{
 const f=fixture();let data=await f.window.studio.content.open();f.window.studio.content.onState(value=>{data=value;});
 assert.equal(data.generation,1);await new Promise(r=>setImmediate(r));assert.equal(data.generation,2);const before=data;await f.window.studio.content.project.open();
 assert.equal(f.projectStates.length,1);assert.equal(f.projectStates[0].phase,'ready');
 assert.equal(f.contentStates.length,1,'only the live read may broadcast a business snapshot');
 assert.equal(data,before);assert.equal(data.topics.filter(Boolean).length,2);assert.equal(data.projects.length,1);
 assert.equal(f.mount.children[0].tag,'iframe');
});
test('entry refreshes once in background and the explicit refresh waits for another live read',async()=>{
 const f=fixture();let data=await f.window.studio.content.open();f.window.studio.content.onState(value=>{data=value;});
 await new Promise(r=>setImmediate(r));assert.equal(data.generation,2);assert.equal(data.stale,false);
 for(let i=0;i<3;i++){await f.window.studio.content.project.open();await f.window.studio.content.project.hide();assert.equal(data.topics.length,2);assert.equal(data.projects.length,1);assert.equal(f.mount.children.length,0);}
 const value=await f.window.studio.content.read();assert.equal(value.generation,3);assert.equal(value.topics.length,2);assert.equal(f.contentStates.length,2);assert.equal(f.projectStates.length,3);
 const reads=f.requests.filter(x=>x.startsWith('/v1/workspace/snapshot?kind=content'));assert.equal(reads.length,3);assert.equal(reads.filter(x=>x.includes('refresh=1')).length,2);
});
test('concurrent manual refresh clicks share one live request',async()=>{
 const f=fixture();await f.window.studio.content.open();await new Promise(r=>setImmediate(r));const before=f.requests.length;
 const [a,b]=await Promise.all([f.window.studio.content.read(),f.window.studio.content.read()]);assert.equal(a.generation,b.generation);assert.equal(f.requests.length,before+1);assert.ok(f.requests.at(-1).includes('refresh=1'));
});
test('blocked iframe reports only project state and unsubscribe retains content separation',async()=>{
 const f=fixture(false);const data=await f.window.studio.content.open();await new Promise(r=>setImmediate(r));const contentCount=f.contentStates.length;let calls=0;const off=f.window.studio.content.project.onState(()=>calls++);
 assert.equal(await f.window.studio.content.project.open(),false);assert.equal(f.projectStates[0].phase,'blocked');assert.equal(f.contentStates.length,contentCount);assert.equal(data.topics.length,2);assert.equal(calls,1);
 off();await f.window.studio.content.project.open();assert.equal(calls,1);assert.equal(f.contentStates.length,contentCount);assert.equal(f.mount.children.some(x=>x.tag==='iframe'),false);
});

test('script columns open the two configured folder URLs without touching the content snapshot',async()=>{
 const f=fixture();await f.window.studio.content.videoScripts.open();await f.window.studio.content.articleScripts.open();
 assert.equal(f.videoMount.children[0].src,'https://tenant.feishu.cn/drive/folder/VideoScripts');assert.equal(f.articleMount.children[0].src,'https://tenant.feishu.cn/drive/folder/ArticleScripts');assert.equal(f.videoMount.children[0].title,'飞书视频脚本');assert.equal(f.articleMount.children[0].title,'飞书文章脚本');assert.equal(f.contentStates.length,0);assert.equal(f.requests.some(x=>x.includes('snapshot')),false);
});
test('side data review embeds the distinct Base without reading content or emitting project state',async()=>{
 const f=fixture();await f.window.studio.dataReview.open();
 assert.equal(f.reviewMount.children[0].tag,'iframe');assert.equal(f.reviewMount.children[0].src,'https://tenant.feishu.cn/base/FixtureReview?table=tblReview');assert.equal(f.reviewMount.children[0].title,'飞书内容数据复盘');
 assert.equal(f.reviewStates[0].phase,'ready');assert.equal(f.projectStates.length,0);assert.equal(f.contentStates.length,0);
 assert.equal(f.requests.some(x=>x.includes('snapshot')),false);
 await f.window.studio.dataReview.command('reload');assert.equal(f.reviewMount.children.length,1);
 await f.window.studio.dataReview.hide();assert.equal(f.reviewMount.children.length,0);
 await f.window.studio.content.project.open();assert.equal(f.mount.children[0].src,'https://tenant.feishu.cn/base/FixtureContent');
 await f.window.studio.content.project.hide();await f.window.studio.dataReview.open();assert.equal(f.reviewMount.children.length,1);
});
test('review missing bridge remains explicit and its listener is isolated and removable',async()=>{
 const f=fixture(false);let count=0;const off=f.window.studio.dataReview.onState(()=>count++);
 assert.equal(await f.window.studio.dataReview.open(),false);assert.equal(f.reviewStates[0].phase,'blocked');
 assert.equal(f.reviewMount.children.some(x=>x.tag==='iframe'),false);assert.equal(count,1);off();
 await f.window.studio.dataReview.open();assert.equal(count,1);assert.equal(f.contentStates.length,0);
});
test('review renderer guards all navigation and refresh paths, with a native fallback',()=>{
 const ui=fs.readFileSync(uiPath,'utf8');
 for(const fragment of ["reviewAPI=window.studio.dataReview","window.openStudioDataReview","data-review-workbench","data-nav=\"data\"","nativeReviews?.hide()","nativeReviews?.command('reload')"])assert.ok(ui.includes(fragment),fragment);
});
test('content navigation defaults to production and contains only project, script folders and production',()=>{
 const ui=fs.readFileSync(uiPath,'utf8');for(const label of ['内容项目','视频脚本','文章脚本','制作'])assert.ok(ui.includes('>'+label+'</button>'));assert.ok(ui.includes("view='production'"));assert.ok(ui.includes("view='production';status='all'"));assert.ok(!ui.includes('>选题池</button>'));assert.ok(!ui.includes('data-content-tab="reviews"'));
});
test('production exposes a labeled live Feishu refresh control',()=>{
 const ui=fs.readFileSync(uiPath,'utf8');assert.ok(ui.includes('刷新飞书数据'));assert.ok(ui.includes('实时读取飞书原表'));assert.ok(ui.includes("C('content-refresh').onclick"));
});
