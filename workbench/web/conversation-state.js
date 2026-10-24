'use strict';
// Private in-memory UI state. No credentials, storage, DOM, network or borrowed identity.
(function(root){
class ConversationState{
 constructor(){this.identityEpoch=0;this.viewEpoch=0;this.listEpoch=0;this.member=null;this.clearPrivate();}
 clearPrivate(){this.selected=null;this.scope='active';this.search='';this.items=[];this.cursor=null;this.revision=null;this.counts={active:0,archived:0,trash:0};this.messages=[];this.olderCursor=null;this.version=null;this.selectedMeta=null;this.drafts=new Map();this.pending=new Map();this.jobs=new Map();this.currentDraft='';}
 identity(member){if(!member||this.member?.principal_id!==member.principal_id){this.identityEpoch++;this.viewEpoch++;this.listEpoch++;this.clearPrivate();}this.member=member||null;return !!member;}
 capture(kind='view'){return {identity:this.identityEpoch,epoch:kind==='list'?this.listEpoch:this.viewEpoch,kind,principal:this.member?.principal_id??null};}
 accepts(ticket){return !!ticket&&ticket.identity===this.identityEpoch&&ticket.principal===(this.member?.principal_id??null)&&ticket.epoch===(ticket.kind==='list'?this.listEpoch:this.viewEpoch);}
 query(scope='active',search=''){if(!['active','archived','trash'].includes(scope)||typeof search!=='string'||search.length>100)throw Error('invalid_query');this.scope=scope;this.search=search;this.listEpoch++;this.items=[];this.cursor=null;this.revision=null;return this.capture('list');}
 select(cid){this.drafts.set(this.selected||'new',this.currentDraft);this.selected=cid||null;this.currentDraft=this.drafts.get(this.selected||'new')||'';this.viewEpoch++;this.messages=[];this.olderCursor=null;this.version=null;this.selectedMeta=null;return this.capture('view');}
 draft(text){if(typeof text!=='string'||text.length>16000)throw Error('invalid_draft');this.currentDraft=text;this.drafts.set(this.selected||'new',text);}
 acceptPage(reply,ticket,append=false){if(!this.accepts(ticket)||!Array.isArray(reply.items))return false;if(append&&this.revision!==null&&reply.revision!==this.revision)return false;const current=append?this.items:[],map=new Map(current.map(x=>[x.conversation_id,x]));for(const item of reply.items)map.set(item.conversation_id,item);this.items=[...map.values()];this.cursor=reply.next_cursor??null;this.revision=reply.revision??null;this.counts=reply.counts||this.counts;return true;}
 acceptMessages(reply,ticket,older=false){
  if(!this.accepts(ticket)||!Array.isArray(reply.items))return false;
  const existing=this.messages,previousCursor=this.olderCursor;
  const all=older?[...reply.items,...existing]:[...existing,...reply.items],map=new Map();
  for(const item of all)map.set(item.message_id,item);
  this.messages=[...map.values()];
  if(this.messages.every(m=>Number.isInteger(m.seq)))this.messages.sort((a,b)=>a.seq-b.seq);
  const preservedEarlier=!older&&existing.length&&reply.items.length&&Number.isInteger(existing[0].seq)&&existing[0].seq<reply.items[0].seq;
  this.olderCursor=preservedEarlier?previousCursor:reply.older_cursor??null;
  this.version=reply.version??null;
  if(reply.conversation)this.selectedMeta=reply.conversation;
  const refs=this.messages.filter(m=>m.job_id&&!m.entry_kind),waiting=refs.find(m=>['waiting_input','waiting_confirmation','waiting_authorization','paused'].includes(m.job_state)),active=refs.find(m=>['queued','running','cancel_requested'].includes(m.job_state)),m=waiting||active||refs.at(-1);
  if(m){const old=this.jobs.get(this.selected);this.jobs.set(this.selected,{...(old?.job_id===m.job_id?old:{}),job_id:m.job_id,state:m.job_state,message_id:m.message_id});}
  return true;
 }

 metadata(item){this.items=this.items.map(x=>x.conversation_id===item.conversation_id?item:x);if(item.conversation_id===this.selected)this.version=item.version;}
 removed(cid){this.items=this.items.filter(x=>x.conversation_id!==cid);this.drafts.delete(cid);this.pending.delete(cid);this.jobs.delete(cid);this.listEpoch++;if(this.selected===cid){this.selected=null;this.currentDraft=this.drafts.get('new')||'';this.messages=[];this.olderCursor=null;this.version=null;this.selectedMeta=null;this.viewEpoch++;}}
 request(cid,body,key){const scope=cid||'new';const old=this.pending.get(scope);if(old)return old;const value={body:{...body},key,identityEpoch:this.identityEpoch};this.pending.set(scope,value);return value;}
 acknowledged(cid){this.pending.delete(cid||'new');}
}
if(typeof module==='object'&&module.exports)module.exports={ConversationState};else root.StudioConversationState=ConversationState;
})(typeof window==='object'?window:globalThis);
