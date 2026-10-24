"""Authenticated conversation gateway with server-bound local execution. Browser never receives shell or credential access."""
import contextlib, hashlib, hmac, json, re, secrets, shutil, sqlite3, threading, time, uuid
from pathlib import Path

from domain import STATES, Problem, uid, shape, digest, stamp
from conversation_repository import ConversationRepository
from unified_conversations import UnifiedConversations
from conversation_context import ConversationContext
from execution_adapter import ExecutionAdapter, WAITING
from conversation_database import ConversationDatabase

class Service:
    def __init__(self,config,model,notifier,clock=time.time,start_worker=True):
        self.config=config;self.model=model;self.notifier=notifier;self.clock=clock
        self.root=Path(config['state_dir']).resolve();self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        self.lock=threading.RLock();self.condition=threading.Condition(self.lock);self.stop=threading.Event();self.members={m['handle']:m for m in config['members']};self.by_owner={m['principal_id']:m for m in config['members']}
        self.db=ConversationDatabase(self.root/'chat.sqlite3',self.lock)
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS member_bindings(owner TEXT PRIMARY KEY,subject TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,owner TEXT NOT NULL,expires REAL NOT NULL,last_seen REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS logins(id TEXT PRIMARY KEY,owner TEXT NOT NULL,challenge TEXT NOT NULL,device TEXT NOT NULL,code_hash TEXT NOT NULL,expires REAL NOT NULL,created REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,consumed INTEGER NOT NULL DEFAULT 0,delivered INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,conversation TEXT NOT NULL REFERENCES conversations(id),owner TEXT NOT NULL,role TEXT NOT NULL,text TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,conversation TEXT NOT NULL REFERENCES conversations(id),owner TEXT NOT NULL,message_id TEXT NOT NULL,client_message_id TEXT NOT NULL,state TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',created TEXT NOT NULL,updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS idempotency(owner TEXT NOT NULL,scope TEXT NOT NULL,key TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(owner,scope,key));
        CREATE TABLE IF NOT EXISTS events(job TEXT NOT NULL REFERENCES jobs(id),seq INTEGER NOT NULL,type TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(job,seq));
        CREATE UNIQUE INDEX IF NOT EXISTS messages_client_dedupe ON jobs(owner,conversation,client_message_id);
        ''');(self.root/'chat.sqlite3').chmod(0o600)
        self.db.execute('PRAGMA busy_timeout=5000')
        self.conversation_store=ConversationRepository(self)
        self.unified=UnifiedConversations(self)
        self.context=ConversationContext(self)
        self.unified_enabled=bool(config.get('unified_conversations',{}).get('enabled',False))
        execution_config=config.get('unified_conversations',{}).get('execution')
        self.execution=ExecutionAdapter(self,execution_config) if self.unified_enabled and execution_config else None
        with self.lock:
            for member in config['members']:
                subject=member['tenant_key']+':'+config.get('feishu_profile','fixture')+':'+member['open_id'];old=self.one('SELECT subject FROM member_bindings WHERE owner=?',(member['principal_id'],))
                if old and old['subject']!=subject:raise ValueError('member_identity_binding_changed')
                self.db.execute('INSERT OR IGNORE INTO member_bindings VALUES(?,?)',(member['principal_id'],subject))
            for job in self.db.execute("SELECT * FROM jobs WHERE state IN ('running','cancel_requested')").fetchall() if start_worker else []:
                self.db.execute("UPDATE jobs SET state='execution_unknown',error='interrupted_execution',updated=? WHERE id=?",(stamp(),job['id']));self.event(job['id'],'status',{'state':'execution_unknown'})
        self.worker=None
        if start_worker:
            self.worker=threading.Thread(target=self.work,name='studio-chat-worker',daemon=True);self.worker.start()
    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            nested=self.db.in_transaction
            if not nested:self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                if not nested:self.db.execute('COMMIT')
            except BaseException:
                if not nested:self.db.execute('ROLLBACK')
                raise
    def one(self,sql,args=()):
        with self.lock:return self.db.execute(sql,args).fetchone()
    def member(self,owner):
        m=self.by_owner.get(owner)
        if not m or not m.get('active',True):raise Problem(401,'authentication_required')
        return m
    def info(self,owner):
        m=self.member(owner);return {'principal_id':owner,'display_name':m['name'],'capabilities':{'chat':True,'feishu_read':False,'feishu_write':False,'shared_context':self.unified_enabled,**({'local_execution':True,'engine':'bridge'} if self.execution else {})},'auth_mode':'feishu_device_oauth_and_private_otp'}
    def authenticate(self,token):
        if not token or len(token)>512:raise Problem(401,'authentication_required')
        with self.lock:
            row=self.one('SELECT * FROM sessions WHERE token_hash=?',(digest(token),));now=self.clock()
            if not row or row['revoked'] or row['expires']<=now or row['last_seen']+self.config.get('idle_seconds',1800)<=now:raise Problem(401,'authentication_required')
            self.member(row['owner']);self.notifier.ensure_active(self.member(row['owner']));self.db.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?',(now,digest(token)));return row['owner']
    def logout(self,token):
        with self.lock:self.db.execute('UPDATE sessions SET revoked=1 WHERE token_hash=?',(digest(token),))
        return {'ok':True}
    def code_hash(self,login,code):return hmac.new(self.config['auth_pepper'].encode(),(login+':'+code).encode(),hashlib.sha256).hexdigest()
    def start_login(self,body):
        shape(body,['client_challenge','device_id','member_handle'],['client_challenge','device_id','member_handle']);challenge=body['client_challenge'];uid(body['device_id'])
        if not isinstance(challenge,str) or not re.fullmatch('[a-f0-9]{64}',challenge):raise Problem(400,'invalid_request')
        if not isinstance(body['member_handle'],str) or body['member_handle'] not in self.members:raise Problem(400,'invalid_request')
        m=self.members[body['member_handle']];self.member(m['principal_id']);now=self.clock()
        with self.transaction():
            last=self.one('SELECT created FROM logins WHERE owner=? ORDER BY created DESC LIMIT 1',(m['principal_id'],))
            if last and last['created']+60>now:raise Problem(429,'rate_limited')
            self.db.execute('UPDATE logins SET consumed=1 WHERE owner=?',(m['principal_id'],))
            login=str(uuid.uuid4());code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8));expires=now+300
            self.db.execute('INSERT INTO logins(id,owner,challenge,device,code_hash,expires,created) VALUES(?,?,?,?,?,?,?)',(login,m['principal_id'],challenge,body['device_id'],self.code_hash(login,code),expires,now))
        try:
            receipt=self.notifier.send(m,code,login,body['device_id'][-8:])
            if not receipt.get('message_id'):raise ValueError()
        except Exception:
            with self.lock:self.db.execute('UPDATE logins SET consumed=1 WHERE id=?',(login,))
            raise Problem(503,'login_delivery_failed')
        with self.lock:self.db.execute('UPDATE logins SET delivered=1 WHERE id=?',(login,))
        return {'login_id':login,'expires_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(expires)),'delivery':'feishu_private_card','member_name':m['name']}
    def poll_login(self,body):
        shape(body,['login_id','client_verifier','code'],['login_id','client_verifier','code']);uid(body['login_id']);verifier=body['client_verifier'];code=body['code']
        if not isinstance(verifier,str) or not 43<=len(verifier)<=128 or not isinstance(code,str) or len(code)>20:raise Problem(400,'invalid_request')
        with self.transaction():
            row=self.one('SELECT * FROM logins WHERE id=?',(body['login_id'],));now=self.clock()
            if not row or row['consumed'] or not row['delivered'] or row['expires']<=now or row['attempts']>=5:raise Problem(401,'login_expired_or_invalid')
            if not hmac.compare_digest(row['challenge'],digest(verifier)):raise Problem(401,'login_expired_or_invalid')
            valid=hmac.compare_digest(row['code_hash'],self.code_hash(row['id'],code.strip().upper()))
            if valid:
                self.member(row['owner']);token=secrets.token_urlsafe(36);expiry=now+self.config.get('session_seconds',43200)
                self.db.execute('UPDATE logins SET consumed=1 WHERE id=?',(row['id'],));self.db.execute('INSERT INTO sessions(token_hash,owner,expires,last_seen) VALUES(?,?,?,?)',(digest(token),row['owner'],expiry,now))
                answer={'status':'granted','session_credential':token,'expires_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(expiry)),'member':self.info(row['owner'])}
            else:self.db.execute('UPDATE logins SET attempts=attempts+1 WHERE id=?',(row['id'],));answer=None
        if answer is None:raise Problem(401,'login_expired_or_invalid')
        return answer
    def grant_oauth(self,identity):
        matches=[m for m in self.members.values() if m['open_id']==identity.get('open_id') and m['tenant_key']==identity.get('tenant_key') and m.get('active',True)]
        if len(matches)!=1:raise Problem(403,'oauth_member_not_allowed')
        m=matches[0];self.notifier.ensure_active(m)
        with self.transaction():
            now=self.clock();token=secrets.token_urlsafe(36);expiry=now+self.config.get('session_seconds',43200)
            self.db.execute('INSERT INTO sessions(token_hash,owner,expires,last_seen) VALUES(?,?,?,?)',(digest(token),m['principal_id'],expiry,now))
        return {'status':'granted','session_credential':token,'expires_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(expiry)),'member':self.info(m['principal_id'])}
    def check_conv(self,owner,cid):
        return self.conversation_store.get(owner,cid)
    def check_job(self,owner,jid):
        uid(jid);row=self.one('SELECT * FROM jobs WHERE id=? AND owner=?',(jid,owner))
        if not row:raise Problem(404,'not_found')
        self.check_conv(owner,row['conversation'])
        return row
    def key(self,value):
        if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{8,100}',value):raise Problem(400,'invalid_request')
        return value
    def existing(self,owner,scope,key,body):
        fingerprint=digest(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')));row=self.one('SELECT * FROM idempotency WHERE owner=? AND scope=? AND key=?',(owner,scope,key))
        if row:
            if not hmac.compare_digest(row['fingerprint'],fingerprint):raise Problem(409,'idempotency_conflict')
            return fingerprint,json.loads(row['result'])
        return fingerprint,None
    def remember(self,owner,scope,key,fingerprint,result):self.db.execute('INSERT INTO idempotency VALUES(?,?,?,?,?)',(owner,scope,key,fingerprint,json.dumps(result,ensure_ascii=False)))
    def create_conversation(self,owner,body,key):
        return self.conversation_store.create(owner,body,key)
    def start_message(self,owner,body,key,channel='app'):
        self.member(owner);shape(body,['title','text','client_message_id'],['title','text','client_message_id']);key=self.key(key);uid(body['client_message_id'])
        if not isinstance(body['text'],str) or not body['text'].strip() or len(body['text'])>16000:raise Problem(400,'invalid_request')
        with self.transaction():
            fingerprint,old=self.existing(owner,'start-message',key,body)
            if old:
                return {**old,'conversation':self.conversation_store.dto(self.check_conv(owner,old['conversation_id']))}
            conversation=self.create_conversation(owner,{'title':body['title']},'start_'+digest(key))
            accepted=self.submit(owner,conversation['conversation_id'],{'text':body['text'],'client_message_id':body['client_message_id']},'message_'+digest(key),channel)
            result={**accepted,'conversation_id':conversation['conversation_id'],'conversation':self.conversation_store.dto(self.check_conv(owner,conversation['conversation_id']))}
            self.remember(owner,'start-message',key,fingerprint,result);return result
    def conversations(self,owner,limit=30,offset=0,scope='active',search='',cursor=None):
        return self.conversation_store.list(owner,limit,offset,scope,search,cursor)
    def update_conversation(self,owner,cid,action,body,key):
        return self.conversation_store.mutate(owner,cid,action,body,key)
    def messages(self,owner,cid,limit=100,offset=0,before=None):
        if self.unified_enabled:return self.unified.messages(owner,cid,limit,offset,before)
        if not 1<=limit<=100 or (before is not None and (type(before) is not int or before<1)):
            raise Problem(400,'invalid_request')
        with self.lock:
            conversation=self.check_conv(owner,cid)
            if before is not None or offset==-1:
                clause=' AND seq<?' if before is not None else ''
                args=(owner,cid,before) if before is not None else (owner,cid)
                rows=self.db.execute('SELECT * FROM messages WHERE owner=? AND conversation=?'+clause+' ORDER BY seq DESC LIMIT ?',(*args,limit+1)).fetchall()
                more=len(rows)>limit;rows=list(reversed(rows[:limit]))
                older_cursor=str(rows[0]['seq']) if more and rows else None
                next_cursor=None
            else:
                rows=self.db.execute('SELECT * FROM messages WHERE owner=? AND conversation=? ORDER BY seq LIMIT ? OFFSET ?',(owner,cid,limit+1,offset)).fetchall()
                next_cursor=str(offset+limit) if len(rows)>limit else None;rows=rows[:limit];older_cursor=None
            items=[]
            for x in rows:
                job=self.one('SELECT id,state FROM jobs WHERE message_id=? AND owner=? ORDER BY rowid DESC LIMIT 1',(x['id'],owner))
                items.append({'message_id':x['id'],'seq':x['seq'],'role':x['role'],'text':x['text'],'created_at':x['created'],'source_refs':[],**({'job_id':job['id'],'job_state':job['state']} if job else {})})
            return {'items':items,'next_cursor':next_cursor,'older_cursor':older_cursor,'version':conversation['version'],'conversation':self.conversation_store.dto(conversation)}
    def submit(self,owner,cid,body,key,channel='app'):
        self.member(owner);shape(body,['text','client_message_id'],['text','client_message_id']);text=body['text'];uid(body['client_message_id']);key=self.key(key)
        if not isinstance(text,str) or not text.strip() or len(text)>16000:raise Problem(400,'invalid_request')
        if self.unified_enabled:
            with self.transaction():
                self.check_conv(owner,cid)
                fingerprint,old=self.existing(owner,cid,key,body)
                if old:return old
                accepted=self.unified.submit(owner,channel,body['client_message_id'],text,cid)
                self.remember(owner,cid,key,fingerprint,accepted)
                return accepted
        with self.transaction():
            conversation=self.check_conv(owner,cid)
            if conversation['archived_at']:raise Problem(409,'conversation_archived')
            fingerprint,old=self.existing(owner,cid,key,body)
            if old:return old
            if self.one('SELECT id FROM jobs WHERE owner=? AND conversation=? AND client_message_id=?',(owner,cid,body['client_message_id'])):raise Problem(409,'idempotency_conflict')
            if self.one("SELECT id FROM jobs WHERE conversation=? AND state IN ('queued','running','cancel_requested','execution_unknown') AND NOT EXISTS (SELECT 1 FROM job_resolutions r WHERE r.job=jobs.id)",(cid,)):raise Problem(409,'conversation_busy_or_unknown')
            if self.one("SELECT id FROM jobs WHERE owner=? AND state IN ('queued','running','cancel_requested')",(owner,)):raise Problem(429,'member_busy')
            jid,mid=str(uuid.uuid4()),str(uuid.uuid4());now=stamp();self.db.execute('INSERT INTO messages(id,conversation,owner,role,text,created) VALUES(?,?,?,?,?,?)',(mid,cid,owner,'user',text,now));self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(jid,cid,owner,mid,body['client_message_id'],'queued','',now,now))
            result={'job_id':jid,'message_id':mid,'state':'queued'};self.remember(owner,cid,key,fingerprint,result);self.conversation_store.touch(owner,cid,content=True);self.event(jid,'status',{'state':'queued'});self.condition.notify_all();return result
    def retry_job(self,owner,jid,body,key):
        self.member(owner);shape(body,['client_message_id','expected_version','acknowledge_unknown'],['client_message_id','expected_version']);uid(body['client_message_id']);key=self.key(key)
        if type(body['expected_version']) is not int or type(body.get('acknowledge_unknown',False)) is not bool:raise Problem(400,'invalid_request')
        with self.transaction():
            parent=self.check_job(owner,jid);cid=parent['conversation'];conversation=self.check_conv(owner,cid)
            fingerprint,old=self.existing(owner,'retry:'+jid,key,body)
            if old:return old
            if conversation['archived_at']:raise Problem(409,'conversation_archived')
            if conversation['version']!=body['expected_version']:raise Problem(409,'conversation_conflict')
            if parent['state'] not in ('failed','cancelled','execution_unknown') or self.one('SELECT job FROM job_resolutions WHERE job=?',(jid,)):raise Problem(409,'job_not_retryable')
            if self.execution and (parent['state']=='execution_unknown' or self.execution.detail(parent)['effect_state']!='none'):
                raise Problem(409,'studio_original_execution_required')
            if parent['state']=='execution_unknown' and not body.get('acknowledge_unknown'):raise Problem(409,'retry_confirmation_required')
            last=self.one("SELECT id FROM messages WHERE owner=? AND conversation=? AND role='user' ORDER BY seq DESC LIMIT 1",(owner,cid))
            if not last:raise Problem(409,'retry_not_latest')
            if last['id']!=parent['message_id']:
                later_finished=self.one("SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.owner=? AND j.conversation=? AND m.seq>(SELECT seq FROM messages WHERE id=?) AND j.state NOT IN ('queued','cancelled') LIMIT 1",(owner,cid,parent['message_id']))
                if not self.unified_enabled or parent['state']!='execution_unknown' or later_finished:raise Problem(409,'retry_not_latest')
            busy_states="('running','cancel_requested')" if self.unified_enabled else "('queued','running','cancel_requested')"
            if self.one("SELECT id FROM jobs WHERE owner=? AND state IN "+busy_states,(owner,)):raise Problem(429,'member_busy')
            new_id=str(uuid.uuid4());now=stamp()
            self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(new_id,cid,owner,parent['message_id'],body['client_message_id'],'queued','',now,now))
            self.db.execute('INSERT INTO job_resolutions VALUES(?,?)',(jid,new_id));self.db.execute('INSERT INTO retry_links VALUES(?,?)',(new_id,jid))
            self.conversation_store.touch(owner,cid,content=True);self.event(new_id,'status',{'state':'queued','retry_of':jid})
            result={'job_id':new_id,'message_id':parent['message_id'],'state':'queued','retry_of':jid};self.remember(owner,'retry:'+jid,key,fingerprint,result);self.condition.notify_all();return result
    def event(self,jid,kind,payload):
        seq=self.one('SELECT COALESCE(MAX(seq),0)+1 n FROM events WHERE job=?',(jid,))['n'];self.db.execute('INSERT INTO events VALUES(?,?,?,?)',(jid,seq,kind,json.dumps(payload,ensure_ascii=False)))
    def job(self,owner,jid):
        with self.lock:
            row=self.check_job(owner,jid);detail=self.execution.detail(row) if self.execution else {'effect_state':'none'}
            return {'job_id':row['id'],'conversation_id':row['conversation'],'state':row['state'],'error_code':row['error'] or None,'created_at':row['created'],'updated_at':row['updated'],'result_refs':[],**detail}
    def events(self,owner,jid,after=0):
        with self.lock:self.check_job(owner,jid);rows=self.db.execute('SELECT * FROM events WHERE job=? AND seq>? ORDER BY seq LIMIT 100',(jid,after)).fetchall()
        return [{'job_id':jid,'seq':r['seq'],'type':r['type'],'payload':json.loads(r['payload'])} for r in rows]
    def respond_job(self,owner,jid,body,key,channel='app'):
        if not self.execution:raise Problem(409,'shared_context_not_enabled')
        shape(body,['text','client_message_id'],['text','client_message_id']);uid(body['client_message_id']);key=self.key(key)
        with self.transaction():
            self.check_job(owner,jid);fingerprint,old=self.existing(owner,'respond:'+jid,key,body)
            if old:return old
            result=self.execution.control(owner,jid,channel,body['client_message_id'],body['text'])
            self.remember(owner,'respond:'+jid,key,fingerprint,result);return result
    def recover_job(self,owner,jid,key):
        if not self.execution:raise Problem(409,'shared_context_not_enabled')
        key=self.key(key)
        with self.lock:
            job=self.check_job(owner,jid)
            if job['state']!='execution_unknown':return self.job(owner,jid)
        result=self.execution.recover(job)
        return {**self.job(owner,jid),**result}
    def cancel(self,owner,jid,key):
        key=self.key(key)
        with self.transaction():
            row=self.check_job(owner,jid);fingerprint,old=self.existing(owner,'cancel:'+jid,key,{})
            if old:return self.job(owner,jid)
            state='cancelled' if row['state']=='queued' else 'cancel_requested' if row['state']=='running' or self.execution and row['state'] in WAITING else row['state'];self.db.execute('UPDATE jobs SET state=?,updated=? WHERE id=?',(state,stamp(),jid));self.event(jid,'status',{'state':state});self.remember(owner,'cancel:'+jid,key,fingerprint,{'state':state})
        if self.execution and state in ('cancel_requested','cancelled') and self.one('SELECT job FROM conversation_execution WHERE job=?',(jid,)):
            ack=self.execution.cancel(row)
            if ack['accepted'] and row['state'] in WAITING:
                with self.transaction():
                    self.db.execute("UPDATE jobs SET state='cancelled',updated=? WHERE id=? AND state='cancel_requested'",(stamp(),jid));self.event(jid,'status',{'state':'cancelled'})
        return self.job(owner,jid)
    def work(self):
        while not self.stop.is_set():
            with self.transaction():
                job=self.one("SELECT j.* FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' AND NOT EXISTS(SELECT 1 FROM jobs older JOIN messages om ON om.id=older.message_id WHERE older.conversation=j.conversation AND om.seq<m.seq AND older.state IN ('execution_unknown','waiting_authorization','waiting_confirmation','waiting_input','paused') AND NOT EXISTS(SELECT 1 FROM job_resolutions r WHERE r.job=older.id)) ORDER BY m.seq,j.rowid LIMIT 1")
                if job:self.db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?",(stamp(),job['id']));self.event(job['id'],'status',{'state':'running'})
            if not job:self.stop.wait(.2);continue
            directory=None
            try:
                target=self.root/'work'/job['owner']/job['id']
                if any(p.is_symlink() for p in [self.root/'work',target.parent,target]):raise Problem(503,'unsafe_workspace')
                target.resolve().relative_to(self.root);target.mkdir(parents=True,exist_ok=True,mode=0o700);directory=target
                self.member(job['owner'])
                with self.lock:
                    message=self.one('SELECT seq FROM messages WHERE id=? AND owner=?',(job['message_id'],job['owner']))
                    if self.unified_enabled:history=self.unified.history(job)
                    else:
                        rows=self.db.execute('SELECT role,text FROM messages WHERE owner=? AND conversation=? AND seq<=? ORDER BY seq DESC LIMIT 24',(job['owner'],job['conversation'],message['seq'])).fetchall();history=[{'role':r['role'],'content':r['text']} for r in reversed(rows)]
                if self.unified_enabled and not (self.execution and self.execution.is_local(job)):history=self.context.assemble(job,history)
                if self.execution:
                    outcome=self.execution.execute(job,history);answer=outcome['text'];state=outcome['state']
                    if state!='succeeded':
                        with self.transaction():
                            current=self.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state']
                            if current=='cancel_requested' and state!='execution_unknown':state='cancelled'
                            if state in WAITING:
                                turn=self.one('SELECT channel,source_id FROM conversation_turns WHERE question=?',(job['message_id'],))
                                self.unified.transcript.record(job,'notice',outcome['attempt_id'],'assistant',answer,
                                    turn['channel'],'notice:'+outcome['attempt_id'])
                            self.db.execute('UPDATE jobs SET state=?,error=?,updated=? WHERE id=?',(state,outcome.get('error_code',''),stamp(),job['id']))
                            self.event(job['id'],'notice',{'text':answer,'effect_state':outcome['effect_state']})
                            self.event(job['id'],'status',{'state':state})
                        continue
                else:answer=self.model.complete(history,self.member(job['owner'])['name'])
                if not isinstance(answer,str) or not answer.strip():raise Problem(502,'empty_model_response')
                with self.transaction():
                    now=stamp();current=self.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state'];conversation=self.conversation_store.get(job['owner'],job['conversation'],include_deleted=True);state='cancelled' if current=='cancel_requested' or conversation['deleted_at'] else 'succeeded'
                    if state=='succeeded':
                        answer_id=str(uuid.uuid4());self.db.execute('INSERT INTO messages(id,conversation,owner,role,text,created) VALUES(?,?,?,?,?,?)',(answer_id,job['conversation'],job['owner'],'assistant',answer,now))
                        self.unified.finish(job,answer_id)
                        self.event(job['id'],'final',{'text':answer,'result_refs':outcome.get('artifacts',[]) if self.execution else []});self.conversation_store.touch(job['owner'],job['conversation'],content=True)
                    self.db.execute('UPDATE jobs SET state=?,updated=? WHERE id=?',(state,now,job['id']));self.event(job['id'],'status',{'state':state})
            except Exception as error:
                code=error.code if isinstance(error,Problem) else 'model_request_failed'
                with self.transaction():
                    current=self.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state'];state='cancelled' if current=='cancel_requested' else 'failed';self.db.execute('UPDATE jobs SET state=?,error=?,updated=? WHERE id=?',(state,code,stamp(),job['id']));self.event(job['id'],'error',{'error_code':code});self.event(job['id'],'status',{'state':state})
            finally:
                if directory is not None:shutil.rmtree(directory,ignore_errors=True)
    def close(self):
        self.stop.set()
        if self.worker:self.worker.join(2)
        if self.worker is None or not self.worker.is_alive():
            with self.lock:self.db.close()
