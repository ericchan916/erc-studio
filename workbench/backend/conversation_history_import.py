"""Incremental PERSONAL legacy transcript import. Never creates runnable jobs.

The caller supplies a consistent read-only Bridge snapshot and its trusted
member bindings. Group chats, other actors and local workflow signals are not
read into personal history. Original databases remain untouched.
"""
import json,sqlite3,uuid
from pathlib import Path
from domain import Problem,digest

FINAL=('done','reconciled')


class LegacyHistoryImporter:
    def __init__(self, service):
        self.s=service
        service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_import_groups(
            owner TEXT NOT NULL,channel TEXT NOT NULL,group_id TEXT NOT NULL,
            conversation TEXT NOT NULL REFERENCES conversations(id),PRIMARY KEY(owner,channel,group_id))''')
        service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_import_sources(
            owner TEXT NOT NULL,channel TEXT NOT NULL,source_id TEXT NOT NULL,
            conversation TEXT NOT NULL REFERENCES conversations(id),question TEXT NOT NULL REFERENCES messages(id),
            answer TEXT REFERENCES messages(id),question_seq INTEGER NOT NULL,
            question_hash TEXT NOT NULL,answer_hash TEXT,state TEXT NOT NULL,
            PRIMARY KEY(owner,channel,source_id))''')
        service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_legacy_receipts(
            owner TEXT NOT NULL,source_id TEXT NOT NULL,parent_source TEXT NOT NULL,
            conversation TEXT NOT NULL,control TEXT,answer TEXT NOT NULL,
            fingerprint TEXT NOT NULL,settled INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(owner,source_id))''')
        if 'settled' not in {r['name'] for r in service.db.execute('PRAGMA table_info(conversation_legacy_receipts)')}:
            service.db.execute('ALTER TABLE conversation_legacy_receipts ADD COLUMN settled INTEGER NOT NULL DEFAULT 0')

    def pending(self, owner, cid):
        return bool(self.s.one("SELECT source_id FROM conversation_import_sources WHERE owner=? AND conversation=? AND state NOT IN ('done','reconciled') LIMIT 1",(owner,cid)))

    def apply(self, owner, group_id, records):
        s=self.s;member=s.member(owner)
        if not isinstance(group_id,str) or not group_id or len(group_id)>240 or not isinstance(records,list):raise Problem(400,'invalid_request')
        # Validate whole batch before any mutation, including idempotency scope.
        for r in records:
            if not isinstance(r,dict) or set(r)!={'source_id','chat_id','sender_id','text','answer','state','created_at'}:
                raise Problem(400,'invalid_request')
            if (r['chat_id'],r['sender_id'])!=(member.get('chat_id'),member['open_id']):raise Problem(403,'studio_member_not_allowed')
            if not isinstance(r['source_id'],str) or not 1<=len(r['source_id'])<=160 or not isinstance(r['text'],str) or not r['text'].strip() or len(r['text'])>1000000:
                raise Problem(400,'invalid_request')
            if r['answer'] is not None and (not isinstance(r['answer'],str) or not r['answer'].strip() or len(r['answer'])>1000000):raise Problem(400,'invalid_request')
            if r['state'] not in (*FINAL,'execution_unknown','failed','waiting_isolated_auth','legacy_pending') or not isinstance(r['created_at'],str) or len(r['created_at'])>80:raise Problem(400,'invalid_request')
        if not records:return {'conversation_id':None,'inserted':0,'existing':0,'pending':False}
        inserted=existing=0
        with s.transaction():
            group=s.one('SELECT conversation FROM conversation_import_groups WHERE owner=? AND channel=? AND group_id=?',(owner,'feishu',group_id))
            cid=group['conversation'] if group else str(uuid.uuid5(uuid.NAMESPACE_URL,'studio-legacy:'+owner+':'+group_id))
            if not group:
                title=('飞书旧会话 · '+records[0]['text'].strip())[:100]
                seq=s.conversation_store.tick(owner)
                s.db.execute('INSERT INTO conversations(id,owner,title,created,updated,sort_seq) VALUES(?,?,?,?,?,?)',
                    (cid,owner,title,records[0]['created_at'],records[-1]['created_at'],seq))
                s.db.execute('INSERT INTO conversation_import_groups VALUES(?,?,?,?)',(owner,'feishu',group_id,cid))
            for r in records:
                old=s.one('SELECT * FROM conversation_import_sources WHERE owner=? AND channel=? AND source_id=?',(owner,'feishu',r['source_id']))
                qhash=digest(r['text']);ahash=digest(r['answer']) if r['answer'] else None
                if old:
                    if (old['conversation'],old['question_hash'])!=(cid,qhash) or old['answer_hash'] is not None and old['answer_hash']!=ahash:
                        raise Problem(409,'history_import_source_conflict')
                    existing+=1
                    # A later ORIGINAL receipt may fill an absent answer; never
                    # rewrite an already imported question/answer or downgrade it.
                    if old['answer'] is None and r['answer']:
                        aid=self.message(owner,cid,'assistant',r['answer'],r['created_at'])
                        s.db.execute('UPDATE conversation_import_sources SET answer=?,answer_hash=? WHERE owner=? AND channel=? AND source_id=?',
                            (aid,ahash,owner,'feishu',r['source_id']))
                        s.conversation_store.touch(owner,cid,content=True)
                    if old['state'] not in FINAL and r['state'] in FINAL:
                        s.db.execute('UPDATE conversation_import_sources SET state=? WHERE owner=? AND channel=? AND source_id=?',(r['state'],owner,'feishu',r['source_id']))
                        s.conversation_store.touch(owner,cid,content=True)
                    if r['state']=='legacy_pending' and old['state'] in FINAL and not s.one('SELECT source_id FROM conversation_legacy_receipts WHERE owner=? AND parent_source=? AND settled=1',(owner,r['source_id'])):
                        s.db.execute("UPDATE conversation_import_sources SET state='legacy_pending' WHERE owner=? AND channel='feishu' AND source_id=?",(owner,r['source_id']))
                        s.conversation_store.touch(owner,cid,content=True)
                    continue
                qid=self.message(owner,cid,'user',r['text'],r['created_at'])
                aid=self.message(owner,cid,'assistant',r['answer'],r['created_at']) if r['answer'] else None
                seq=s.one('SELECT seq FROM messages WHERE id=?',(qid,))['seq']
                s.db.execute('INSERT INTO conversation_import_sources VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (owner,'feishu',r['source_id'],cid,qid,aid,seq,qhash,ahash,r['state']))
                inserted+=1
                s.conversation_store.touch(owner,cid,content=True)
            return {'conversation_id':cid,'inserted':inserted,'existing':existing,'pending':self.pending(owner,cid)}

    def message(self,owner,cid,role,text,created):
        mid=str(uuid.uuid4())
        self.s.db.execute('INSERT INTO messages(id,conversation,owner,role,text,created) VALUES(?,?,?,?,?,?)',(mid,cid,owner,role,text,created))
        return mid

    def receipt(self,owner,body):
        """Append real OLD-channel outcomes; no model, new job or active bind."""
        self.s.member(owner)
        required={'source_id','parent_source_id','text','answer','created_at','settled'}
        if not isinstance(body,dict) or set(body)!=required or type(body['settled']) is not bool:raise Problem(400,'invalid_request')
        if any(not isinstance(body[k],str) or not 1<=len(body[k])<=200 for k in ('source_id','parent_source_id')):raise Problem(400,'invalid_request')
        if not isinstance(body['text'],str) or len(body['text'])>16000 or not isinstance(body['answer'],str) or not body['answer'].strip() or len(body['answer'])>100000 or not isinstance(body['created_at'],str) or len(body['created_at'])>80:raise Problem(400,'invalid_request')
        fingerprint=digest(json.dumps(body,ensure_ascii=False,sort_keys=True))
        with self.s.transaction():
            parent=self.s.one('SELECT * FROM conversation_import_sources WHERE owner=? AND channel=? AND source_id=?',(owner,'feishu',body['parent_source_id']))
            if not parent:return {'imported':False,'reason':'legacy_source_not_imported'}
            old=self.s.one('SELECT fingerprint FROM conversation_legacy_receipts WHERE owner=? AND source_id=?',(owner,body['source_id']))
            if old:
                if old[0]!=fingerprint:raise Problem(409,'history_import_source_conflict')
                return {'imported':True,'duplicate':True,'conversation_id':parent['conversation']}
            cid=parent['conversation']
            # Preserve an original already visible waiting notice. Authorization
            # completion is not another user question; card choices ARE actual
            # user control text, and are retained without re-executing them.
            control=self.message(owner,cid,'user',body['text'],body['created_at']) if body['source_id']!=body['parent_source_id'] and body['text'].strip() else None
            answer=self.message(owner,cid,'assistant',body['answer'],body['created_at'])
            self.s.db.execute('INSERT INTO conversation_legacy_receipts(owner,source_id,parent_source,conversation,control,answer,fingerprint,settled) VALUES(?,?,?,?,?,?,?,?)',(owner,body['source_id'],body['parent_source_id'],cid,control,answer,fingerprint,int(body['settled'])))
            if body['settled'] and parent['state']=='legacy_pending':
                self.s.db.execute("UPDATE conversation_import_sources SET state='done' WHERE owner=? AND channel='feishu' AND source_id=?",(owner,body['parent_source_id']))
            self.s.conversation_store.touch(owner,cid,content=True)
            return {'imported':True,'duplicate':False,'conversation_id':cid}


def read_personal_events(snapshot, member, native_states=None):
    """Read only the EXACT registered sender/private-chat pair from a snapshot."""
    path=Path(snapshot)
    if path.is_symlink() or not path.is_file():raise ValueError('history_snapshot_invalid')
    groups={};skipped={};group=member['chat_id']+':root'
    # Only consistent, CLOSED backup snapshots are accepted here. immutable
    # avoids trying to create WAL/SHM beside a frozen standalone backup.
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-journal')):raise ValueError('history_snapshot_not_frozen')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    try:
        rows=db.execute('SELECT * FROM events WHERE sender_id=? AND chat_id=? ORDER BY created_at,rowid',(member['open_id'],member['chat_id'])).fetchall()
        for row in rows:
            event=json.loads(row['payload_json'])
            if (event.get('sender_id'),event.get('chat_id'),event.get('chat_type'))!=(member['open_id'],member['chat_id'],'p2p') or event.get('origin'):
                skipped['non_personal']=skipped.get('non_personal',0)+1;continue
            text=event.get('content')
            if not isinstance(text,str) or not text.strip():
                skipped['no_text']=skipped.get('no_text',0)+1;continue
            if text.strip().lower()=='/new':group=member['chat_id']+':'+row['event_id']
            if row['status'] not in (*FINAL,'execution_unknown','failed','waiting_isolated_auth'):
                skipped['in_flight']=skipped.get('in_flight',0)+1;continue
            # Keep only actually persisted visible output, including original
            # question/confirmation notices; never reconstruct erased messages.
            answer=row['response_text'] or None
            state=row['status']
            if (native_states or {}).get(row['event_id']) not in (None,'done','cancelled'):state='legacy_pending'
            groups.setdefault(group,[]).append({'source_id':row['event_id'],'chat_id':member['chat_id'],'sender_id':member['open_id'],
                'text':text,'answer':answer,'state':state,'created_at':row['created_at']})
    finally:db.close()
    return groups,skipped


def read_native_states(snapshot,member):
    path=Path(snapshot)
    if path.is_symlink() or not path.is_file() or Path(str(path)+'-wal').exists():raise ValueError('native_snapshot_not_frozen')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'native_tasks','native_task_events'}<=tables:return {}
        result={}
        for eid,state,payload in db.execute('SELECT e.event_id,t.state,t.payload FROM native_task_events e JOIN native_tasks t ON t.id=e.task_id WHERE t.chat_id=?',(member['chat_id'],)):
            task=json.loads(payload)
            if task.get('user')==member['open_id'] and task.get('chat_id')==member['chat_id'] and task.get('conversation_id') is None:result[eid]=state
        return result
    finally:db.close()
