'use strict';
// Deterministic, content-preserving Graphite Minimal export; local markdown-it 15.0.2.
(function(root){
 const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const leaf=value=>'<span leaf="">'+escape(value)+'</span>';
 const FONT="-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif";
 const STYLES={paragraph:'margin:0 0 22px;font-size:15px;line-height:1.8;color:#52525B;letter-spacing:0.3px;overflow-wrap:anywhere;',heading:'margin:32px 0 20px;font-size:20px;font-weight:700;color:#27272A;line-height:1.5;letter-spacing:0.3px;',quote:'margin:24px 0;padding:18px 20px;border-left:2px solid #52525B;background:#FAFAFA;',code:"margin:0;font-family:'SF Mono',Consolas,Monaco,monospace;font-size:13px;line-height:1.6;color:#27272A;overflow-wrap:anywhere;",image:'max-width:100%;height:auto;display:block;margin:0 auto;'};
 // Preview uses only self-hosted CSS under the unchanged style-src 'self'.
 // Clipboard/export retain the identical fixed inline declarations WeChat needs.
 const PREVIEW_STYLES={...STYLES,
  root:"max-width:677px;margin:0 auto;padding:0 10px;background:#FFFFFF;font-family:"+FONT+";color:#52525B;line-height:1.8;letter-spacing:0.3px;",
  strong:'font-weight:600;color:#27272A;border-bottom:2px solid #52525B;',
  list:'margin:0 0 22px;padding-left:24px;',
  item:'margin:0 0 8px;font-size:15px;line-height:1.8;',
  link:'color:#52525B;text-decoration:underline;',
  imageWrap:'margin:0 0 22px;',
  caption:'margin:8px 0 0;text-align:center;font-size:12px;color:#71717A;',
  inlineCode:'background:#F4F4F5;color:#52525B;padding:1px 4px;font-family:Consolas,Monaco,monospace;font-size:14px;',
  codeWrap:'margin:0 0 22px;padding:12px 14px;background:#F4F4F5;border-left:2px solid #52525B;',
  rule:'height:1px;background:#E4E4E7;margin:32px 0;',
  table:'width:100%;border-collapse:collapse;font-size:13px;line-height:1.8;margin:0 0 22px;table-layout:fixed;',
  headerCell:'padding:8px;border:1px solid #E4E4E7;text-align:left;background:#FAFAFA;overflow-wrap:anywhere;',
  cell:'padding:8px;border:1px solid #E4E4E7;overflow-wrap:anywhere;'
 };
 const previewClasses=new Map(Object.entries(PREVIEW_STYLES).map(([name,style])=>[style,'radar-article-'+name]));
 function previewMarkup(html){return html.replace(/ style="([^"]*)"/g,(_all,style)=>{const name=previewClasses.get(style);if(!name)throw Error('article_preview_style_not_registered');return ' class="'+name+'"';});}
 function previewStylesheet(){return Object.entries(PREVIEW_STYLES).map(([name,style])=>'.radar-article-body .radar-article-'+name+'{'+style+'}').join('\n');}
 function copyFallback(document,text,html){
  const temp=document.createElement('textarea');temp.className='radar-copy-buffer';temp.value=text;
  let written=false;
  const onCopy=event=>{if(!event.clipboardData)return;event.clipboardData.setData('text/plain',text);if(html)event.clipboardData.setData('text/html',html);event.preventDefault();written=true;};
  document.body.append(temp);document.addEventListener('copy',onCopy);let accepted=false;
  try{temp.select();accepted=document.execCommand('copy');}
  finally{document.removeEventListener('copy',onCopy);temp.remove();}
  if(!accepted||!written)throw Error('clipboard_failed');
 }
 function safeURL(value){try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:'';}catch{return '';}}
 function create(factory){
  const md=factory({html:false,linkify:false,typographer:false,breaks:false});
  // CJK writers commonly close bold before a Chinese continuation. Default
  // CommonMark punctuation flanking leaves **重点。**而它 as literal markers.
  // Parse the explicit delimiters, not a text rewrite: escapes/code stay intact.
  md.inline.ruler.before('emphasis','radar_cjk_strong',(state,silent)=>{
   const start=state.pos;if(state.src.slice(start,start+2)!=='**'||/\s/.test(state.src[start+2]||''))return false;
   const end=state.src.indexOf('**',start+2);if(end<0||end>=state.posMax||end===start+2||/\s/.test(state.src[end-1])||!/^\p{Script=Han}/u.test(state.src.slice(end+2)))return false;
   let escapes=0;for(let p=end-1;p>=start+2&&state.src[p]==='\\';p--)escapes++;if(escapes%2)return false;
   const content=state.src.slice(start+2,end);if(content.includes('\n')||state.level>=state.md.options.maxNesting)return false;
   if(!silent){state.push('strong_open','strong',1);const children=[];state.md.inline.parse(content,state.md,state.env,children);for(const token of children){token.level+=state.level;state.tokens.push(token);}state.push('strong_close','strong',-1);}
   state.pos=end+2;return true;
  });
  const r=md.renderer.rules;
  r.text=(t,i)=>leaf(t[i].content);
  r.softbreak=()=>'<br>';r.hardbreak=()=>'<br>';
  r.paragraph_open=()=>'<p style="'+STYLES.paragraph+'">';r.paragraph_close=()=>'</p>';
  r.heading_open=(t,i)=>'<h3 style="'+STYLES.heading+'">';r.heading_close=()=>'</h3>';
  r.strong_open=()=>'<strong style="font-weight:600;color:#27272A;border-bottom:2px solid #52525B;">';r.strong_close=()=>'</strong>';
  r.em_open=()=>'<em>';r.em_close=()=>'</em>';r.s_open=()=>'<s>';r.s_close=()=>'</s>';
  r.blockquote_open=()=>'<section style="'+STYLES.quote+'">';r.blockquote_close=()=>'</section>';
  r.bullet_list_open=()=>'<ul style="margin:0 0 22px;padding-left:24px;">';r.bullet_list_close=()=>'</ul>';
  r.ordered_list_open=(t,i)=>'<ol start="'+Number(t[i].attrGet('start')||1)+'" style="margin:0 0 22px;padding-left:24px;">';r.ordered_list_close=()=>'</ol>';
  r.list_item_open=()=>'<li style="margin:0 0 8px;font-size:15px;line-height:1.8;">';r.list_item_close=()=>'</li>';
  r.link_open=(t,i)=>{const href=safeURL(t[i].attrGet('href'));return href?'<a href="'+escape(href)+'" style="color:#52525B;text-decoration:underline;">':'<span>';};
  r.link_close=(t,i)=>{let depth=0;for(let j=i-1;j>=0;j--){if(t[j].type==='link_close')depth++;if(t[j].type==='link_open'){if(depth)depth--;else return safeURL(t[j].attrGet('href'))?'</a>':'</span>';}}return '</span>';};
  r.image=(t,i)=>{const token=t[i],url=safeURL(token.attrGet('src')),alt=token.content||'';return url?'<section style="margin:0 0 22px;">'+ '<span leaf=""><img src="'+escape(url)+'" alt="'+escape(alt)+'" style="'+STYLES.image+'"></span>'+ (alt?'<p style="margin:8px 0 0;text-align:center;font-size:12px;color:#71717A;">'+leaf(alt)+'</p>':'')+'</section>':leaf('[图片待补：'+(alt||token.attrGet('src')||'未提供地址')+']');};
  r.code_inline=(t,i)=>'<span style="background:#F4F4F5;color:#52525B;padding:1px 4px;font-family:Consolas,Monaco,monospace;font-size:14px;">'+leaf(t[i].content)+'</span>';
  const code=(t,i)=>'<section style="margin:0 0 22px;padding:12px 14px;background:#F4F4F5;border-left:2px solid #52525B;">'+t[i].content.replace(/\n$/,'').split('\n').map(line=>'<p style="'+STYLES.code+'">'+leaf(line.replace(/\t/g,'    ').replace(/ /g,'\u00a0')||'\u00a0')+'</p>').join('')+'</section>';
  r.fence=code;r.code_block=code;
  r.hr=()=>'<section style="height:1px;background:#E4E4E7;margin:32px 0;"><span leaf=""><br></span></section>';
  r.table_open=()=>'<table style="width:100%;border-collapse:collapse;font-size:13px;line-height:1.8;margin:0 0 22px;table-layout:fixed;">';r.table_close=()=>'</table>';
  r.th_open=()=>'<th style="padding:8px;border:1px solid #E4E4E7;text-align:left;background:#FAFAFA;overflow-wrap:anywhere;">';r.th_close=()=>'</th>';
  r.td_open=()=>'<td style="padding:8px;border:1px solid #E4E4E7;overflow-wrap:anywhere;">';r.td_close=()=>'</td>';
  return {
   render(value){
    const source=String(value||'').replace(/^\uFEFF/,'');
    const tokens=md.parse(source,{});let title='',start=0;
    if(tokens[0]?.type==='heading_open'&&tokens[0].tag==='h1'){
     title=(tokens[1].children||[]).filter(t=>t.type==='text'||t.type==='code_inline').map(t=>t.content).join('');start=3;
    }
    const html='<section style="'+PREVIEW_STYLES.root+'">'+md.renderer.render(tokens.slice(start),md.options,{})+'</section>';
    return {title,hasBody:tokens.length>start,html,previewHtml:previewMarkup(html),characters:Array.from(source.replace(/\s/g,'')).length};
   }
  };
 }
 const api={create,safeURL,escape,previewMarkup,previewStylesheet,copyFallback};
 if(typeof module==='object'&&module.exports)module.exports=api;else root.StudioRadarArticle=api;
})(typeof window!=='undefined'?window:globalThis);
