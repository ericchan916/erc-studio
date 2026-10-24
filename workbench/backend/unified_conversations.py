"""One owner-scoped transcript and durable routing index for all three channels.

The routing/transcript separation follows Hermes. The identity, SQLite schema,
idempotency and task state machine remain the studio's existing contracts.
This is an expand-only migration; existing messages and nine-column jobs stay intact.
"""
import json
import uuid

from domain import Problem, digest, shape, stamp, uid
from conversation_transcript import ConversationTranscript, STRIDE
from conversation_history_import import LegacyHistoryImporter

CHANNELS = ('web', 'app', 'feishu')


class UnifiedConversations:
    def __init__(self, service):
        self.s = service
        with service.transaction():
            # Use a feature-specific schema version: old lifecycle code still accepts user_version=2.
            service.db.execute('CREATE TABLE IF NOT EXISTS conversation_runtime_schema(version INTEGER PRIMARY KEY)')
            version = service.one('SELECT MAX(version) n FROM conversation_runtime_schema')['n']
            if version is not None and version > 1:
                raise Problem(503, 'schema_version_unsupported')
            service.db.execute('INSERT OR IGNORE INTO conversation_runtime_schema VALUES(1)')
            service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_active(
                owner TEXT PRIMARY KEY,conversation TEXT NOT NULL REFERENCES conversations(id),
                revision INTEGER NOT NULL,updated TEXT NOT NULL)''')
            service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_ingress(
                owner TEXT NOT NULL,channel TEXT NOT NULL,source_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,job TEXT NOT NULL REFERENCES jobs(id),
                PRIMARY KEY(owner,channel,source_id))''')
            service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_turns(
                job TEXT PRIMARY KEY REFERENCES jobs(id),owner TEXT NOT NULL,
                conversation TEXT NOT NULL REFERENCES conversations(id),
                question TEXT NOT NULL UNIQUE REFERENCES messages(id),
                answer TEXT UNIQUE REFERENCES messages(id),
                channel TEXT NOT NULL,source_id TEXT NOT NULL,question_seq INTEGER NOT NULL)''')
            service.db.execute('CREATE INDEX IF NOT EXISTS conversation_turn_order ON conversation_turns(owner,conversation,question_seq)')
            self.transcript=ConversationTranscript(service)
            self.importer=LegacyHistoryImporter(service)

    def active(self, owner):
        self.s.member(owner)
        with self.s.lock:
            row = self.s.one('SELECT * FROM conversation_active WHERE owner=?', (owner,))
            if not row:
                return {'conversation_id': None, 'revision': 0}
            conv = self.s.conversation_store.get(owner, row['conversation'], include_deleted=True)
            return {'conversation_id': None if conv['deleted_at'] or conv['archived_at'] else row['conversation'],
                    'revision': row['revision']}

    def bind(self, owner, cid, expected_revision=None):
        self.s.member(owner)
        with self.s.transaction():
            conv = self.s.check_conv(owner, cid)
            if self.importer.pending(owner,cid):raise Problem(409,'imported_history_pending')
            if conv['archived_at']:
                raise Problem(409, 'conversation_archived')
            current = self.s.one('SELECT * FROM conversation_active WHERE owner=?', (owner,))
            revision = current['revision'] if current else 0
            if expected_revision is not None and (type(expected_revision) is not int or expected_revision != revision):
                raise Problem(409, 'conversation_binding_conflict')
            if current and current['conversation'] == cid:
                return {'conversation_id': cid, 'revision': revision}
            self.s.db.execute('INSERT OR REPLACE INTO conversation_active VALUES(?,?,?,?)',
                              (owner, cid, revision+1, stamp()))
            return {'conversation_id': cid, 'revision': revision+1}

    def activate(self, owner, cid, body, key):
        """Explicit 'continue this conversation', with CAS and retry-safe admission."""
        shape(body, ['expected_revision'], ['expected_revision'])
        if type(body['expected_revision']) is not int or body['expected_revision'] < 0:
            raise Problem(400, 'invalid_request')
        key = self.s.key(key)
        with self.s.transaction():
            self.s.member(owner)
            self.s.check_conv(owner, cid)
            fingerprint, old = self.s.existing(owner, 'continue:'+cid, key, body)
            if old:
                return old
            result = self.bind(owner, cid, body['expected_revision'])
            self.s.remember(owner, 'continue:'+cid, key, fingerprint, result)
            return result

    @staticmethod
    def source(channel, source_id):
        if channel not in CHANNELS or not isinstance(source_id, str) or not 1 <= len(source_id) <= 160:
            raise Problem(400, 'invalid_request')
        if any(ord(c) < 32 for c in source_id):
            raise Problem(400, 'invalid_request')

    def submit(self, owner, channel, source_id, text, cid=None, title=None):
        """Transactional admission. Replay is resolved BEFORE the current binding.

        cid is a conversation choice, never a caller-supplied identity. Feishu's
        trusted adapter supplies owner after validating tenant/member/private-chat.
        """
        self.s.member(owner)
        self.source(channel, source_id)
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise Problem(400, 'invalid_request')
        if cid is not None:
            uid(cid)
        fingerprint = digest(json.dumps([text, cid, title], ensure_ascii=False, separators=(',', ':')))
        with self.s.transaction():
            old = self.s.one('SELECT * FROM conversation_ingress WHERE owner=? AND channel=? AND source_id=?',
                             (owner, channel, source_id))
            if old:
                if old['fingerprint'] != fingerprint:
                    raise Problem(409, 'idempotency_conflict')
                job = self.s.check_job(owner, old['job'])
                return self.receipt(job)
            if cid is None:
                cid = self.active(owner)['conversation_id']
            if cid is None:
                created = self.s.create_conversation(owner, {'title': title or text.strip()[:40]},
                                                     'unified_'+digest(channel+':'+source_id))
                cid = created['conversation_id']
            conv = self.s.check_conv(owner, cid)
            if conv['archived_at']:
                raise Problem(409, 'conversation_archived')
            unknown = self.s.one("""SELECT j.id FROM jobs j WHERE j.owner=? AND j.conversation=?
                AND j.state='execution_unknown' AND NOT EXISTS(SELECT 1 FROM job_resolutions r WHERE r.job=j.id)""",
                                 (owner, cid))
            if unknown:
                raise Problem(409, 'conversation_busy_or_unknown')
            count = self.s.one("SELECT COUNT(*) n FROM jobs WHERE owner=? AND conversation=? AND state IN ('queued','running','cancel_requested')", (owner, cid))['n']
            if count >= self.s.config.get('conversation_queue_limit', 20):
                raise Problem(429, 'conversation_queue_full')
            jid, mid, now = str(uuid.uuid4()), str(uuid.uuid4()), stamp()
            client_id = str(uuid.uuid5(uuid.NAMESPACE_URL, owner+':'+channel+':'+source_id))
            self.s.db.execute('INSERT INTO messages(id,conversation,owner,role,text,created) VALUES(?,?,?,?,?,?)',
                              (mid, cid, owner, 'user', text, now))
            self.s.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                              (jid, cid, owner, mid, client_id, 'queued', '', now, now))
            seq = self.s.one('SELECT seq FROM messages WHERE id=?', (mid,))['seq']
            self.s.db.execute('INSERT INTO conversation_turns VALUES(?,?,?,?,NULL,?,?,?)',
                              (jid, owner, cid, mid, channel, source_id, seq))
            self.s.db.execute('INSERT INTO conversation_ingress VALUES(?,?,?,?,?)',
                              (owner, channel, source_id, fingerprint, jid))
            self.bind(owner, cid)
            self.s.conversation_store.touch(owner, cid, content=True)
            self.s.event(jid, 'status', {'state': 'queued', 'channel': channel})
            self.s.condition.notify_all()
            return self.receipt(self.s.check_job(owner, jid))

    def receipt(self, job):
        seen = set()
        while True:
            if job['id'] in seen or len(seen) >= 32:
                raise Problem(503, 'job_resolution_invalid')
            seen.add(job['id'])
            resolution = self.s.one('SELECT replacement FROM job_resolutions WHERE job=?', (job['id'],))
            if not resolution:
                break
            replacement = self.s.check_job(job['owner'], resolution['replacement'])
            if (replacement['conversation'], replacement['message_id']) != (job['conversation'], job['message_id']):
                raise Problem(503, 'job_resolution_invalid')
            job = replacement
        return {'job_id': job['id'], 'conversation_id': job['conversation'],
                'message_id': job['message_id'], 'state': job['state']}

    def finish(self, job, answer_id):
        """Called inside the same transaction as final message persistence."""
        answer = self.s.one('SELECT owner,conversation,role FROM messages WHERE id=?', (answer_id,))
        if not answer or (answer['owner'], answer['conversation'], answer['role']) != (job['owner'], job['conversation'], 'assistant'):
            raise Problem(409, 'conversation_answer_binding_conflict')
        old = self.s.one('SELECT answer FROM conversation_turns WHERE question=? AND owner=? AND conversation=?',
                         (job['message_id'], job['owner'], job['conversation']))
        if old and old['answer'] and old['answer'] != answer_id:
            raise Problem(409, 'conversation_answer_binding_conflict')
        if old:
            self.s.db.execute('UPDATE conversation_turns SET answer=? WHERE question=? AND owner=? AND conversation=?',
                              (answer_id, job['message_id'], job['owner'], job['conversation']))
        elif self.s.unified_enabled:
            # A retry of a pre-migration question keeps that question's logical
            # position; its old channel is unknown rather than invented.
            seq = self.s.one('SELECT seq FROM messages WHERE id=?', (job['message_id'],))['seq']
            self.s.db.execute('INSERT INTO conversation_turns VALUES(?,?,?,?,?,?,?,?)',
                              (job['id'], job['owner'], job['conversation'], job['message_id'], answer_id,
                               'legacy', job['client_message_id'], seq))

    def ordered(self, owner, cid):
        self.s.member(owner)
        self.s.check_conv(owner, cid)
        # A queued second question may be inserted before the first answer.
        # The logical order therefore uses question acceptance order + role,
        # not the physical order of reply insertion or wall-clock timestamps.
        return self.transcript.ordered(owner,cid)

    def history(self, job):
        with self.s.lock:
            question = self.s.one('SELECT seq FROM messages WHERE id=? AND owner=?', (job['message_id'], job['owner']))
            if not question:
                raise Problem(404, 'not_found')
            return [{'role': r['role'], 'content': r['text']} for r in self.ordered(job['owner'], job['conversation'])
                    if r['order_seq'] <= question['seq']*STRIDE]

    def messages(self, owner, cid, limit=100, offset=0, before=None):
        if not 1 <= limit <= 100 or offset < -1 or before is not None and (type(before) is not int or before <= 0):
            raise Problem(400, 'invalid_request')
        with self.s.lock:
            rows = self.ordered(owner, cid)
            if before is not None:
                rows = [r for r in rows if r['order_seq'] < before]
            latest = offset == -1 or before is not None
            page = rows[-limit:] if latest else rows[offset:offset+limit]
            items = []
            for r in page:
                job = self.s.one('SELECT id,state FROM jobs WHERE owner=? AND message_id=? ORDER BY rowid DESC LIMIT 1', (owner, r['id']))
                items.append({'message_id': r['id'], 'seq': r['order_seq'], 'role': r['role'], 'text': r['text'],
                              **({'entry_kind':r['entry_kind']} if r['entry_kind'] else {}),
                              'created_at': r['created'], 'source_refs': ([{'channel': r['channel'], 'source_id': r['source_id'],
                                  **({'legacy':True,'timestamp_kind':'bridge_event_round'} if r['imported'] else {})}] if r['channel'] else []),
                              **({'job_id': job['id'], 'job_state': job['state']} if job else {})})
            conv = self.s.check_conv(owner, cid)
            return {'items': items, 'older_cursor': str(page[0]['order_seq']) if latest and len(rows) > limit else None,
                    'next_cursor': str(offset+limit) if not latest and len(rows) > offset+limit else None,
                    'version': conv['version'], 'conversation': self.s.conversation_store.dto(conv)}
