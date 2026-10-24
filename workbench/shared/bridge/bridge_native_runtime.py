"""Per-member technical checkpoints, resource budgets and fair model admission."""
from __future__ import annotations
import contextlib
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from bridge_cherry import ExecutionRejected

@dataclass(frozen=True)
class NativeBudget:
    model_turns: int = 64
    tool_calls: int = 256
    wall_seconds: int = 600
    repeated_results: int = 3
    context_chars: int = 120000
    model_concurrency: int = 2

    @classmethod
    def parse(cls, value=None):
        value={} if value is None else value
        bounds={'model_turns':(16,256),'tool_calls':(16,1024),'wall_seconds':(60,1800),
                'repeated_results':(2,8),'context_chars':(32000,500000),'model_concurrency':(1,8)}
        if not isinstance(value,dict) or set(value)-set(bounds):raise ValueError('native_execution_budget_invalid')
        for key,v in value.items():
            if type(v) is not int or not bounds[key][0]<=v<=bounds[key][1]:raise ValueError('native_execution_budget_invalid:'+key)
        return cls(**value)

class FairModelGate:
    """FIFO admission per inference, not a slot held by an entire long task."""
    def __init__(self,capacity=2):
        self.capacity=capacity;self.condition=threading.Condition();self.waiters=[];self.active=set()

    @contextlib.contextmanager
    def enter(self,user,timeout=120):
        ticket=object();deadline=time.monotonic()+timeout
        with self.condition:
            self.waiters.append((ticket,user))
            while True:
                eligible=[t for t,u in self.waiters if u not in self.active]
                if user not in self.active and len(self.active)<self.capacity and eligible and eligible[0] is ticket:
                    self.waiters.remove((ticket,user));self.active.add(user);self.condition.notify_all();break
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    self.waiters.remove((ticket,user));self.condition.notify_all()
                    raise ExecutionRejected('模型请求正在排队，已保存进度；稍后继续当前事项。')
                self.condition.wait(remaining)
        try:yield
        finally:
            with self.condition:self.active.remove(user);self.condition.notify_all()

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def conversation_scope(event):
    """Transport chat is not a logical conversation; only canonical UUID scopes are accepted."""
    value=event.get('conversation_id')
    if value is None:return None
    try:
        if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ExecutionRejected('对话归属标识不符合契约。') from None
    return value

class NativeTaskJournal:
    """Technical transcript only; never a new business ledger or credential store."""
    def __init__(self,cli):
        self.cli=cli
        with cli.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS native_tasks(
                id TEXT PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,chat_id TEXT NOT NULL,
                input_hash TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS native_task_events(event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,chat_id TEXT NOT NULL,input_hash TEXT NOT NULL);''')

    def load(self,event,text,messages,continue_task=False):
        digest=fingerprint(text);eid=event['event_id'];chat=event['chat_id'];scope=conversation_scope(event)
        with self.cli.connect() as db:
            binding=db.execute('SELECT task_id,chat_id,input_hash FROM native_task_events WHERE event_id=?',(eid,)).fetchone()
            if binding:
                if binding[1]!=chat or binding[2]!=digest:raise ExecutionRejected('任务回执与当前本人会话不一致。')
                row=db.execute('SELECT payload FROM native_tasks WHERE id=?',(binding[0],)).fetchone()
            elif continue_task:
                candidates=db.execute("SELECT payload FROM native_tasks WHERE chat_id=? AND state='paused' ORDER BY updated_at DESC",(chat,)).fetchall()
                row=next((r for r in candidates if json.loads(r[0]).get('conversation_id')==scope),None)
            else:row=None
            if row:task=json.loads(row[0])
            else:
                task={'id':uuid.uuid4().hex,'event_id':eid,'chat_id':chat,'user':self.cli.user,
                      'input_hash':digest,'state':'running','messages':messages,'pending':[],
                      'pending_index':0,'turns':0,'results_seen':{},'answer':None,'approval_id':None}
                if scope:task['conversation_id']=scope
            if task['chat_id']!=chat or task['user']!=self.cli.user or task.get('conversation_id')!=scope:raise ExecutionRejected('任务检查点身份或对话归属不匹配。')
            db.execute('INSERT OR IGNORE INTO native_task_events VALUES(?,?,?,?)',(eid,task['id'],chat,digest))
            self._save(db,task)
            return task

    def _save(self,db,task):
        prior=db.execute('SELECT state,payload FROM native_tasks WHERE id=?',(task['id'],)).fetchone()
        if prior and json.loads(prior[1]).get('conversation_id')!=task.get('conversation_id'):
            raise ExecutionRejected('既有任务的对话归属保持不变。')
        if prior and prior[0]=='cancel_requested' and task['state']!='cancelled':task['state']='cancel_requested'
        db.execute('''INSERT INTO native_tasks VALUES(?,?,?,?,?,?,?) ON CONFLICT(id)
            DO UPDATE SET state=excluded.state,payload=excluded.payload,updated_at=excluded.updated_at''',
            (task['id'],task['event_id'],task['chat_id'],task['input_hash'],task['state'],json.dumps(task,ensure_ascii=False),time.time()))

    def save(self,task):
        with self.cli.connect() as db:self._save(db,task)

    def awaiting(self,chat,approval_id,conversation_id=None):
        with self.cli.connect() as db:
            rows=db.execute("SELECT payload FROM native_tasks WHERE chat_id=? AND state IN ('waiting_confirmation','waiting_auth','unknown') ORDER BY updated_at DESC",(chat,)).fetchall()
        return next((t for row in rows for t in [json.loads(row[0])] if t.get('approval_id')==approval_id and t.get('conversation_id')==conversation_id),None)

    def bind_event(self,task,event,text):
        if task['chat_id']!=event['chat_id'] or task.get('conversation_id')!=conversation_scope(event):
            raise ExecutionRejected('控制消息与当前事项的对话归属不一致。')
        with self.cli.connect() as db:
            row=db.execute('SELECT task_id,chat_id,input_hash FROM native_task_events WHERE event_id=?',(event['event_id'],)).fetchone()
            if row and (row[0]!=task['id'] or row[1]!=event['chat_id'] or row[2]!=fingerprint(text)):
                raise ExecutionRejected('确认消息已绑定其他事项，未切换目标。')
            db.execute('INSERT OR IGNORE INTO native_task_events VALUES(?,?,?,?)',(event['event_id'],task['id'],event['chat_id'],fingerprint(text)))

    def question(self,chat,task_id=None,conversation_id=None):
        with self.cli.connect() as db:
            if task_id:row=db.execute("SELECT payload FROM native_tasks WHERE chat_id=? AND id=? AND state='waiting_input'",(chat,task_id)).fetchone()
            else:
                rows=db.execute("SELECT payload FROM native_tasks WHERE chat_id=? AND state='waiting_input' ORDER BY updated_at DESC",(chat,)).fetchall()
                row=next((r for r in rows if json.loads(r[0]).get('conversation_id')==conversation_id),None)
        task=json.loads(row[0]) if row else None
        return task if task and task.get('conversation_id')==conversation_id else None

    def reset(self,chat,conversation_id=None):
        with self.cli.connect() as db:
            rows=db.execute('SELECT id,payload FROM native_tasks WHERE chat_id=?',(chat,)).fetchall()
            for row in rows:
                if json.loads(row[1]).get('conversation_id')!=conversation_id:continue
                db.execute('DELETE FROM native_task_events WHERE task_id=?',(row[0],))
                db.execute('DELETE FROM native_tasks WHERE id=?',(row[0],))

    def cancelled(self,task_id):
        with self.cli.connect() as db:row=db.execute('SELECT state FROM native_tasks WHERE id=?',(task_id,)).fetchone()
        return bool(row and row[0]=='cancel_requested')

    def request_cancel(self,chat,conversation_id=None):
        with self.cli.connect() as db:
            rows=db.execute("SELECT id,payload FROM native_tasks WHERE chat_id=? AND state IN ('running','paused','waiting_confirmation','waiting_auth','waiting_input') ORDER BY updated_at DESC",(chat,)).fetchall()
            row=next((r for r in rows if json.loads(r[1]).get('conversation_id')==conversation_id),None)
            if not row:return False
            task=json.loads(row[1]);db.execute("UPDATE native_tasks SET state='cancel_requested',updated_at=? WHERE id=?",(time.time(),row[0]))
            if task.get('approval_id'):
                db.execute("UPDATE actions SET state='cancelled' WHERE id=? AND chat_id=? AND state='pending'",(task['approval_id'],chat))
            return True

def compact_metadata(messages,maximum):
    """Discard redundant *official metadata* only. Keep all business data and pending calls."""
    if len(json.dumps(messages,ensure_ascii=False))<=maximum:return messages
    groups=[];i=0
    while i<len(messages):
        message=messages[i];calls=message.get('tool_calls') or []
        if message.get('role')=='assistant' and calls:
            group=[message];i+=1
            while i<len(messages) and messages[i].get('role')=='tool':group.append(messages[i]);i+=1
            meta=True
            for call in calls:
                try:args=json.loads(call['function']['arguments'])['args']
                except (KeyError,ValueError,TypeError):meta=False;break
                if not args or not (args[0] in {'skills','schema'} or '--help' in args):meta=False
            groups.append((group,meta))
        else:groups.append(([message],False));i+=1
    removed=[]
    while len(json.dumps([m for group,_ in groups for m in group],ensure_ascii=False))>maximum:
        index=next((n for n,(_,meta) in enumerate(groups) if meta),None)
        if index is None:break
        group,_=groups.pop(index)
        removed.extend(c['function']['arguments'] for c in group[0]['tool_calls'])
    result=[m for group,_ in groups for m in group]
    if removed:result.insert(1,{'role':'user','content':'已读取的官方帮助已从本次模型上下文移出以节省预算；完整结果仍在本人任务检查点中。不要把它当业务数据缺失。已读命令：'+'；'.join(removed)})
    return result
