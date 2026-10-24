'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {sphereMesh,projection,parts,create}=require('../web/home-avatar.js');
test('placeholder contains actual indexed 3D geometry with normalized normals',()=>{
 const mesh=sphereMesh();assert.ok(mesh.vertices instanceof Float32Array);assert.ok(mesh.indices instanceof Uint16Array);assert.equal(mesh.vertices.length,21*33*6);assert.equal(mesh.indices.length,20*32*6);
 for(let i=0;i<mesh.vertices.length;i+=6){assert.ok(Math.abs(Math.hypot(...mesh.vertices.slice(i,i+3))-1)<1e-6);assert.deepEqual(mesh.vertices.slice(i,i+3),mesh.vertices.slice(i+3,i+6));}assert.ok(Math.max(...mesh.indices)<mesh.vertices.length/6);
 for(const n of [0,2,65,NaN])assert.throws(()=>sphereMesh(n,32),/mesh_segments_invalid/);
});
test('scene uses bounded local procedural parts, perspective depth, and no downloaded model',()=>{
 const scene=parts();assert.ok(scene.length>25);for(const p of scene){for(const v of [...p.position,...p.scale,...p.color])assert.ok(Number.isFinite(v));assert.ok(p.scale.every(x=>x>0));assert.ok(p.color.every(x=>x>=0&&x<=1));}assert.equal(projection(1.5)[11],-1);assert.ok(projection(1.5)[0]<projection(1.5)[5]);assert.throws(()=>projection(0),/aspect_invalid/);
});
test('GPU-unavailable placeholder is labelled honestly without blocking other modules',()=>{
 let message;const avatar=create({getContext:()=>null},s=>message=s);assert.match(message,/三维暂未加载/);avatar.setActive(true);avatar.setOrbit(false);avatar.destroy();
});
test('WebGL draw, pixel budget, hidden-page pause, reduced motion and cleanup are actual lifecycle behaviors',()=>{
 const originals={};for(const k of ['window','document','ResizeObserver','requestAnimationFrame','cancelAnimationFrame'])originals[k]=global[k];
 const queue=new Map(),events={},mediaEvents={},canvasEvents={},calls={draws:0,deleted:0};let id=0;
 const media={matches:false,addEventListener:(k,v)=>mediaEvents[k]=v,removeEventListener:k=>delete mediaEvents[k]};
 global.window={devicePixelRatio:2,matchMedia:()=>media};global.document={hidden:false,addEventListener:(k,v)=>events[k]=v,removeEventListener:k=>delete events[k]};global.requestAnimationFrame=cb=>{queue.set(++id,cb);return id;};global.cancelAnimationFrame=n=>queue.delete(n);global.ResizeObserver=class{observe(){}disconnect(){calls.disconnected=true;}};
 const gl={VERTEX_SHADER:1,FRAGMENT_SHADER:2,COMPILE_STATUS:3,LINK_STATUS:4,ARRAY_BUFFER:5,ELEMENT_ARRAY_BUFFER:6,STATIC_DRAW:7,FLOAT:8,DEPTH_TEST:9,COLOR_BUFFER_BIT:10,DEPTH_BUFFER_BIT:11,TRIANGLES:12,UNSIGNED_SHORT:13,NO_ERROR:0,createShader:()=>({}),shaderSource(){},compileShader(){},getShaderParameter:()=>true,deleteShader(){},createProgram:()=>({}),attachShader(){},linkProgram(){},getProgramParameter:()=>true,createBuffer:()=>({}),bindBuffer(){},bufferData(){},getUniformLocation:(_,n)=>n,getAttribLocation:(_,n)=>n==='aNormal'?1:0,enableVertexAttribArray(){},vertexAttribPointer(){},enable(){},clearColor(){},useProgram(){},viewport(){},clear(){},uniformMatrix4fv(){},uniform1f(){},uniform3fv(){},drawElements(){calls.draws++;},getError:()=>0,deleteBuffer(){calls.deleted++;},deleteProgram(){calls.deleted++;}};
 const canvas={dataset:{},clientWidth:420,clientHeight:290,getContext:()=>gl,addEventListener:(k,v)=>canvasEvents[k]=v,removeEventListener:k=>delete canvasEvents[k]};let avatar;
 const tick=t=>{const entry=queue.entries().next().value;assert.ok(entry);queue.delete(entry[0]);entry[1](t);};
 try{let message;avatar=create(canvas,s=>message=s);assert.equal(queue.size,0);avatar.setActive(true);tick(64);assert.equal(calls.draws,parts().length);assert.equal(canvas.width,630);assert.equal(canvas.height,435);assert.equal(canvas.dataset.renderer,'webgl');assert.match(message,/已加载/);assert.equal(queue.size,1);
  avatar.setActive(false);assert.equal(queue.size,0);avatar.setActive(true);global.document.hidden=true;events.visibilitychange();assert.equal(queue.size,0);global.document.hidden=false;events.visibilitychange();media.matches=true;mediaEvents.change();tick(128);assert.equal(queue.size,0);
  media.matches=false;avatar.setOrbit(false);tick(192);assert.equal(queue.size,0);avatar.destroy();assert.equal(calls.deleted,3);assert.ok(calls.disconnected);assert.deepEqual(Object.keys(canvasEvents),[]);
 }finally{avatar?.destroy();for(const [k,v] of Object.entries(originals)){if(v===undefined)delete global[k];else global[k]=v;}}
});
test('home integration uses real member API and keeps renderer offline without fabricated identity',()=>{
 const root=path.resolve(__dirname,'..'),html=fs.readFileSync(path.join(root,'web/index.html'),'utf8'),ui=fs.readFileSync(path.join(root,'web/home-ui.js'),'utf8'),main=fs.readFileSync(path.join(root,'src/main.cjs'),'utf8'),preload=fs.readFileSync(path.join(root,'src/preload.cjs'),'utf8');
 assert.ok(html.includes('id="home-send" class="home-send" disabled'));assert.ok(html.includes('成员未登录'));assert.ok(html.includes('业务工具与代码执行未开放'));assert.ok(html.includes('home-chat.js'));assert.ok(preload.includes("ipcRenderer.invoke('studio:conversation'"));assert.ok(html.includes('id="home-avatar"'));assert.ok(main.includes("bind('studio:home-open'"));assert.ok(preload.includes("ipcRenderer.invoke('studio:home-open')"));assert.ok(!/localStorage|fetch\(|XMLHttpRequest|user_access_token|tenant_access_token/.test(ui));assert.ok(ui.includes('setActive(false)'));assert.ok(ui.includes('window.openStudioHome();'));
});
test('home navigation preserves the existing account, task and website view handoffs',()=>{
 const root=path.resolve(__dirname,'..'),app=fs.readFileSync(path.join(root,'web/app.js'),'utf8');assert.ok(app.includes("if(name==='home')"));assert.ok(app.includes("[['home','主页'],['tasks','任务'],['content','内容']")&&app.includes("['radar','选题'],['account','账号']"));assert.ok(app.includes('window.hideStudioHome?.();window.hideStudioTasks?.();window.hideStudioWebsite?.();'));
 for(const name of ['task-ui.js','website-ui.js'])assert.ok(fs.readFileSync(path.join(root,'web',name),'utf8').includes('window.hideStudioHome?.()'));
});
