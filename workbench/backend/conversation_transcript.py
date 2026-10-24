"""Visible control/notice records anchored to their ORIGINAL accepted question.

No engine thoughts or raw tool journals enter this transcript. Existing message
and job columns stay intact. Stable logical positions also support pagination.
"""
import uuid
from domain import Problem, stamp

STRIDE=1000000


class ConversationTranscript:
    def __init__(self, service):
        self.s=service
        service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_entries(
            message TEXT PRIMARY KEY REFERENCES messages(id),job TEXT NOT NULL REFERENCES jobs(id),
            owner TEXT NOT NULL,conversation TEXT NOT NULL,question_seq INTEGER NOT NULL,
            position INTEGER NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,
            channel TEXT NOT NULL,source_id TEXT NOT NULL,
            UNIQUE(job,kind,source),UNIQUE(owner,conversation,question_seq,position))''')

    def record(self, job, kind, source, role, text, channel, source_id):
        if kind not in ('control','notice') or role not in ('user','assistant'):
            raise Problem(400,'invalid_request')
        self.s.member(job['owner'])
        with self.s.transaction():
            conv=self.s.conversation_store.get(job['owner'],job['conversation'],include_deleted=True)
            if conv['deleted_at']:return None
            old=self.s.one('SELECT e.message,m.text FROM conversation_entries e JOIN messages m ON m.id=e.message WHERE e.job=? AND e.kind=? AND e.source=?',
                (job['id'],kind,source))
            if old:
                if old['text']!=text:raise Problem(409,'idempotency_conflict')
                return old['message']
            question=self.s.one('SELECT seq FROM messages WHERE id=? AND owner=? AND conversation=? AND role=?',
                (job['message_id'],job['owner'],job['conversation'],'user'))
            if not question:raise Problem(409,'conversation_answer_binding_conflict')
            position=self.s.one('SELECT COALESCE(MAX(position),0)+1 n FROM conversation_entries WHERE owner=? AND conversation=? AND question_seq=?',
                (job['owner'],job['conversation'],question['seq']))['n']
            if position>=STRIDE-1:raise Problem(429,'conversation_queue_full')
            mid=str(uuid.uuid4())
            self.s.db.execute('INSERT INTO messages(id,conversation,owner,role,text,created) VALUES(?,?,?,?,?,?)',
                (mid,job['conversation'],job['owner'],role,text,stamp()))
            self.s.db.execute('INSERT INTO conversation_entries VALUES(?,?,?,?,?,?,?,?,?,?)',
                (mid,job['id'],job['owner'],job['conversation'],question['seq'],position,kind,source,channel,source_id))
            self.s.conversation_store.touch(job['owner'],job['conversation'],content=True)
            return mid

    def ordered(self, owner, cid):
        self.s.member(owner);self.s.check_conv(owner,cid)
        return self.s.db.execute('''SELECT m.*,
            COALESCE(t.question_seq,e.question_seq,i.question_seq,m.seq)*?+
            CASE WHEN t.answer=m.id OR i.answer=m.id THEN ? ELSE COALESCE(e.position,0) END order_seq,
            COALESCE(t.channel,e.channel,i.channel,CASE WHEN l.source_id IS NOT NULL THEN 'feishu' END) channel,COALESCE(t.source_id,e.source_id,i.source_id,l.source_id) source_id,
            e.kind entry_kind,i.source_id IS NOT NULL OR l.source_id IS NOT NULL imported
            FROM messages m LEFT JOIN conversation_turns t ON (m.id=t.question OR m.id=t.answer)
                AND m.owner=t.owner AND m.conversation=t.conversation
            LEFT JOIN conversation_entries e ON e.message=m.id AND e.owner=m.owner AND e.conversation=m.conversation
            LEFT JOIN conversation_import_sources i ON (i.question=m.id OR i.answer=m.id) AND i.owner=m.owner AND i.conversation=m.conversation
            LEFT JOIN conversation_legacy_receipts l ON (l.control=m.id OR l.answer=m.id) AND l.owner=m.owner AND l.conversation=m.conversation
            WHERE m.owner=? AND m.conversation=? ORDER BY order_seq,m.seq''',
            (STRIDE,STRIDE-1,owner,cid)).fetchall()
