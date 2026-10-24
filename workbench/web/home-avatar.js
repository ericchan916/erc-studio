'use strict';
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.StudioHomeAvatar=api;})(typeof window!=='undefined'?window:globalThis,()=>{
 function sphereMesh(lat=20,lon=32){
  if(!Number.isInteger(lat)||!Number.isInteger(lon)||lat<3||lon<3||lat>64||lon>96)throw new Error('mesh_segments_invalid');
  const vertices=[],indices=[];
  for(let y=0;y<=lat;y++)for(let x=0;x<=lon;x++){const a=y*Math.PI/lat,b=x*2*Math.PI/lon,p=[Math.sin(a)*Math.cos(b),Math.cos(a),Math.sin(a)*Math.sin(b)];vertices.push(...p,...p);}
  for(let y=0;y<lat;y++)for(let x=0;x<lon;x++){const a=y*(lon+1)+x,b=a+lon+1;indices.push(a,b,a+1,b,b+1,a+1);}
  return {vertices:new Float32Array(vertices),indices:new Uint16Array(indices)};
 }
 function projection(aspect){if(!Number.isFinite(aspect)||aspect<=0)throw new Error('aspect_invalid');const f=1/Math.tan(38*Math.PI/360),near=.1,far=30;return new Float32Array([f/aspect,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);}
 function parts(){
  const result=[],skin=[.94,.70,.52],hair=[.16,.13,.18],purple=[.30,.17,.68],ink=[.08,.08,.13],white=[.98,.97,.94];
  const add=(position,scale,color)=>result.push({position,scale,color});
  add([0,.03,0],[.68,.035,.43],[.88,.86,.94]);
  add([-.18,.26,.07],[.14,.25,.14],ink);add([.18,.26,.07],[.14,.25,.14],ink);
  add([-.18,.10,.17],[.20,.10,.27],[.24,.22,.31]);add([.18,.10,.17],[.20,.10,.27],[.24,.22,.31]);
  add([0,.96,0],[.42,.52,.32],purple);add([0,1.33,-.12],[.45,.20,.34],[.38,.23,.80]);
  add([-.46,1.05,0],[.14,.38,.15],purple);add([.46,1.05,0],[.14,.38,.15],purple);
  add([-.47,.74,.03],[.12,.14,.12],skin);add([.47,.74,.03],[.12,.14,.12],skin);
  add([0,1.53,0],[.18,.23,.18],skin);add([0,2.13,0],[.55,.62,.47],skin);
  add([-.55,2.13,0],[.09,.15,.10],skin);add([.55,2.13,0],[.09,.15,.10],skin);
  add([0,2.47,-.08],[.59,.43,.48],hair);
  for(const [x,y,z,s] of [[-.37,2.47,.22,.21],[-.17,2.59,.32,.22],[.06,2.54,.35,.22],[.31,2.45,.24,.24]])add([x,y,z],[s,.22,.18],hair);
  for(const x of [-.19,.19]){add([x,2.15,.444],[.082,.112,.039],white);add([x+.009,2.14,.480],[.043,.070,.025],ink);add([x+.021,2.175,.501],[.016,.022,.012],white);add([x,2.33,.430],[.092,.024,.027],hair);}
  add([0,2.00,.486],[.063,.066,.065],skin);add([-.32,1.99,.375],[.085,.045,.025],[.92,.43,.44]);add([.32,1.99,.375],[.085,.045,.025],[.92,.43,.44]);add([0,1.86,.422],[.088,.016,.024],[.38,.17,.16]);
  add([-.12,1.17,.297],[.018,.12,.018],white);add([.12,1.17,.297],[.018,.12,.018],white);add([0,.86,.31],[.23,.12,.045],[.26,.13,.60]);
  return result;
 }
 const VERTEX='attribute vec3 aPosition;attribute vec3 aNormal;uniform mat4 uProjection;uniform vec3 uPosition;uniform vec3 uScale;uniform float uAngle;uniform float uBob;varying vec3 vNormal;varying vec3 vView;void main(){float c=cos(uAngle),s=sin(uAngle);vec3 p=aPosition*uScale+uPosition;vec3 world=vec3(c*p.x+s*p.z,p.y+uBob,-s*p.x+c*p.z);vec3 n=aNormal/uScale;vNormal=normalize(vec3(c*n.x+s*n.z,n.y,-s*n.x+c*n.z));vView=vec3(0.,1.43,5.0)-world;gl_Position=uProjection*vec4(world.x,world.y-1.43,world.z-5.0,1.);}';
 const FRAGMENT='precision mediump float;uniform vec3 uColor;varying vec3 vNormal;varying vec3 vView;void main(){vec3 n=normalize(vNormal),v=normalize(vView),l=normalize(vec3(-.7,1.,1.6));float light=.43+.52*max(dot(n,l),0.);float spec=pow(max(dot(reflect(-l,n),v),0.),30.)*.11;vec3 color=uColor*light+vec3(spec);gl_FragColor=vec4(pow(color,vec3(.87)),1.);}';
 function create(canvas,onStatus=()=>{}){
  const gl=canvas.getContext('webgl',{alpha:true,antialias:true,premultipliedAlpha:false});if(!gl){onStatus('三维暂未加载，可继续使用其他模块');return {setActive(){},setOrbit(){},destroy(){}};}
  let program,vertex,index,locations,active=false,orbit=true,lost=false,destroyed=false,frame=0,last=0,pointer=0,ready=false;
  const media=window.matchMedia('(prefers-reduced-motion: reduce)'),scene=parts(),mesh=sphereMesh();
  function resources(){
   const compile=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)){gl.deleteShader(s);throw new Error('avatar_shader_invalid');}return s;};
   const vs=compile(gl.VERTEX_SHADER,VERTEX),fs=compile(gl.FRAGMENT_SHADER,FRAGMENT);program=gl.createProgram();gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);gl.deleteShader(vs);gl.deleteShader(fs);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error('avatar_program_invalid');
   vertex=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,vertex);gl.bufferData(gl.ARRAY_BUFFER,mesh.vertices,gl.STATIC_DRAW);index=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,index);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,mesh.indices,gl.STATIC_DRAW);
   locations={};for(const name of ['Projection','Position','Scale','Angle','Bob','Color'])locations[name]=gl.getUniformLocation(program,'u'+name);
   for(const [name,offset] of [['aPosition',0],['aNormal',12]]){const a=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(a);gl.vertexAttribPointer(a,3,gl.FLOAT,false,24,offset);}
   gl.enable(gl.DEPTH_TEST);gl.clearColor(0,0,0,0);gl.useProgram(program);
  }
  function stop(){if(frame)cancelAnimationFrame(frame);frame=0;}
  function draw(time=0){
   frame=0;if(destroyed||lost||!active||document.hidden)return;
   if(!media.matches&&orbit&&time-last<32){frame=requestAnimationFrame(draw);return;}last=time;
   const ratio=Math.min(window.devicePixelRatio||1,1.5),w=Math.max(1,Math.round(canvas.clientWidth*ratio)),h=Math.max(1,Math.round(canvas.clientHeight*ratio));if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}
   gl.viewport(0,0,w,h);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.uniformMatrix4fv(locations.Projection,false,projection(w/h));gl.uniform1f(locations.Angle,pointer+(orbit&&!media.matches?Math.sin(time/3000)*.16:0));gl.uniform1f(locations.Bob,orbit&&!media.matches?Math.sin(time/1700)*.025:0);
   for(const p of scene){gl.uniform3fv(locations.Position,p.position);gl.uniform3fv(locations.Scale,p.scale);gl.uniform3fv(locations.Color,p.color);gl.drawElements(gl.TRIANGLES,mesh.indices.length,gl.UNSIGNED_SHORT,0);}
   if(!ready){if(gl.getError()!==gl.NO_ERROR){onStatus('三维加载异常，可继续使用其他模块');return;}ready=true;canvas.dataset.renderer='webgl';onStatus('WebGL 三维占位已加载');}
   if(orbit&&!media.matches)frame=requestAnimationFrame(draw);
  }
  function start(){stop();if(active&&!lost&&!destroyed&&!document.hidden)frame=requestAnimationFrame(draw);}
  const move=event=>{pointer=Math.max(-.38,Math.min(.38,(event.offsetX/Math.max(1,canvas.clientWidth)-.5)*.65));if(!orbit||media.matches)start();};
  const visibility=()=>document.hidden?stop():start(),change=()=>start();
  const contextLost=event=>{event.preventDefault();lost=true;ready=false;stop();onStatus('三维显示暂停，等待图形恢复');};
  const contextRestored=()=>{try{resources();lost=false;start();}catch{onStatus('三维恢复异常，可继续使用其他模块');}};
  try{resources();}catch{onStatus('三维加载异常，可继续使用其他模块');return {setActive(){},setOrbit(){},destroy(){}};}
  const resize=new ResizeObserver(start);resize.observe(canvas);canvas.addEventListener('pointermove',move);canvas.addEventListener('webglcontextlost',contextLost);canvas.addEventListener('webglcontextrestored',contextRestored);document.addEventListener('visibilitychange',visibility);media.addEventListener('change',change);
  return {setActive(value){active=!!value;start();},setOrbit(value){orbit=!!value;start();},destroy(){if(destroyed)return;destroyed=true;stop();resize.disconnect();canvas.removeEventListener('pointermove',move);canvas.removeEventListener('webglcontextlost',contextLost);canvas.removeEventListener('webglcontextrestored',contextRestored);document.removeEventListener('visibilitychange',visibility);media.removeEventListener('change',change);gl.deleteBuffer(vertex);gl.deleteBuffer(index);gl.deleteProgram(program);}};
 }
 return {sphereMesh,projection,parts,create};
});
