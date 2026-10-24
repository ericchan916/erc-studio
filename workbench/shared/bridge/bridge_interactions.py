"""Durable member-bound Card 1.0 interactions; callback ingress performs no business I/O."""
from __future__ import annotations
import hashlib,json,os,re,secrets,sqlite3,time,uuid
from pathlib import Path

WORKFLOW='cx_native'
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def status_card(title,text,color='blue'):
    return {'config':{'wide_screen_mode':True},'header':{'title':{'tag':'plain_text','content':title},'template':color},'elements':[{'tag':'div','text':{'tag':'plain_text','content':text}}]}
def response(title,text,color='blue',error=False):
    return {'toast':{'type':'error' if error else 'info','content':title},'card':{'type':'raw','data':status_card(title,text,color)}}
def parse(payload):
    e=payload.get('event') if isinstance(payload.get('event'),dict) else payload
    a=e.get('action') or {};v=a.get('value',payload.get('action_value',{}))
    if isinstance(v,str):v=json.loads(v)
    c=e.get('context') or {};u=e.get('operator') or {};h=payload.get('header') or {}
    return {'event_id':h.get('event_id') or payload.get('event_id') or e.get('event_id'),
            'user':u.get('open_id') or payload.get('operator_id'),
            'chat':c.get('open_chat_id') or payload.get('chat_id'),
            'message':c.get('open_message_id') or payload.get('message_id'),
            'token':e.get('token') or payload.get('token'),'value':v}

def human_args(args):
    """Render all frozen arguments as plain text, never an executable JSON/code block."""
    parts=['操作：'+' '.join(args[:2])];i=2
    labels={'--name':'名称','--table-name':'数据表','--base-token':'Base ID','--folder-token':'保存文件夹ID','--table-id':'数据表ID','--time-zone':'时区','--title':'标题','--fields':'字段','--json':'变更内容','--data':'变更内容','--params':'目标参数'}
    def human(v):
        if isinstance(v,dict):
            labels={'name':'名称','type':'类型','multiple':'多选','options':'选项','style':'格式','currency_code':'货币','precision':'小数位','format':'日期格式','rules':'编号规则','default_value':'默认值','description':'说明'}
            return '；'.join(labels.get(str(k),str(k))+'：'+human(x) for k,x in v.items())
        if isinstance(v,list):return '\n'.join('• '+human(x) for x in v)
        if type(v) is bool:return '是' if v else '否'
        return str(v)
    while i<len(args):
        k,sep,v=args[i].partition('=')
        if not sep and i+1<len(args) and not args[i+1].startswith('--'):i+=1;v=args[i]
        if k not in {'--as','--format','--yes'}:
            try:v=human(json.loads(v))
            except (ValueError,TypeError):pass
            parts.append(labels.get(k,k.removeprefix('--') if hasattr(k,'removeprefix') else k[2:])+'：'+v)
        i+=1
    text='\n\n'.join(parts)
    if args[:2]==['base','+base-create']:text+='\n\n平台默认空表保留；失败时保留已创建资源，先核验再处理。'
    if len(text)>18000:raise ValueError('confirmation_too_large_split_plan')
    return text

class InteractionStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS interactions(
              id TEXT PRIMARY KEY,user TEXT NOT NULL,chat TEXT NOT NULL,task TEXT NOT NULL,
              kind TEXT NOT NULL,binding TEXT NOT NULL,payload TEXT NOT NULL,nonce TEXT NOT NULL,
              expires REAL NOT NULL,message TEXT NOT NULL DEFAULT '',state TEXT NOT NULL,
              decision TEXT,event_id TEXT,callback_token TEXT,final_card TEXT,updated REAL NOT NULL,
              UNIQUE(user,chat,binding))''')
        self.path.chmod(0o600)
    def db(self):
        c=sqlite3.connect(str(self.path),timeout=.3);c.row_factory=sqlite3.Row;return c
    def get(self,id):
        with self.db() as c:r=c.execute('SELECT * FROM interactions WHERE id=?',(id,)).fetchone()
        return dict(r) if r else None
    def ensure(self,user,chat,task,kind,binding,payload):
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO interactions(id,user,chat,task,kind,binding,payload,nonce,expires,state,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (secrets.token_hex(8),user,chat,task,kind,binding,json.dumps(payload,ensure_ascii=False),secrets.token_hex(12),time.time()+1800,'pending',time.time()))
            row=dict(c.execute('SELECT * FROM interactions WHERE user=? AND chat=? AND binding=?',(user,chat,binding)).fetchone())
        return row
    def bind(self,id,message):
        if not isinstance(message,str) or not message.startswith('om_'):raise ValueError('message_binding_invalid')
        with self.db() as c:
            r=c.execute('UPDATE interactions SET message=?,updated=? WHERE id=? AND message IN (?,?)',(message,time.time(),id,'',message))
            if r.rowcount!=1:raise ValueError('message_binding_conflict')
    def card(self,row):
        p=json.loads(row['payload']);kind=row['kind']
        if kind=='approval':
            title='请确认本次飞书操作';body=human_args(p['args']);choices=[('confirm','确认执行'),('revise','修改需求'),('cancel','取消')]
        elif kind=='question':
            title='需要你确认一下';body=p['question'];choices=[('choose'+str(i),x) for i,x in enumerate(p['options'])]+[('revise','在聊天中补充')]
        else:
            title='内容助手 · 按钮验收';body='这是一张无业务副作用的测试卡。点击只验证本人身份、即时反馈和原卡更新，不创建表格、不记账、不发其他消息。';choices=[('smoke','测试按钮')]
        card=status_card(title,body,'red' if p.get('risk')=='high-risk-write' else 'orange')
        card['elements'].append({'tag':'note','elements':[{'tag':'plain_text','content':'仅限本人 · 30分钟有效 · 参数修改须重新确认'}]})
        card['elements'].append({'tag':'action','actions':[{'tag':'button','text':{'tag':'plain_text','content':label},'type':'primary' if n==0 else 'default','value':{'workflow':WORKFLOW,'id':row['id'],'nonce':row['nonce'],'decision':d}} for n,(d,label) in enumerate(choices)]})
        return card
    def current(self,row):
        if row['kind']=='smoke':return True
        p=json.loads(row['payload']);database=p['operations_db']
        with sqlite3.connect('file:'+database+'?mode=ro',uri=True,timeout=.2) as c:
            if row['kind']=='approval':
                r=c.execute('SELECT chat_id,state,args FROM actions WHERE id=?',(p['approval_id'],)).fetchone()
                return bool(r and r[0]==row['chat'] and r[1]=='pending' and digest(json.loads(r[2]))==p['args_hash'])
            r=c.execute('SELECT chat_id,state,payload FROM native_tasks WHERE id=?',(row['task'],)).fetchone()
            return bool(r and r[0]==row['chat'] and r[1]=='waiting_input' and (json.loads(r[2]).get('question') or {}).get('id')==p['question_id'])
    def accept(self,payload):
        x=parse(payload);v=x['value']
        if not isinstance(v,dict) or v.get('workflow')!=WORKFLOW:raise ValueError('workflow_invalid')
        row=self.get(v.get('id',''))
        if not row or not secrets.compare_digest(str(v.get('nonce','')),row['nonce']):raise ValueError('nonce_invalid')
        if (x['user'],x['chat'],x['message'])!=(row['user'],row['chat'],row['message']) or not row['message'] or not x['event_id'] or not x['token']:raise ValueError('identity_or_message_mismatch')
        decision=v.get('decision');p=json.loads(row['payload'])
        choices={'confirm','revise','cancel'} if row['kind']=='approval' else {'smoke'} if row['kind']=='smoke' else {'revise',*('choose'+str(i) for i in range(len(p['options'])))}
        if decision not in choices:raise ValueError('decision_invalid')
        if row['state']!='pending':
            card=json.loads(row['final_card']) if row['final_card'] else status_card('已收到','该卡已处理，操作不会重复执行。')
            return {'accepted':False,'duplicate':True,'callback_response':{'toast':{'type':'info','content':'该卡已处理'},'card':{'type':'raw','data':card}}}
        if time.time()>row['expires'] or not self.current(row):
            return {'accepted':False,'callback_response':response('此卡已失效','目标参数或任务状态已变化，请重新发起预览。','grey',True)}
        with self.db() as c:
            changed=c.execute("UPDATE interactions SET state='accepted',decision=?,event_id=?,callback_token=?,expires=?,updated=? WHERE id=? AND state='pending'",(decision,x['event_id'],x['token'],time.time()+1700,time.time(),row['id'])).rowcount
        return {'accepted':bool(changed),'callback_response':response('已收到，正在处理','确认已保存；请稍候，结果会更新在本卡。')}
    def ready(self):
        with self.db() as c:return [dict(r) for r in c.execute("SELECT * FROM interactions WHERE state='accepted' ORDER BY updated")]
    def event(self,row):
        p=json.loads(row['payload']);d=row['decision']
        text=('确认 '+p['approval_id']) if d=='confirm' else p['options'][int(d[6:])] if d.startswith('choose') else ''
        event={'event_id':'native-card:'+row['id'],'message_id':row['message'],'chat_id':row['chat'],'sender_id':row['user'],'chat_type':p.get('chat_type','p2p'),'message_type':'text','content':text,'native_card_id':row['id']}
        if p.get('conversation_id'):event['conversation_id']=p['conversation_id']
        if row['kind']=='approval':event['native_args_hash']=p['args_hash']
        if row['kind']=='question':event['clarification_task_id']=row['task']
        return event
    def final(self,id,text,title='处理结果',color='blue'):
        card=status_card(title,text[:24000],color)
        with self.db() as c:c.execute("UPDATE interactions SET state='final',final_card=?,updated=? WHERE id=?",(json.dumps(card,ensure_ascii=False),time.time(),id))
    def flush(self,sender):
        with self.db() as c:rows=[dict(r) for r in c.execute("SELECT * FROM interactions WHERE state='final' AND callback_token IS NOT NULL AND updated<?",(time.time()-2,))]
        for row in rows:
            if row['expires']<time.time():
                with self.db() as c:c.execute("UPDATE interactions SET state='delivery_expired',callback_token=NULL WHERE id=?",(row['id'],))
                continue
            try:
                sender._api('POST','/open-apis/interactive/v1/card/update',{'token':row['callback_token'],'card':json.loads(row['final_card'])})
                # A callback waiting for OAuth may finish after one status update.
                # Keep the second (and last) official update use for that result.
                payload=json.loads(row['payload']);payload['_delivery_count']=payload.get('_delivery_count',0)+1
                token=row['callback_token'] if payload['_delivery_count']<2 else None
                with self.db() as c:c.execute("UPDATE interactions SET state='delivered',callback_token=?,payload=?,updated=? WHERE id=?",(token,json.dumps(payload,ensure_ascii=False),time.time(),row['id']))
            except Exception:
                # Retry card delivery only, never the business action.
                with self.db() as c:c.execute('UPDATE interactions SET updated=? WHERE id=?',(time.time()+30,row['id']))
    def for_task(self,native,event):
        cli=native.contexts[event['sender_id']]
        with cli.connect() as c:
            r=c.execute('SELECT t.id,t.state,t.payload FROM native_tasks t JOIN native_task_events e ON e.task_id=t.id WHERE e.event_id=?',(event['event_id'],)).fetchone()
            if not r:return None
            task=json.loads(r[2])
            if r[1]=='waiting_confirmation':
                action=c.execute('SELECT args,state,risk FROM actions WHERE id=?',(task['approval_id'],)).fetchone()
                if not action or action[1]!='pending':return None
                args=json.loads(action[0]);payload={'args':args,'args_hash':digest(args),'approval_id':task['approval_id'],'risk':action[2],'operations_db':str(cli.database),'chat_type':event.get('chat_type','p2p')}
                if task.get('conversation_id'):payload['conversation_id']=task['conversation_id']
                human_args(args)
                return self.ensure(cli.user,event['chat_id'],r[0],'approval',task['approval_id'],payload)
            if r[1]=='waiting_input':
                q=task['question'];payload={**q,'question_id':q['id'],'operations_db':str(cli.database),'chat_type':event.get('chat_type','p2p')}
                if task.get('conversation_id'):payload['conversation_id']=task['conversation_id']
                return self.ensure(cli.user,event['chat_id'],r[0],'question',q['id'],payload)
        return None
    def send(self,sender,row):
        if row['message']:return
        card=self.card(row)
        sent=sender._api('POST','/open-apis/im/v1/messages',{'receive_id':row['chat'],'msg_type':'interactive','content':json.dumps(card,ensure_ascii=False),'uuid':str(uuid.uuid5(uuid.NAMESPACE_URL,'cx-native:'+row['id']))},{'receive_id_type':'chat_id'})
        self.bind(row['id'],(sent.get('data') or {}).get('message_id'))
    def poll(self,bridge):
        for row in self.ready():
            d=row['decision'];native=bridge.isolated_handler.native
            if row['kind']=='smoke':self.final(row['id'],'本人身份、回调接收、去重与原卡更新链路已通过；没有执行任何业务操作。','按钮验证通过','green');continue
            if not self.current(row):
                self.final(row['id'],'任务或参数已发生变化，未按旧卡执行，请重新预览。','旧卡已失效','grey');continue
            if d in {'cancel','revise'}:
                cli=native.contexts[row['user']];p=json.loads(row['payload'])
                with cli.connect() as c:
                    if row['kind']=='approval':c.execute("UPDATE actions SET state='cancelled' WHERE id=? AND chat_id=? AND state='pending'",(p['approval_id'],row['chat']))
                    r=c.execute('SELECT payload FROM native_tasks WHERE id=? AND chat_id=?',(row['task'],row['chat'])).fetchone()
                    if r:
                        task=json.loads(r[0]);task.update(state='cancel_requested',answer='旧方案已取消，请发送新的要求。')
                        c.execute('UPDATE native_tasks SET state=?,payload=?,updated_at=? WHERE id=?',('cancel_requested',json.dumps(task,ensure_ascii=False),time.time(),row['task']))
                self.final(row['id'],'本次操作已取消。'+('请直接在聊天中补充信息，重新生成方案后再确认。' if d=='revise' else '没有执行待确认的业务操作。'),'等待补充' if d=='revise' else '已取消','grey');continue
            event=self.event(row);bridge.store.add_event(event);bridge.enqueue(event['event_id'],event['chat_id'])
            with self.db() as c:c.execute("UPDATE interactions SET state='queued',updated=? WHERE id=? AND state='accepted'",(time.time(),row['id']))
        # Recover display after restart from durable Bridge outcome; no write replay.
        with self.db() as c:rows=[dict(r) for r in c.execute("SELECT * FROM interactions WHERE state='queued'")]
        for row in rows:
            with bridge.store._connect() as c:r=c.execute('SELECT status,response_text FROM events WHERE event_id=?',('native-card:'+row['id'],)).fetchone()
            if r and r[0] in {'done','execution_unknown','failed'}:
                self.final(row['id'],r[1] or '操作尚未完成，结果需要核验；已停止重复执行。','处理结果' if r[0]=='done' else '需要核验','blue' if r[0]=='done' else 'red')
        self.flush(bridge.sender)

if __name__=='__main__':
    import sys
    try:
        raw=sys.stdin.buffer.read(262145)
        if len(raw)>262144:raise ValueError('payload_limit')
        path=Path.home()/'Library/Application Support/CherryFeishuBridge/native-interactions.db'
        if not path.is_file():raise ValueError('interaction_service_not_ready')
        result=InteractionStore(path).accept(json.loads(raw))
    except Exception:
        result={'accepted':False,'callback_response':{'toast':{'type':'error','content':'未接受操作：请由本人使用当前有效卡片，或稍后重试。'}}}
    print(json.dumps(result,ensure_ascii=False))
