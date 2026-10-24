"""Gateway job lifecycle into a persisted execution owner: existing GLM/Bridge for new chat turns, native Codex only for persisted legacy executions.

The browser never receives the machine key or chooses an engine/profile. A
persisted attempt is submitted once; transport uncertainty is never retried as
a fresh execution. Controls resume the same job, with its original context.
"""
import json
import uuid
from conversation_protocol import ProtocolError, canonical, endpoint, read_key, request
from domain import Problem, shape, stamp

WAITING = ('waiting_input', 'waiting_confirmation', 'waiting_authorization', 'paused')
OUTCOMES = ('succeeded', 'failed', 'cancelled', 'execution_unknown', *WAITING)


class ExecutionAdapter:
    def __init__(self, service, config):
        shape(config, ['bridge_origin', 'key_file', 'timeout_seconds'], ['bridge_origin', 'key_file'])
        self.s = service
        self.origin = endpoint(config['bridge_origin'])
        self.key_file = config['key_file']
        read_key(self.key_file)
        local=service.config.get('local_codex') or {}
        self.local=None
        if local.get('enabled'):
            from local_codex import LocalCodex
            self.local=LocalCodex(service.root,local)
        self.timeout = config.get('timeout_seconds', 660)
        if type(self.timeout) is not int or not 30 <= self.timeout <= 1830:
            raise ValueError('studio_execution_timeout_invalid')
        with service.transaction():
            service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_execution(
                job TEXT PRIMARY KEY REFERENCES jobs(id),attempt TEXT NOT NULL,
                request_json TEXT NOT NULL,result_json TEXT,state TEXT NOT NULL,
                pending_control TEXT,updated TEXT NOT NULL)''')
            service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_source_events(
                owner TEXT NOT NULL,channel TEXT NOT NULL,source_id TEXT NOT NULL,event_id TEXT NOT NULL,
                PRIMARY KEY(owner,channel,source_id))''')
            service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_controls(
                owner TEXT NOT NULL,channel TEXT NOT NULL,source_id TEXT NOT NULL,
                job TEXT NOT NULL REFERENCES jobs(id),text TEXT NOT NULL,
                event_id TEXT,created TEXT NOT NULL,PRIMARY KEY(owner,channel,source_id))''')

    def attach_source(self, owner, channel, source_id, event_id):
        if channel != 'feishu' or not isinstance(event_id, str) or not 1 <= len(event_id) <= 160:
            raise Problem(400, 'invalid_request')
        old = self.s.one('SELECT event_id FROM conversation_source_events WHERE owner=? AND channel=? AND source_id=?',
                         (owner, channel, source_id))
        if old and old['event_id'] != event_id:
            raise Problem(409, 'idempotency_conflict')
        self.s.db.execute('INSERT OR IGNORE INTO conversation_source_events VALUES(?,?,?,?)',
                          (owner, channel, source_id, event_id))

    def is_local(self,job):
        row=self.s.one('SELECT request_json FROM conversation_execution WHERE job=?',(job['id'],))
        if row:return json.loads(row['request_json']).get('engine')=='codex-cli'
        # The video production module owns new Codex executions. A chat channel
        # is not a video task; only persisted legacy jobs may retain that engine.
        return False

    def local_outcome(self,job,body,history):
        from local_codex import CodexError
        if not self.local:raise ProtocolError('codex_route_not_configured')
        work=self.local.workspace(job['owner'],job['conversation'])
        prompt=json.dumps({'conversation_history':history,'current_request':history[-1] if history else {},'control':body.get('control')},ensure_ascii=False)
        try:
            value=self.local.run(work,prompt,job['id'],cancelled=lambda:self.s.stop.is_set() or self.s.check_job(job['owner'],job['id'])['state']=='cancel_requested',attempt=body['attempt_id'])
            result={'job_id':job['id'],'attempt_id':body['attempt_id'],'state':value['state'],'effect_state':'not_evaluated','text':value['text'],'engine':'codex-cli','model':value.get('model'),'artifacts':value['artifacts']}
            if value.get('artifacts'):
                import urllib.parse
                origin=next((x for x in self.s.config.get('allowed_origins',[]) if x.startswith('https://studio.')),None)
                if origin:result['text']+='\n\n'+ '\n'.join('[产物 '+str(i+1)+']('+origin+'/v1/codex/'+job['conversation']+'/files/'+urllib.parse.quote(name,safe='')+')' for i,name in enumerate(value['artifacts']))
            if value['state']=='waiting_input':result['interaction']={'kind':'input','question':value['text']}
            return result
        except CodexError as error:
            if self.s.check_job(job['owner'],job['id'])['state']=='cancel_requested':
                return {'job_id':job['id'],'attempt_id':body['attempt_id'],'state':'cancelled','effect_state':'not_evaluated','text':'已停止本机Codex后续执行，已产生的工程文件保留。','engine':'codex-cli'}
            return {'job_id':job['id'],'attempt_id':body['attempt_id'],'state':'execution_unknown' if error.uncertain else 'failed','effect_state':'unknown' if error.uncertain else 'none','text':'本机Codex执行未返回完整回执，已保留原任务与工程；先核验，不重新创建执行。','error_code':error.code,'engine':'codex-cli'}

    def execute(self, job, history):
        s = self.s
        with s.transaction():
            old = s.one('SELECT * FROM conversation_execution WHERE job=?', (job['id'],))
            if old:
                if not old['pending_control'] or old['state'] not in WAITING:
                    # Re-opening a row is not permission to replay a lost request.
                    raise Problem(409, 'studio_original_execution_required')
                previous = json.loads(old['request_json'])
                body = {**previous, 'previous_attempt_id': old['attempt'],
                        'attempt_id': str(uuid.uuid4()), 'control': json.loads(old['pending_control'])}
            else:
                turn = s.one('SELECT * FROM conversation_turns WHERE question=? AND owner=?', (job['message_id'], job['owner']))
                if not turn:
                    raise Problem(409, 'studio_source_binding_required')
                body = {'owner': job['owner'], 'job_id': job['id'], 'conversation_id': job['conversation'],
                        'attempt_id': str(uuid.uuid4()), 'channel': turn['channel'], 'history': history}
                source = s.one('SELECT event_id FROM conversation_source_events WHERE owner=? AND channel=? AND source_id=?',
                               (job['owner'], turn['channel'], turn['source_id']))
                if source:
                    body['source_event_id'] = source['event_id']
            if not old and self.is_local(job):body['engine']='codex-cli'
            s.db.execute('INSERT OR REPLACE INTO conversation_execution VALUES(?,?,?,NULL,?,NULL,?)',
                          (job['id'], body['attempt_id'], canonical(body).decode(), 'running', stamp()))
        try:
            packet = self.local_outcome(job,body,history) if body.get('engine')=='codex-cli' else request(self.origin, '/internal/studio/execute', body, self.key_file, self.timeout)
            result = self.validate_result(job, body['attempt_id'], packet)
        except ProtocolError as error:
            result = {'job_id': job['id'], 'attempt_id': body['attempt_id'],
                      'state': 'execution_unknown' if error.uncertain else 'failed',
                      'effect_state': 'unknown' if error.uncertain else 'none', 'error_code': error.code,
                      'text': '原执行回执尚未确认；已保留原任务，不创建新执行重试。' if error.uncertain else '执行入口暂未就绪，原请求保留。'}
        with s.transaction():
            s.db.execute('UPDATE conversation_execution SET result_json=?,state=?,updated=? WHERE job=? AND attempt=?',
                          (canonical(result).decode(), result['state'], stamp(), job['id'], body['attempt_id']))
        return result

    @staticmethod
    def validate_result(job, attempt, packet):
        if packet.get('job_id') != job['id'] or packet.get('attempt_id') != attempt or packet.get('state') not in OUTCOMES:
            raise ProtocolError('studio_execution_response_unknown', uncertain=True)
        text = packet.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            raise ProtocolError('studio_execution_response_unknown', uncertain=True)
        if packet.get('effect_state') not in ('none', 'pending_confirmation', 'committed', 'unknown', 'not_evaluated'):
            raise ProtocolError('studio_execution_response_unknown', uncertain=True)
        return packet

    def lease(self, body):
        shape(body, ['owner', 'job_id', 'conversation_id', 'attempt_id'], ['owner', 'job_id', 'conversation_id', 'attempt_id'])
        s = self.s
        member = s.member(body['owner'])
        s.notifier.ensure_active(member)
        with s.lock:
            job = s.check_job(body['owner'], body['job_id'])
            row = s.one('SELECT attempt,state FROM conversation_execution WHERE job=?', (job['id'],))
            return {'valid': bool(row and row['attempt'] == body['attempt_id'] and row['state'] == 'running'
                                  and job['state'] == 'running' and job['conversation'] == body['conversation_id'])}

    def detail(self, job):
        row = self.s.one('SELECT result_json,state FROM conversation_execution WHERE job=?', (job['id'],))
        if not row or not row['result_json']:
            return {'effect_state': 'unknown' if row else 'none'}
        packet = json.loads(row['result_json'])
        # Expose status/preview, not host paths, signer material, or raw journals.
        return {'effect_state': packet['effect_state'], 'notice': packet.get('text'),
                **({'interaction': packet['interaction']} if isinstance(packet.get('interaction'), dict) else {}),**({k:packet[k] for k in ('engine','model','artifacts') if k in packet})}

    def control(self, owner, jid, channel, source_id, text, event_id=None, kind='reply'):
        self.s.unified.source(channel, source_id)
        if not isinstance(text, str) or not text.strip() or len(text) > 16000 or kind not in ('reply', 'continue', 'authorization'):
            raise Problem(400, 'invalid_request')
        with self.s.transaction():
            job = self.s.check_job(owner, jid)
            old = self.s.one('SELECT * FROM conversation_controls WHERE owner=? AND channel=? AND source_id=?', (owner, channel, source_id))
            if old:
                if (old['job'], old['text'], old['event_id']) != (jid, text, event_id):
                    raise Problem(409, 'idempotency_conflict')
                return self.s.unified.receipt(job)
            row = self.s.one('SELECT * FROM conversation_execution WHERE job=?', (jid,))
            if not row or row['state'] not in WAITING or job['state'] not in WAITING or row['pending_control']:
                raise Problem(409, 'studio_control_not_waiting')
            if kind == 'authorization' and job['state'] != 'waiting_authorization':
                raise Problem(409, 'studio_control_not_waiting')
            if job['state']=='waiting_authorization' and kind!='authorization':
                raise Problem(409,'studio_authorization_resume_required')
            if job['state']=='waiting_confirmation' and kind=='reply':
                result=json.loads(row['result_json']) if row['result_json'] else {}
                approval=(result.get('interaction') or {}).get('approval_id')
                if not approval or text.strip()!='确认 '+approval:raise Problem(409,'studio_confirmation_binding_required')
            control = {'kind': kind, 'text': text, 'channel': channel}
            if event_id:
                control['source_event_id'] = event_id
            self.s.db.execute('INSERT INTO conversation_controls VALUES(?,?,?,?,?,?,?)',
                              (owner, channel, source_id, jid, text, event_id, stamp()))
            if kind!='authorization':
                self.s.unified.transcript.record(job,'control',channel+':'+source_id,'user',text,channel,source_id)
            self.s.db.execute('UPDATE conversation_execution SET pending_control=?,updated=? WHERE job=?',
                              (canonical(control).decode(), stamp(), jid))
            self.s.db.execute("UPDATE jobs SET state='queued',updated=? WHERE id=?", (stamp(), jid))
            self.s.event(jid, 'status', {'state': 'queued', 'resumed': True, 'channel': channel})
            self.s.condition.notify_all()
            return self.s.unified.receipt(self.s.check_job(owner, jid))

    def cancel(self, job):
        if self.is_local(job):
            accepted=bool(self.local and self.local.cancel(job['id']))
            return {'job_id':job['id'],'accepted':accepted,'state':'cancel_requested'}
        try:
            packet = request(self.origin, '/internal/studio/cancel', {'job_id': job['id'], 'owner': job['owner']}, self.key_file, 15)
            if packet.get('job_id') != job['id'] or type(packet.get('accepted')) is not bool:
                raise ProtocolError('studio_cancel_response_unknown', uncertain=True)
            return packet
        except ProtocolError:
            return {'job_id': job['id'], 'accepted': False, 'state': 'cancel_requested'}

    def recover(self,job):
        row=self.s.one('SELECT attempt FROM conversation_execution WHERE job=?',(job['id'],))
        if not row:raise Problem(404,'not_found')
        if self.is_local(job):
            # Local recovery reads only the original private receipt; never relaunches CLI.
            work=self.local.workspace(job['owner'],job['conversation'])
            receipt=work/'.control'/job['id']/row['attempt']/'receipt.json'
            if not receipt.is_file():return {'recovered':False,'original_worker_alive':job['id'] in self.local.processes}
            value=json.loads(receipt.read_text())
            packet={'job_id':job['id'],'attempt_id':row['attempt'],'state':value['state'],'effect_state':'not_evaluated','text':value['text'],'engine':'codex-cli','artifacts':value.get('artifacts',[])}
        else:packet=None
        try:
            packet=packet or request(self.origin,'/internal/studio/status',{'owner':job['owner'],'job_id':job['id'],'attempt_id':row['attempt']},self.key_file,15)
            if packet.get('state')=='running' and packet.get('worker_alive') is True:
                return {'recovered':False,'original_worker_alive':True}
            result=self.validate_result(job,row['attempt'],packet)
        except ProtocolError as error:raise Problem(503,error.code) from None
        if result['state']=='execution_unknown':return {'recovered':False,'original_worker_alive':False}
        with self.s.transaction():
            current=self.s.check_job(job['owner'],job['id'])
            if current['state']!='execution_unknown':raise Problem(409,'studio_original_execution_required')
            if result['state']=='succeeded':
                answer_id=str(uuid.uuid4())
                self.s.db.execute('INSERT INTO messages(id,conversation,owner,role,text,created) VALUES(?,?,?,?,?,?)',
                    (answer_id,job['conversation'],job['owner'],'assistant',result['text'],stamp()))
                self.s.unified.finish(job,answer_id);self.s.conversation_store.touch(job['owner'],job['conversation'],content=True)
                self.s.event(job['id'],'final',{'text':result['text'],'result_refs':[]})
            elif result['state'] in WAITING:
                turn=self.s.one('SELECT channel FROM conversation_turns WHERE question=?',(job['message_id'],))
                self.s.unified.transcript.record(job,'notice',row['attempt'],'assistant',result['text'],turn['channel'],'notice:'+row['attempt'])
            self.s.db.execute('UPDATE conversation_execution SET state=?,result_json=?,updated=? WHERE job=?',
                (result['state'],canonical(result).decode(),stamp(),job['id']))
            self.s.db.execute('UPDATE jobs SET state=?,error=?,updated=? WHERE id=?',
                (result['state'],result.get('error_code',''),stamp(),job['id']))
            self.s.event(job['id'],'status',{'state':result['state'],'recovered_from_original_receipt':True})
        return {'recovered':True,'original_worker_alive':False}
