'use strict';
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.StudioContentProjectController=api;})(typeof window==='object'?window:globalThis,()=>{
 function create({api,desired,mount,onState,onError}){let requested=false,epoch=0,inflight=null;
  const resize=()=>{if(!requested||!desired())return;const r=mount();return api.bounds({x:r.x,y:r.y,width:r.width,height:r.height});};
  const hide=()=>{if(!requested)return;requested=false;epoch++;return api.hide();};
  const show=()=>{if(!desired())return hide();if(requested){resize();return inflight;}requested=true;const intent=++epoch;
   inflight=Promise.resolve().then(()=>api.open()).then(state=>{if(intent===epoch&&desired()){onState(state);return resize();}if(!desired())return api.hide();}).catch(error=>{if(intent===epoch&&desired()){requested=false;onError(error);}}).finally(()=>{if(intent===epoch)inflight=null;});return inflight;
  };
  api.onState(state=>{if(requested&&desired())onState(state);});
  const command=value=>{if(!requested||!desired())return;return api.command(value);};
  return {show,hide,resize,command};
 }
 return {create};
});
