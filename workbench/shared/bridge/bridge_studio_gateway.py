"""Opt-in personal ingress and lifecycle for the existing Bridge.

Platform polling, Typing, final CardKit delivery and the single callback owner
remain in Bridge. This adapter forwards only verified, bound P2P members.
"""
import json
import os
import threading
import time
import hashlib
import datetime as dt
from pathlib import Path
from bridge_cherry import ExecutionRejected, ExecutionUnknown
from bridge_studio_execution import StudioExecution, ProtocolError, studio_request, server_for, identifier, read_key

TERMINAL=('succeeded','failed','cancelled','execution_unknown')
WAITING=('waiting_input','waiting_confirmation','waiting_authorization','paused')


class StudioGateway:
    def __init__(self,bridge,config):
        allowed={'enabled','gateway_config','key_file','port','wait_seconds','cutover_at'}
        if not isinstance(config,dict) or set(config)-allowed or not {'gateway_config','key_file','port'}<=set(config):
            raise ValueError('studio_gateway_config_invalid')
        path=Path(config['gateway_config'])
        if not path.is_absolute() or path.is_symlink() or path.stat().st_mode&0o077 or path.stat().st_uid!=os.getuid():
            raise ValueError('studio_gateway_config_not_private')
        gate=json.loads(path.read_text())
        if gate.get('host')!='127.0.0.1' or gate.get('feishu_profile')!=bridge.config['feishu_profile']:
            raise ValueError('studio_gateway_profile_binding_invalid')
        if not gate.get('unified_conversations',{}).get('enabled'):
            raise ValueError('studio_gateway_not_enabled')
        self.bridge=bridge;self.key_file=config['key_file'];read_key(self.key_file)
        self.cutover=config.get('cutover_at')
        if self.cutover is not None:
            try:self.cutover=dt.datetime.fromisoformat(self.cutover.replace('Z','+00:00'))
            except (ValueError,AttributeError):raise ValueError('studio_cutover_invalid')
            if self.cutover.tzinfo is None:raise ValueError('studio_cutover_invalid')
        elif gate['feishu_profile']!='fixture':raise ValueError('studio_cutover_required')
        self.origin='http://127.0.0.1:'+str(gate['port'])
        self.members={m['open_id']:dict(m) for m in gate['members'] if m.get('active',True)}
        if len(self.members)!=sum(m.get('active',True) for m in gate['members']):raise ValueError('studio_gateway_member_binding_invalid')
        self.port=config['port'];self.wait_seconds=config.get('wait_seconds',1830)
        if type(self.port) is not int or not 1024<=self.port<=65535 or type(self.wait_seconds) is not int or not 30<=self.wait_seconds<=1860:
            raise ValueError('studio_gateway_port_or_wait_invalid')
        # Only the lock-owning running Bridge may recover execution rows.
        # build_bridge --check is read-only and must not mark live jobs unknown.
        self.execution=None
        self.server=None;self.thread=None
        self.ingress_lock=threading.RLock()

    def eligible(self,event):
        if self.execution is None:return False
        if event.get('chat_type')!='p2p' or event.get('origin') or event.get('deferred_input'):return False
        member=self.members.get(event.get('sender_id'))
        if not member or event.get('chat_id')!=member['chat_id']:return False
        if event.get('native_card_id') and not event.get('conversation_id'):return False # old cards retain their old path
        if self.cutover and not self.admitted(event):
            with self.bridge.store._connect() as db:
                row=db.execute('SELECT created_at FROM events WHERE event_id=?',(event.get('event_id') or event.get('message_id'),)).fetchone()
            if row:
                try:created=dt.datetime.fromisoformat(row[0].replace('Z','+00:00'))
                except ValueError:return False
                if created.tzinfo is None or created<self.cutover:return False
        return True

    def member_body(self,user,chat):
        member=self.members.get(user)
        if not member or member['chat_id']!=chat:raise ExecutionRejected('本人私聊与工作台成员绑定不一致。')
        return {'open_id':user,'tenant_key':member['tenant_key'],'chat_id':chat}

    def call(self,action,body,timeout=25):
        return studio_request(self.origin,'/internal/studio/'+action,body,self.key_file,timeout)

    def lease(self,body):
        packet=self.call('lease',{k:body[k] for k in ('owner','job_id','conversation_id','attempt_id')})
        return packet.get('valid') is True

    def start(self):
        if self.server:return
        # Reserve the listener before recovering durable executions. Port
        # collision must not turn another owner's live attempt into unknown.
        execution=self.execution or StudioExecution(self.bridge,list(self.members.values()),self.lease,recover_running=False)
        server=server_for(execution,self.key_file,self.port)
        try:
            if self.execution is None:execution.recover_running()
        except Exception:
            server.server_close();raise
        self.execution=execution;self.server=server
        try:
            with self.bridge.store._connect() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS studio_ingress_events(
                    event TEXT PRIMARY KEY,user TEXT NOT NULL,chat TEXT NOT NULL,text_hash TEXT NOT NULL,
                    receipt_json TEXT,reaction TEXT,updated REAL NOT NULL)''')
                db.execute('''CREATE TABLE IF NOT EXISTS studio_legacy_receipts(
                    event TEXT PRIMARY KEY,user TEXT NOT NULL,chat TEXT NOT NULL,
                    packet_json TEXT NOT NULL,state TEXT NOT NULL)''')
        except Exception:
            self.server.server_close();self.server=None;self.execution=None;raise
        self.thread=threading.Thread(target=self.server.serve_forever,name='studio-execution-port',daemon=True);self.thread.start()

    def poll_authorizations(self):
        if self.server and self.execution:self.execution.poll_authorizations()

    def capture_legacy(self,event):
        member=self.members.get(event.get('sender_id'))
        if not member or event.get('chat_id')!=member['chat_id'] or event.get('chat_type')!='p2p' or event.get('origin') or self.eligible(event):return
        eid=event.get('event_id') or event.get('message_id')
        with self.bridge.store._connect() as db:
            old=db.execute('SELECT event FROM studio_legacy_receipts WHERE event=?',(eid,)).fetchone()
            row=db.execute('SELECT response_text,updated_at,status FROM events WHERE event_id=? AND sender_id=? AND chat_id=?',
                (eid,event['sender_id'],event['chat_id'])).fetchone()
        if old or not row or row['status'] not in ('done','reconciled') or not row['response_text']:return
        parent=eid;settled=True
        native=getattr(self.bridge.isolated_handler,'native',None) if self.bridge.isolated_handler else None
        context=native.contexts.get(event['sender_id']) if native else None
        if context:
            with context.connect() as db:
                task=db.execute('SELECT t.payload FROM native_tasks t JOIN native_task_events e ON e.task_id=t.id WHERE e.event_id=?',(eid,)).fetchone()
            if task:
                task=json.loads(task[0])
                if task.get('conversation_id') is not None:return
                if (task['user'],task['chat_id'])!=(event['sender_id'],event['chat_id']):raise ExecutionRejected('旧任务归属未通过本人私聊核验。')
                parent=task['event_id'];settled=task['state'] in ('done','cancelled')
        packet={**self.member_body(event['sender_id'],event['chat_id']),'source_id':eid,'parent_source_id':parent,
            'text':str(event.get('content') or ''),'answer':row['response_text'],'created_at':row['updated_at'],'settled':settled}
        with self.bridge.store._connect() as db:
            db.execute('INSERT OR IGNORE INTO studio_legacy_receipts VALUES(?,?,?,?,?)',(eid,event['sender_id'],event['chat_id'],json.dumps(packet,ensure_ascii=False),'pending'))

    def poll_legacy_receipts(self):
        if not self.server:return
        # Recover a capture interrupted after original delivery, never the
        # model/tool call. Only pre-cutover personal events changed since the
        # cutover and legacy native callbacks are candidates.
        if self.cutover:
            cut=self.cutover.astimezone(dt.timezone.utc).isoformat()
            with self.bridge.store._connect() as db:
                rows=db.execute('''SELECT payload_json FROM events WHERE status IN ('done','reconciled') AND updated_at>=?
                    AND (created_at<? OR event_id LIKE 'native-card:%') AND NOT EXISTS(
                    SELECT 1 FROM studio_legacy_receipts r WHERE r.event=events.event_id) ORDER BY updated_at LIMIT 100''',(cut,cut)).fetchall()
            for row in rows:self.capture_legacy(json.loads(row[0]))
        with self.bridge.store._connect() as db:rows=db.execute("SELECT event,packet_json FROM studio_legacy_receipts WHERE state='pending' LIMIT 50").fetchall()
        for row in rows:
            result=self.call('legacy-receipt',json.loads(row['packet_json']))
            if result.get('imported') is True:
                with self.bridge.store._connect() as db:db.execute("UPDATE studio_legacy_receipts SET state='done' WHERE event=?",(row['event'],))

        with self.bridge.store._connect() as db:pending=db.execute("SELECT count(*) FROM studio_legacy_receipts WHERE state='pending'").fetchone()[0]
        if pending:raise ExecutionRejected('旧任务回执仍待历史库回读，未重放原业务。')

    def stop(self):
        if self.server:self.server.shutdown();self.server.server_close();self.server=None
        if self.thread:self.thread.join(3)

    def job_from_card(self,event):
        cards=self.bridge.native_interactions;card=cards.get(event['native_card_id']) if cards else None
        if not card or (card['user'],card['chat'],card['message'])!=(event['sender_id'],event['chat_id'],event.get('message_id')):
            raise ExecutionRejected('操作卡未通过本人及原消息绑定校验。')
        cli=self.bridge.isolated_handler.native.contexts[event['sender_id']]
        with cli.connect() as db:row=db.execute('SELECT payload FROM native_tasks WHERE id=?',(card['task'],)).fetchone()
        task=json.loads(row[0]) if row else None
        if not task or task.get('conversation_id')!=event.get('conversation_id') or not task['event_id'].startswith('studio-job:'):
            raise ExecutionRejected('操作卡未绑定原工作台对话。')
        return identifier(task['event_id'][len('studio-job:'):])

    def admitted(self,event):
        if not self.execution:return None
        with self.bridge.store._connect() as db:
            row=db.execute('SELECT * FROM studio_ingress_events WHERE event=? AND user=? AND chat=?',
                (event.get('event_id') or event.get('message_id'),event['sender_id'],event['chat_id'])).fetchone()
        return row

    def reaction(self,event):
        row=self.admitted(event)
        return row['reaction'] if row else None

    def save_reaction(self,event,value):
        with self.bridge.store._connect() as db:
            db.execute('UPDATE studio_ingress_events SET reaction=?,updated=? WHERE event=? AND user=? AND chat=?',
                (value,time.time(),event.get('event_id') or event.get('message_id'),event['sender_id'],event['chat_id']))

    def clear_reaction(self,message_id,reaction):
        with self.bridge.store._connect() as db:
            db.execute('UPDATE studio_ingress_events SET reaction=NULL WHERE reaction=? AND event IN (SELECT event_id FROM events WHERE message_id=?)',
                (reaction,message_id))

    def admit(self,event,text):
        if not self.eligible(event):raise ExecutionRejected('当前消息未绑定个人工作台入口。')
        base=self.member_body(event['sender_id'],event['chat_id'])
        source=event.get('message_id') or event.get('event_id');eid=event.get('event_id') or source
        if not isinstance(source,str) or not source or not isinstance(eid,str):raise ExecutionRejected('消息来源标识缺失。')
        try:
            with self.ingress_lock:
                self.execution.member(self.members[event['sender_id']]['principal_id'])
                with self.bridge.store._connect() as db:
                    stored=db.execute('SELECT sender_id,chat_id,payload_json FROM events WHERE event_id=?',(eid,)).fetchone()
                if not stored or (stored['sender_id'],stored['chat_id'])!=(event['sender_id'],event['chat_id']):
                    raise ExecutionRejected('原消息尚未通过持久来源与本人私聊核验。')
                original=json.loads(stored['payload_json'])
                if original.get('chat_type')!='p2p' or original.get('origin'):raise ExecutionRejected('原消息未绑定个人私聊入口。')
                fingerprint=hashlib.sha256(text.encode()).hexdigest();old=self.admitted(event)
                if old and old['text_hash']!=fingerprint:raise ExecutionRejected('原来源正文已冻结，未重新受理不同正文。')
                if old and old['receipt_json']:return json.loads(old['receipt_json'])
                with self.bridge.store._connect() as db:
                    db.execute('INSERT OR IGNORE INTO studio_ingress_events VALUES(?,?,?,?,NULL,NULL,?)',
                        (eid,event['sender_id'],event['chat_id'],fingerprint,time.time()))
                if text.strip().lower() in ('/new','/whoami','/cancel') or text.strip() in ('取消当前任务','停止当前任务'):
                    receipt=self.call('command',{**base,'source_id':eid,'text':text})
                    if not isinstance(receipt.get('text'),str):raise ProtocolError('studio_admission_response_unknown',uncertain=True)
                elif event.get('native_card_id'):
                    receipt=self.call('control',{**base,'job_id':self.job_from_card(event),'source_id':eid,'source_event_id':eid,'text':text})
                else:receipt=self.call('ingest',{**base,'source_id':source,'source_event_id':eid,'text':text})
                if 'text' not in receipt:
                    identifier(receipt.get('job_id'));identifier(receipt.get('conversation_id'))
                with self.bridge.store._connect() as db:
                    db.execute('UPDATE studio_ingress_events SET receipt_json=?,updated=? WHERE event=? AND user=? AND chat=?',
                        (json.dumps(receipt,ensure_ascii=False),time.time(),eid,event['sender_id'],event['chat_id']))
                return receipt
        except ProtocolError as error:
            if error.uncertain:raise ExecutionUnknown('工作台受理回执尚未确认，原来源保持同一幂等标识。') from None
            raise ExecutionRejected('工作台入口暂未完成请求：'+error.code) from None

    def forward(self,event,text):
        receipt=self.admit(event,text)
        if 'text' in receipt:return receipt['text']
        base=self.member_body(event['sender_id'],event['chat_id'])
        try:
            deadline=time.monotonic()+self.wait_seconds
            while True:
                job=self.call('status',{**base,'job_id':receipt['job_id']})
                if job['state'] in WAITING or job['state'] in ('succeeded','cancelled'):
                    return job.get('notice') or ('已停止原任务，已发生的操作保留回执。' if job['state']=='cancelled' else '原任务已受理，请在工作台查看当前状态。')
                if job['state']=='execution_unknown':raise ExecutionUnknown('原工作台任务结果待核验，未重复执行。')
                if job['state']=='failed':raise ExecutionRejected(job.get('notice') or '工作台原任务未完成，请核验原回执。')
                if self.bridge.stop_event.wait(.25) or time.monotonic()>=deadline:
                    raise ExecutionUnknown('原工作台任务回执仍在等待，已保存同一任务标识，不重开执行。')
        except ProtocolError as error:
            if error.uncertain:raise ExecutionUnknown('工作台传输回执尚未确认，原消息保持待核验。') from None
            raise ExecutionRejected('工作台入口暂未完成请求：'+error.code) from None

    def resume_native_auth(self,user,pending,flow):
        if not pending.get('event_id','').startswith('studio-job:'):return False
        jid=identifier(pending['event_id'][len('studio-job:'):])
        base=self.member_body(user,pending['chat_id'])
        if flow['status']=='ready' and not self.bridge.isolated_handler.native.contexts[user].actor():return False
        try:
            self.call('resume-auth',{**base,'job_id':jid,'status':flow['status']})
            return True
        except ProtocolError as error:
            # A cancelled/deleted/already finished task is acknowledged, never resurrected.
            if not error.uncertain and error.status in (404,409):return True
            return False
