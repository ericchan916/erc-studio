'use strict';
// Four creator URLs and conservative marker precedence are adapted from the
// user's existing data-scientist-community collectors; no Cookie import.
const PLATFORMS = Object.freeze([
  {id:'xiaohongshu',name:'小红书',short:'红',color:'#ff2442',url:'https://creator.xiaohongshu.com/new/home',domains:['xiaohongshu.com'],positive:['笔记管理','笔记数据','直播场次数据'],negative:['手机号登录','验证码登录','登录小红书创作服务平台']},
  {id:'douyin',name:'抖音',short:'♪',color:'#15191e',url:'https://creator.douyin.com/creator-micro/content/manage',domains:['douyin.com','bytedance.com'],positive:['内容管理','作品管理','数据中心'],negative:['登录抖音创作者中心','登录后即可查看','验证码登录']},
  {id:'bilibili',name:'哔哩哔哩',short:'哔',color:'#fb7299',url:'https://member.bilibili.com/platform/data-up/video/',domains:['bilibili.com'],positive:['数据中心','稿件管理','稿件分析','近期稿件对比'],negative:['登录 B 站','登录B站','密码登录','短信登录']},
  {id:'kuaishou',name:'快手',short:'快',color:'#ff5400',url:'https://cp.kuaishou.com/',domains:['kuaishou.com'],positive:['作品管理','数据中心'],negative:['立即登录','扫码登录','手机号登录','验证码登录']},
  {id:'wechat',name:'微信公众号',short:'公',color:'#07b75a',url:'https://mp.weixin.qq.com/',domains:['weixin.qq.com','qq.com'],positive:['内容与互动','新的创作','发表记录','草稿箱'],negative:['扫码登录','使用账号登录','登录公众平台','登录微信公众号','微信扫码登录']},
  {id:'channels',name:'视频号',short:'视',color:'#ffa02e',url:'https://channels.weixin.qq.com/',domains:['weixin.qq.com','qq.com'],positive:['发表视频','内容管理','视频号数据','互动管理'],negative:['立即登录','登录视频号助手','微信扫码登录','扫码登录视频号','视频号助手登录']}
]);
function platform(id){const p=PLATFORMS.find(x=>x.id===id);if(!p)throw new Error('platform_invalid');return p;}
function allowedURL(value,id,qaOrigin){
  try{const u=new URL(value);if(qaOrigin&&u.origin===qaOrigin)return true;
    if(u.protocol!=='https:'||u.username||u.password)return false;
    return platform(id).domains.some(d=>u.hostname===d||u.hostname.endsWith('.'+d));
  }catch{return false;}
}
function classify(id,evidence){
  const p=platform(id),text=String(evidence.text||'');
  if(evidence.networkError)return {status:'unknown',reason:'页面或网络加载异常'};
  if(evidence.challenge)return {status:'verification',reason:'平台要求安全验证，请在原页面完成'};
  const loginURL=/\/login(?:[/?#]|$)|\/passport\/|passport\./i.test(evidence.url||'');
  const login=evidence.qr||loginURL||evidence.loginButton||p.negative.some(x=>text.includes(x));
  if(login)return {status:'login_required',reason:evidence.qr?'已显示官方扫码入口，等待本人授权':'请在平台页面完成登录'};
  // An unrecognised page (including an apparently valid Cookie) never means success.
  if(p.positive.some(x=>text.includes(x)))return {status:'authorized',reason:'已识别当前平台后台页面'};
  return {status:'unknown',reason:'尚未确认后台登录状态'};
}
function isLoginLanding(id,value){try{const u=new URL(value);return (id==='kuaishou'&&u.hostname==='cp.kuaishou.com'&&['/','/profile'].includes(u.pathname))||(id==='channels'&&u.hostname==='channels.weixin.qq.com'&&u.pathname==='/');}catch{return false;}}
const READ_EVIDENCE = `(()=>{const text=(document.body?.innerText||'').slice(0,60000);const visible=e=>!!e.getClientRects().length;const qr=[...document.querySelectorAll('img,canvas,svg')].some(e=>visible(e)&&(/qrcode|qr-code|qr_code|二维码/i.test([e.className?.baseVal||e.className,e.id,e.getAttribute('alt'),e.getAttribute('src')].join(' '))||(/扫码|扫描|二维码/.test(text)&&e.getBoundingClientRect().width>=130&&e.getBoundingClientRect().height>=130)));const loginButton=[...document.querySelectorAll('button,a,[role="button"]')].some(e=>visible(e)&&/^(立即登录|登录|扫码登录)$/.test(e.textContent.trim()));return {text,url:location.href,qr:qr&&/扫码|扫描|二维码/.test(text),loginButton,challenge:/验证失败|请完成安全验证|操作过于频繁|访问受限|安全验证中/.test(text)}})()`;
const OPEN_LOGIN = `(()=>{const visible=e=>!!e.getClientRects().length;const candidates=[...document.querySelectorAll('button,a,[role="button"],span,div')].filter(e=>visible(e)&&/^(立即登录|登录)$/.test(e.textContent.trim()));const target=candidates.sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length)[0];if(target){if(target.tagName==='A'&&target.href)return {href:target.href};target.click();return {clicked:true}}return null})()`;
const OPEN_QR = `(()=>{const visible=e=>!!e.getClientRects().length;const candidates=[...document.querySelectorAll('button,a,[role="button"],span,div')].filter(e=>visible(e)&&/^(扫码登录|使用其他头像、昵称或账号)$/.test(e.textContent.trim()));const target=candidates.sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length)[0];if(target){target.click();return true}if(location.hostname==='creator.xiaohongshu.com'){const wrapper=document.querySelector('.sso-login-wrapper');if(wrapper&&/短信登录|验证码登录/.test(wrapper.innerText)){const image=[...wrapper.querySelectorAll('img')].find(e=>{const r=e.getBoundingClientRect();return visible(e)&&r.width===64&&r.height===64});if(image){image.click();return true}}}return false})()`;
module.exports={PLATFORMS,platform,allowedURL,classify,isLoginLanding,READ_EVIDENCE,OPEN_LOGIN,OPEN_QR};
