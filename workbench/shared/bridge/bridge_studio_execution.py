"""Trusted studio execution port into the EXISTING Bridge engines.

This creates neither a platform consumer nor a second member CLI/credential
owner. The Bridge's NativeFeishuAgent, Seatbelt engine and original sender stay
the sole implementation of those capabilities.
"""
from __future__ import annotations
import contextlib
import hashlib
import importlib.util
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from conversation_protocol import ProtocolError, canonical, identifier, read_key, verify, headers_for, request as studio_request, MAX_PACKET
except ModuleNotFoundError:
    # Desktop source uses the canonical stdlib leaf in backend/. Deployment
    # installs that exact leaf next to bridge.py (byte identity is verified).
    leaf = Path(__file__).resolve().parents[2]/'backend/conversation_protocol.py'
    spec = importlib.util.spec_from_file_location('studio_conversation_protocol', leaf)
    protocol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protocol)
    ProtocolError, canonical, identifier = protocol.ProtocolError, protocol.canonical, protocol.identifier
    read_key, verify, headers_for, MAX_PACKET = protocol.read_key, protocol.verify, protocol.headers_for, protocol.MAX_PACKET
    studio_request=protocol.request

from bridge_cherry import ExecutionRejected, ExecutionUnknown
from bridge_native_feishu import NativeAuthorizationRequired, NATIVE_PROMPT
from bridge_utils import parse_auth_scopes

STATE_MAP = {'done': 'succeeded', 'cancelled': 'cancelled', 'cancel_requested': 'cancelled',
             'waiting_confirmation': 'waiting_confirmation', 'waiting_auth': 'waiting_authorization',
             'waiting_input': 'waiting_input', 'paused': 'paused', 'unknown': 'execution_unknown'}


class StudioExecution:
    def __init__(self, bridge, members, lease_checker, recover_running=True):
        if not callable(lease_checker):
            raise ProtocolError('studio_lease_adapter_required', 503)
        self.bridge, self.lease_checker = bridge, lease_checker
        self.members = {m['principal_id']: dict(m) for m in members}
        if len(self.members) != len(members):
            raise ProtocolError('studio_member_binding_invalid', 503)
        self.lock = threading.RLock()
        self.active = {}
        self.auth_workers = {}
        with bridge.store._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS studio_execution(
                job TEXT PRIMARY KEY,owner TEXT NOT NULL,conversation TEXT NOT NULL,
                attempt TEXT NOT NULL,request_hash TEXT NOT NULL,state TEXT NOT NULL,
                input_json TEXT NOT NULL,result_json TEXT,updated REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS studio_execution_events(
                job TEXT PRIMARY KEY,owner TEXT NOT NULL,conversation TEXT NOT NULL,
                event_json TEXT NOT NULL,text TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS studio_trusted_auth(
                job TEXT PRIMARY KEY,auth_id TEXT NOT NULL,status TEXT NOT NULL,
                request_json TEXT NOT NULL)''')
            db.execute('CREATE TABLE IF NOT EXISTS studio_trusted_sessions(job TEXT PRIMARY KEY,session TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS studio_trusted_inputs(job TEXT PRIMARY KEY,prepared_text TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS studio_trusted_auth_deliveries(job TEXT PRIMARY KEY,auth_id TEXT NOT NULL)')
            # A durable 'running' row without this process's worker is unknown,
            # never evidence that another model/tool invocation should start.
            if recover_running:
                db.execute("UPDATE studio_execution SET state='execution_unknown',updated=? WHERE state='running'", (time.time(),))

    def recover_running(self):
        with self.lock, self.bridge.store._connect() as db:
            if self.active:
                raise ProtocolError('studio_execution_workers_still_active', 409)
            db.execute("UPDATE studio_execution SET state='execution_unknown',updated=? WHERE state='running'", (time.time(),))

    def member(self, owner):
        identifier(owner)
        member = self.members.get(owner)
        if not member or not member.get('active', True):
            raise ProtocolError('studio_member_not_allowed', 403)
        user, chat = member['open_id'], member['chat_id']
        isolated = user in self.bridge.isolated_users
        if isolated:
            if not self.bridge.isolated_handler or self.bridge.isolated_private_chats.get(chat) != user:
                raise ProtocolError('studio_private_chat_binding_required', 403)
            native=self.bridge.isolated_handler.native
            if not native or user not in native.contexts:raise ProtocolError('studio_member_engine_not_registered',403)
        elif user not in self.bridge.allowed_senders or chat not in self.bridge.allowed_chats or self.bridge.config.get('chat_types', {}).get(chat) != 'p2p':
            raise ProtocolError('studio_member_engine_not_registered', 403)
        return member, isolated

    def validate(self, body):
        allowed = {'job_id', 'attempt_id', 'owner', 'conversation_id', 'channel', 'source_event_id', 'history', 'control', 'previous_attempt_id'}
        required = {'job_id', 'attempt_id', 'owner', 'conversation_id', 'channel', 'history'}
        if not isinstance(body, dict) or set(body)-allowed or not required <= set(body):
            raise ProtocolError('studio_execution_input_invalid', 400)
        for key in ('job_id', 'attempt_id', 'conversation_id'):
            identifier(body[key])
        member, isolated = self.member(body['owner'])
        if body['channel'] not in ('app', 'web', 'feishu'):
            raise ProtocolError('studio_execution_input_invalid', 400)
        history = body['history']
        if not isinstance(history, list) or not history or len(history) > 10000:
            raise ProtocolError('studio_execution_input_invalid', 400)
        for message in history:
            if not isinstance(message, dict) or set(message) != {'role', 'content'} or message['role'] not in ('user', 'assistant') or not isinstance(message['content'], str):
                raise ProtocolError('studio_execution_input_invalid', 400)
        if history[-1]['role'] != 'user' or not history[-1]['content'].strip():
            raise ProtocolError('studio_execution_input_invalid', 400)
        control = body.get('control')
        if control is not None:
            if not isinstance(control, dict) or set(control)-{'text', 'source_event_id', 'kind', 'channel'} or not {'text', 'kind'} <= set(control) or control['kind'] not in ('reply', 'continue', 'authorization') or control.get('channel', body['channel']) not in ('app','web','feishu') or not isinstance(control['text'], str) or not control['text'].strip() or len(control['text']) > 16000:
                raise ProtocolError('studio_execution_control_invalid', 400)
            identifier(body.get('previous_attempt_id'))
        return member, isolated

    def event(self, body, member):
        if body.get('control',{}).get('kind')=='authorization':
            with self.bridge.store._connect() as db:
                row=db.execute('''SELECT e.event_json,e.text FROM studio_execution_events e
                    JOIN studio_execution x ON x.job=e.job WHERE e.job=? AND e.owner=? AND e.conversation=?
                    AND x.attempt=? AND x.state='waiting_authorization' ''',
                    (body['job_id'],body['owner'],body['conversation_id'],body['previous_attempt_id'])).fetchone()
            if not row or row['text']!=body['control']['text']:
                raise ProtocolError('studio_authorization_resume_binding_invalid',409)
            return json.loads(row['event_json']),row['text']
        text = body['control']['text'] if body.get('control') else body['history'][-1]['content']
        source = body['control'].get('source_event_id') if body.get('control') else body.get('source_event_id')
        channel = body.get('control',{}).get('channel',body['channel'])
        if source:
            if not isinstance(source, str) or len(source) > 160:
                raise ProtocolError('studio_source_event_invalid', 400)
            with self.bridge.store._connect() as db:
                row = db.execute('SELECT payload_json FROM events WHERE event_id=?', (source,)).fetchone()
            if not row:
                raise ProtocolError('studio_source_event_not_found', 404)
            event = json.loads(row[0])
            if (event.get('sender_id'), event.get('chat_id'), event.get('chat_type')) != (member['open_id'], member['chat_id'], 'p2p'):
                raise ProtocolError('studio_source_member_mismatch', 403)
            if event.get('conversation_id') not in (None, body['conversation_id']):
                raise ProtocolError('studio_source_conversation_mismatch', 403)
            if event.get('native_card_id'):
                cards = self.bridge.native_interactions
                card = cards.get(event['native_card_id']) if cards else None
                if not card or (card['user'], card['chat'], card['message']) != (member['open_id'], member['chat_id'], event.get('message_id')):
                    raise ProtocolError('studio_card_binding_invalid', 403)
                if json.loads(card['payload']).get('conversation_id') != body['conversation_id']:
                    raise ProtocolError('studio_card_binding_invalid', 403)
                cli = self.bridge.isolated_handler.native.contexts[member['open_id']]
                with cli.connect() as db:
                    task_row = db.execute('SELECT payload FROM native_tasks WHERE id=?', (card['task'],)).fetchone()
                if not task_row or json.loads(task_row[0]).get('event_id') != 'studio-job:'+body['job_id']:
                    raise ProtocolError('studio_card_binding_invalid', 403)
            elif not body.get('control'):
                # Transport text may have a mention stripped by the existing
                # Bridge, so event content is retained for attachment parsing.
                event['source_event_id'] = source
        elif channel == 'feishu':
            raise ProtocolError('studio_source_event_required', 400)
        else:
            # A signed app request is NOT presented as a received Feishu message.
            event = {'chat_id': member['chat_id'], 'sender_id': member['open_id'],
                     'chat_type': 'p2p', 'message_type': 'text', 'content': text,
                     'studio_channel': channel}
        if not body.get('control') or not event.get('native_card_id'):
            event['event_id'] = 'studio-job:'+body['job_id'] if not body.get('control') else 'studio-control:'+body['attempt_id']
        event['conversation_id'] = body['conversation_id']
        return event, text

    def execute(self, body):
        member, isolated = self.validate(body)
        event, text = self.event(body, member)
        request_hash = hashlib.sha256(canonical(body)).hexdigest()
        with self.lock, self.bridge.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM studio_execution WHERE job=?', (body['job_id'],)).fetchone()
            if old:
                if (old['owner'], old['conversation']) != (body['owner'], body['conversation_id']):
                    raise ProtocolError('studio_execution_binding_conflict', 409)
                if old['attempt'] == body['attempt_id']:
                    if old['request_hash'] != request_hash:
                        raise ProtocolError('studio_execution_binding_conflict', 409)
                    return json.loads(old['result_json']) if old['result_json'] else self.status({name: body[name] for name in ('job_id', 'attempt_id', 'owner')})
                if old['state'] not in ('waiting_confirmation', 'waiting_authorization', 'waiting_input', 'paused') or not body.get('control') or old['attempt'] != body.get('previous_attempt_id'):
                    raise ProtocolError('studio_execution_requires_original_outcome', 409)
                original = json.loads(old['input_json'])
                if original['history'] != body['history']:
                    raise ProtocolError('studio_execution_binding_conflict', 409)
                db.execute('UPDATE studio_execution SET attempt=?,request_hash=?,state=?,input_json=?,result_json=NULL,updated=? WHERE job=?',
                           (body['attempt_id'], request_hash, 'running', canonical(body).decode(), time.time(), body['job_id']))
            else:
                if body.get('control'):
                    raise ProtocolError('studio_execution_control_without_task', 409)
                db.execute('INSERT INTO studio_execution VALUES(?,?,?,?,?,?,?,?,?)',
                           (body['job_id'], body['owner'], body['conversation_id'], body['attempt_id'], request_hash, 'running', canonical(body).decode(), None, time.time()))
            db.execute('INSERT OR REPLACE INTO studio_execution_events VALUES(?,?,?,?,?)',
                       (body['job_id'],body['owner'],body['conversation_id'],canonical(event).decode(),text))
            db.commit()
            self.active[(body['job_id'], body['attempt_id'])] = threading.current_thread()
        started = False
        try:
            user_lock = self.bridge.isolated_handler.locks[member['open_id']] if isolated else contextlib.nullcontext()
            with user_lock:
                if not self.bridge._execution_allowed(force=True) or self.bridge.native_channel_ok is not True:
                    raise ProtocolError('studio_execution_guard_not_ready', 503)
                if not self.lease_checker(body):
                    raise ProtocolError('studio_execution_lease_expired', 409)
                with self.bridge._idle_sleep_assertion() as held:
                    if not held:
                        raise ProtocolError('studio_execution_guard_not_ready', 503)
                    started = True
                    result = self.native(body, member, event, text) if isolated else self.trusted(body, member, event, text)
        except NativeAuthorizationRequired as error:
            result = {'state': 'waiting_authorization', 'text': '请在本人飞书私聊完成工具授权；原任务和进度保留。', 'effect_state': self.effect(member,event)}
            try:self.bridge._send_native_authorization(member['chat_id'], member['open_id'], error.flow)
            except Exception:result['error_code']='studio_authorization_delivery_pending'
        except ExecutionUnknown:
            result = {'state': 'execution_unknown', 'text': '原执行结果待核验，未重复执行。', 'effect_state': 'unknown'}
        except ProtocolError as error:
            result = {'state': 'failed', 'text': '执行入口尚未就绪，原请求已保留。', 'error_code': error.code, 'execution_started': started, 'effect_state': 'unknown' if started else 'none'}
        except ExecutionRejected:
            result = {'state': 'failed', 'text': '成员引擎未完成本次请求，原检查点已保留。', 'error_code': 'studio_member_execution_rejected', 'effect_state': 'unknown' if started else 'none'}
        except Exception:
            result = {'state': 'execution_unknown' if started else 'failed', 'text': '执行回执尚未完整确认，请核验原任务。', 'error_code': 'studio_execution_failed', 'effect_state': 'unknown' if started else 'none'}
        result.update(job_id=body['job_id'], attempt_id=body['attempt_id'])
        try:
            with self.lock, self.bridge.store._connect() as db:
                db.execute('UPDATE studio_execution SET state=?,result_json=?,updated=? WHERE job=? AND attempt=?',
                           (result['state'], canonical(result).decode(), time.time(), body['job_id'], body['attempt_id']))
        finally:
            with self.lock:self.active.pop((body['job_id'], body['attempt_id']), None)
        return result

    def native(self, body, member, event, text):
        native = self.bridge.isolated_handler.native
        if not native or member['open_id'] not in native.contexts:
            raise ProtocolError('studio_member_engine_not_registered', 403)
        history = [*body['history']]
        if body.get('control'):
            history.append({'role': 'user', 'content': text})
        answer = native.run(member['open_id'], event, [{'role': 'system', 'content': NATIVE_PROMPT}, *history])
        cli = native.contexts[member['open_id']]
        with cli.connect() as db:
            row = db.execute('''SELECT t.payload FROM native_tasks t JOIN native_task_events e ON e.task_id=t.id WHERE e.event_id=?''', (event['event_id'],)).fetchone()
        task = json.loads(row[0]) if row else None
        if not task and text.strip() in ('/授权', '/cancel', '取消当前任务', '停止当前任务'):
            return {'state': 'succeeded', 'text': answer, 'effect_state': 'none', 'runtime': {'engine': 'native-feishu', 'sandbox': 'application-boundary'}}
        if not task or task.get('conversation_id') != body['conversation_id']:
            raise ProtocolError('studio_native_task_binding_invalid', 409)
        pending = self.bridge.native_interactions.for_task(native, event) if self.bridge.native_interactions else None
        if pending:
            self.bridge.native_interactions.send(self.bridge.sender, pending)
        effect = self.effect(member,event)
        interaction=None
        if task['state']=='waiting_input':
            q=task['question'];interaction={'kind':'question','question':q['question'],'options':q['options']}
        elif task['state']=='waiting_confirmation':
            from bridge_interactions import human_args,digest
            with cli.connect() as db:
                action=db.execute('SELECT args FROM actions WHERE id=? AND chat_id=? AND state=?',(task['approval_id'],event['chat_id'],'pending')).fetchone()
            if action:
                args=json.loads(action[0]);interaction={'kind':'approval','approval_id':task['approval_id'],'text':human_args(args),'args_hash':digest(args)}
        return {'state': STATE_MAP.get(task['state'], 'execution_unknown'), 'text': answer,
                'effect_state': 'unknown' if task['state']=='unknown' else effect,
                **({'interaction':interaction} if interaction else {}),
                'runtime': {'engine': (task.get('engine') or {}).get('type', 'native-feishu'),
                            'sandbox': (task.get('engine') or {}).get('sandbox', 'application-boundary'), 'task_id': task['id']}}

    def effect(self,member,event):
        native=self.bridge.isolated_handler.native
        if not native or member['open_id'] not in native.contexts:return 'not_evaluated'
        with native.contexts[member['open_id']].connect() as db:
            actions={r[0] for r in db.execute('''SELECT a.state FROM actions a
                JOIN native_task_events e ON e.event_id=a.event_id WHERE e.task_id=(
                SELECT task_id FROM native_task_events WHERE event_id=?)''',(event['event_id'],))}
        return 'unknown' if actions & {'unknown','executing'} else 'committed' if 'done' in actions else 'pending_confirmation' if 'pending' in actions else 'none'

    def trusted(self, body, member, event, text):
        if body.get('control'):
            if body['control']['kind']!='authorization':raise ProtocolError('studio_trusted_control_invalid',409)
            with self.bridge.store._connect() as db:
                auth=db.execute('SELECT status FROM studio_trusted_auth WHERE job=?',(body['job_id'],)).fetchone()
            if not auth or auth[0]!='ready':raise ProtocolError('studio_trusted_authorization_pending',409)
        with self.bridge.store._connect() as db:
            prepared=db.execute('SELECT prepared_text FROM studio_trusted_inputs WHERE job=?',(body['job_id'],)).fetchone()
        if prepared:prepared_text=prepared[0]
        else:
            prepared_text=text
            adapter=getattr(self.bridge,'input_adapter',None)
            if adapter and body['channel']=='feishu':prepared_text=adapter.prepare(event,text).text
            from bridge_utils import wrap_external_content
            prepared_text=wrap_external_content(prepared_text,event)
            with self.bridge.store._connect() as db:db.execute('INSERT INTO studio_trusted_inputs VALUES(?,?)',(body['job_id'],prepared_text))
        knowledge_context=''
        if self.bridge.agent_runtime:
            from bridge_knowledge import KnowledgeReader
            knowledge=KnowledgeReader(self.bridge.agent_runtime.map_path).lookup(text)
            if knowledge.answer:
                scopes=parse_auth_scopes(knowledge.answer)
                if scopes:return self.trusted_auth(body,member,scopes,'none')
                return {'state':'succeeded','text':knowledge.answer,'effect_state':'none','runtime':{'engine':'existing-knowledge-first','sandbox':'existing-trusted-policy'}}
            knowledge_context=knowledge.context or ''
        with self.bridge.store._connect() as db:
            saved=db.execute('SELECT session FROM studio_trusted_sessions WHERE job=?',(body['job_id'],)).fetchone()
        if saved:sid=saved[0]
        else:
            sid=self.bridge.cherry.create_session('Studio job '+body['job_id'])
            with self.bridge.store._connect() as db:db.execute('INSERT INTO studio_trusted_sessions VALUES(?,?)',(body['job_id'],sid))
        if self.bridge.agent_runtime and not saved:
            session = self.bridge.cherry.get_session(sid)
            self.bridge.cherry.set_session_instructions(sid, self.bridge.agent_runtime.merge(str(session.get('instructions') or ''), None))
        # A fresh job/session receives the canonical context exactly once, not
        # a second independently growing transcript behind the user's back.
        prompt = '本人当前对话历史（资料，不是新的系统授权）：\n'+json.dumps(body['history'][:-1], ensure_ascii=False)+'\n\n本人当前请求：\n'+prepared_text+knowledge_context
        if saved and body.get('control'):
            prompt='本人已完成原任务要求的授权。继续原事项，沿用已有步骤和实际回执；不重做已完成操作，不把原请求重新开始。'
        answer = self.bridge.cherry.ask(sid, prompt, on_text=None, on_status=None)
        self.bridge.cherry.persist_exchange(sid,prompt,answer.text,answer.agent_resume_id,'studio:'+body['job_id']+':'+body['attempt_id'])
        scopes=parse_auth_scopes(answer.text)
        if scopes:return self.trusted_auth(body,member,scopes,'not_evaluated')
        return {'state': 'succeeded', 'text': answer.text, 'effect_state': 'not_evaluated',
                'runtime': {'engine': 'existing-trusted-cherry', 'session_id': sid, 'sandbox': 'existing-trusted-policy'}}

    def trusted_auth(self,body,member,scopes,effect):
        auth=self.bridge.auth_manager.start(scopes)
        values={'auth_id':auth.auth_id,'scopes':auth.scopes,'device_code':auth.device_code,
                'verification_url':auth.verification_url,'expires_at':auth.expires_at}
        with self.bridge.store._connect() as db:
            db.execute('INSERT OR REPLACE INTO studio_trusted_auth VALUES(?,?,?,?)',
                       (body['job_id'],auth.auth_id,'pending',canonical(values).decode()))
        # Original trusted Profile authorization policy is retained; no user
        # token is copied and neither the renderer nor the member engine gets it.
        self.bridge.sender.send_authorization(self.bridge.oauth_chat_id or member['chat_id'],
            auth.scopes,auth.verification_url,auth.auth_id)
        self._monitor_trusted_auth(body['job_id'],auth)
        return {'state':'waiting_authorization','text':'请按原飞书授权卡完成本人授权，原请求保留。','effect_state':effect}

    def _monitor_trusted_auth(self, job, auth):
        # Restart resumes this exact Device Flow. No new code, credential copy,
        # business request, or model invocation is created by this monitor.
        with self.lock:
            worker=self.auth_workers.get(job)
            if worker and worker.is_alive():return
            def finish():
                status='failed'
                try:
                    if auth.expires_at>time.time():
                        try:self.bridge.auth_manager.complete(auth);status='ready'
                        except Exception:pass
                    with self.bridge.store._connect() as db:
                        db.execute('UPDATE studio_trusted_auth SET status=? WHERE job=? AND auth_id=?',
                            (status,job,auth.auth_id))
                    self._deliver_trusted_auth(job)
                finally:
                    with self.lock:self.auth_workers.pop(job,None)
            worker=threading.Thread(target=finish,name='studio-trusted-auth',daemon=True)
            self.auth_workers[job]=worker;worker.start()

    def _deliver_trusted_auth(self, job):
        # A failed/late signal stays durable for the existing Bridge retry loop.
        # Holding this lock also serializes cancellation against signal delivery.
        with self.lock, self.bridge.store._connect() as db:
            row=db.execute('''SELECT a.*,x.owner,x.state execution_state FROM studio_trusted_auth a
                JOIN studio_execution x ON x.job=a.job WHERE a.job=?''',(job,)).fetchone()
            if not row or row['status'] not in ('ready','failed') or row['execution_state'] not in ('running','waiting_authorization'):return
            delivered=db.execute('SELECT auth_id FROM studio_trusted_auth_deliveries WHERE job=?',(job,)).fetchone()
            if delivered and delivered[0]==row['auth_id']:return
            member,isolated=self.member(row['owner'])
            if isolated:raise ProtocolError('studio_trusted_member_binding_invalid',403)
            gateway=getattr(self.bridge,'studio_gateway',None)
            if not gateway:return
            try:
                gateway.call('resume-auth',{**gateway.member_body(member['open_id'],member['chat_id']),
                    'job_id':job,'status':row['status']})
            except ProtocolError:
                return
            db.execute('INSERT OR REPLACE INTO studio_trusted_auth_deliveries VALUES(?,?)',(job,row['auth_id']))

    def poll_authorizations(self):
        from bridge_lark_auth import AuthRequest
        with self.bridge.store._connect() as db:
            rows=db.execute('''SELECT a.*,x.owner FROM studio_trusted_auth a JOIN studio_execution x ON x.job=a.job
                WHERE x.state IN ('running','waiting_authorization')''').fetchall()
        for row in rows:
            member,isolated=self.member(row['owner'])
            if isolated:continue
            if row['status']=='pending':
                self._monitor_trusted_auth(row['job'],AuthRequest(**json.loads(row['request_json'])))
            else:self._deliver_trusted_auth(row['job'])

    def status(self, body):
        if not isinstance(body, dict) or set(body) != {'job_id', 'attempt_id', 'owner'}:
            raise ProtocolError('studio_execution_input_invalid', 400)
        for name in ('job_id', 'attempt_id', 'owner'):
            identifier(body[name])
        self.member(body['owner'])
        with self.lock, self.bridge.store._connect() as db:
            row = db.execute('SELECT * FROM studio_execution WHERE job=? AND owner=? AND attempt=?',
                             (body['job_id'], body['owner'], body['attempt_id'])).fetchone()
            if not row:
                raise ProtocolError('studio_execution_not_found', 404)
            if row['result_json']:
                return json.loads(row['result_json'])
            worker = self.active.get((body['job_id'], body['attempt_id']))
            alive = bool(worker and worker.is_alive())
            return {'job_id': body['job_id'], 'attempt_id': body['attempt_id'],
                    'state': 'execution_unknown' if row['state']=='running' and not alive else row['state'],
                    'worker_alive': alive}

    def cancel(self, body):
        if not isinstance(body, dict) or set(body) != {'job_id', 'owner'}:
            raise ProtocolError('studio_execution_input_invalid', 400)
        identifier(body['job_id'])
        member, isolated = self.member(body['owner'])
        with self.lock, self.bridge.store._connect() as db:
            row = db.execute('SELECT * FROM studio_execution WHERE job=? AND owner=?', (body['job_id'], body['owner'])).fetchone()
            if row and not isolated and row['state'] in ('waiting_authorization','cancelled'):
                result={'job_id':body['job_id'],'attempt_id':row['attempt'],'state':'cancelled',
                    'text':'已停止原任务的授权等待，历史和已发生的操作保留。','effect_state':'not_evaluated'}
                db.execute('UPDATE studio_execution SET state=?,result_json=?,updated=? WHERE job=?',
                    ('cancelled',canonical(result).decode(),time.time(),body['job_id']))
                return {'job_id':body['job_id'],'state':'cancelled','accepted':True}
        if not row:
            raise ProtocolError('studio_execution_not_found', 404)
        if not isolated:
            # The existing Cherry API has no verified scoped cancellation here;
            # do not invent a successful stop or kill an unrelated process.
            return {'job_id': body['job_id'], 'state': 'cancel_requested', 'accepted': False}
        accepted = self.bridge.isolated_handler.native.cancel(member['open_id'], member['chat_id'], row['conversation'])
        return {'job_id': body['job_id'], 'state': 'cancel_requested', 'accepted': bool(accepted)}


def server_for(execution, key_file, port):
    """An opt-in Bridge-owned loopback listener, not a second event consumer."""
    if type(port) is not int or port != 0 and not 1024 <= port <= 65535:
        raise ProtocolError('studio_loopback_port_invalid', 503)
    read_key(key_file)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            pass

        def reply(self, status, packet):
            raw = canonical(packet)
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            nonce = self.headers.get('X-Studio-Nonce', '')
            if re.fullmatch(r'[a-f0-9]{32}', nonce):
                for name, value in headers_for(read_key(key_file), 'RESPONSE', self.path, raw, nonce=nonce).items():
                    if name.startswith('X-Studio-'):self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            executing = False
            try:
                if self.client_address[0] != '127.0.0.1' or self.headers.get('Host') != '127.0.0.1:'+str(self.server.server_port):
                    raise ProtocolError('studio_internal_origin_denied', 403)
                routes = {'/internal/studio/execute': execution.execute,
                          '/internal/studio/status': execution.status,
                          '/internal/studio/cancel': execution.cancel}
                if self.path not in routes:
                    raise ProtocolError('studio_internal_route_invalid', 404)
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ProtocolError('studio_packet_invalid', 400)
                try:size=int(self.headers.get('Content-Length', '0'))
                except ValueError:raise ProtocolError('studio_packet_invalid', 400)
                if not 0 < size <= MAX_PACKET:
                    raise ProtocolError('studio_packet_too_large', 413)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ProtocolError('studio_packet_invalid', 400)
                verify(read_key(key_file), 'POST', self.path, raw, self.headers)
                try:body=json.loads(raw)
                except ValueError:raise ProtocolError('studio_packet_invalid', 400)
                executing = self.path == '/internal/studio/execute'
                self.reply(200, routes[self.path](body))
            except ProtocolError as error:
                self.reply(error.status or 400, {'code': error.code, 'execution_started': False})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            except Exception:
                self.reply(500, {'code': 'studio_execution_response_unknown', 'execution_started': executing})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server
