"""Owner-scoped conversation lifecycle. Expand-only, recoverable storage migration."""
import hashlib, hmac, json, uuid
from domain import Problem, shape, stamp, uid

SCOPES = ('active', 'archived', 'trash')

class ConversationRepository:
    def __init__(self, service):
        self.s = service
        self.migrate()

    def migrate(self):
        s = self.s
        with s.transaction():
            if s.one('PRAGMA user_version')[0]>2:raise Problem(503,'schema_version_unsupported')
            columns = {r['name'] for r in s.db.execute('PRAGMA table_info(conversations)')}
            additions = {'pinned': 'INTEGER NOT NULL DEFAULT 0',
                         'archived_at': 'TEXT', 'deleted_at': 'TEXT',
                         'sort_seq': 'INTEGER NOT NULL DEFAULT 0'}
            fresh = 'sort_seq' not in columns
            for name, declaration in additions.items():
                if name not in columns:
                    s.db.execute('ALTER TABLE conversations ADD COLUMN '+name+' '+declaration)
            s.db.execute('CREATE TABLE IF NOT EXISTS owner_revisions(owner TEXT PRIMARY KEY,revision INTEGER NOT NULL)')
            s.db.execute('CREATE TABLE IF NOT EXISTS job_resolutions(job TEXT PRIMARY KEY REFERENCES jobs(id),replacement TEXT NOT NULL REFERENCES jobs(id))')
            s.db.execute('CREATE TABLE IF NOT EXISTS retry_links(job TEXT PRIMARY KEY REFERENCES jobs(id),parent_job TEXT NOT NULL REFERENCES jobs(id))')
            s.db.execute('CREATE INDEX IF NOT EXISTS conversation_owner_order ON conversations(owner,pinned DESC,sort_seq DESC,id DESC)')
            s.db.execute('CREATE INDEX IF NOT EXISTS message_owner_conversation ON messages(owner,conversation,seq)')
            if fresh:
                counters = {}
                rows = s.db.execute('SELECT id,owner FROM conversations ORDER BY updated,id').fetchall()
                for row in rows:
                    counters[row['owner']] = counters.get(row['owner'], 0)+1
                    s.db.execute('UPDATE conversations SET sort_seq=? WHERE id=? AND owner=?',
                                 (counters[row['owner']], row['id'], row['owner']))
            for row in s.db.execute('SELECT owner,MAX(sort_seq) n FROM conversations GROUP BY owner').fetchall():
                s.db.execute('INSERT OR IGNORE INTO owner_revisions VALUES(?,?)', (row['owner'], row['n']))
            s.db.execute('PRAGMA user_version=2')

    def revision(self, owner):
        r = self.s.one('SELECT revision FROM owner_revisions WHERE owner=?', (owner,))
        return r['revision'] if r else 0

    def tick(self, owner):
        self.s.db.execute('INSERT OR IGNORE INTO owner_revisions VALUES(?,0)', (owner,))
        self.s.db.execute('UPDATE owner_revisions SET revision=revision+1 WHERE owner=?', (owner,))
        return self.revision(owner)

    def touch(self, owner, cid, content=False):
        seq = self.tick(owner)
        self.s.db.execute('UPDATE conversations SET updated=?,sort_seq=?,version=version+? WHERE id=? AND owner=?',
                          (stamp(), seq, int(content), cid, owner))

    def get(self, owner, cid, include_deleted=False):
        uid(cid)
        row = self.s.one('SELECT * FROM conversations WHERE id=? AND owner=?', (cid, owner))
        if not row or (row['deleted_at'] and not include_deleted):
            raise Problem(404, 'not_found')
        return row

    def dto(self,row):
        unified=getattr(self.s,'unified',None)
        pending=bool(unified and unified.importer.pending(row['owner'],row['id']))
        return {'conversation_id': row['id'], 'title': row['title'], 'version': row['version'],
                **({'readonly_reason':'imported_history_pending'} if pending else {}),
                'pinned': bool(row['pinned']), 'archived_at': row['archived_at'],
                'deleted_at': row['deleted_at'], 'created_at': row['created'],
                'updated_at': row['updated']}

    def create(self, owner, body, key):
        s = self.s
        s.member(owner)
        shape(body, ['title'], ['title'])
        title, key = body['title'], s.key(key)
        if not isinstance(title, str) or not title.strip() or len(title)>100:
            raise Problem(400, 'invalid_request')
        with s.transaction():
            fingerprint, old = s.existing(owner, 'create', key, body)
            if old:
                self.get(owner, old['conversation_id'])
                return old
            cid, now = str(uuid.uuid4()), stamp()
            seq = self.tick(owner)
            s.db.execute('INSERT INTO conversations(id,owner,title,created,updated,sort_seq) VALUES(?,?,?,?,?,?)',
                         (cid, owner, title.strip(), now, now, seq))
            result = self.dto(self.get(owner, cid))
            s.remember(owner, 'create', key, fingerprint, result)
            return result

    def cursor_mac(self, owner, scope, search, data):
        raw = json.dumps([owner, scope, search, data], ensure_ascii=False, separators=(',', ':'))
        return hmac.new(self.s.config['auth_pepper'].encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]

    def list(self, owner, limit=30, offset=0, scope='active', search='', cursor=None):
        s = self.s
        s.member(owner)
        if scope not in SCOPES or not isinstance(search, str) or len(search)>100 or not 1<=limit<=100:
            raise Problem(400, 'invalid_request')
        with s.lock:
            revision = self.revision(owner)
            where, args = ['c.owner=?'], [owner]
            if scope=='active': where += ['c.deleted_at IS NULL', 'c.archived_at IS NULL']
            elif scope=='archived': where += ['c.deleted_at IS NULL', 'c.archived_at IS NOT NULL']
            else: where += ['c.deleted_at IS NOT NULL']
            if search:
                literal = '%'+search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
                where += ["(c.title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM messages m WHERE m.owner=c.owner AND m.conversation=c.id AND m.text LIKE ? ESCAPE '\\'))"]
                args += [literal, literal]
            if cursor:
                try:
                    parts = cursor.split(':')
                    if len(parts)!=5: raise ValueError()
                    rv, pin, seq, ident, mac = parts
                    data = ':'.join(parts[:4])
                    if not hmac.compare_digest(mac, self.cursor_mac(owner, scope, search, data)): raise ValueError()
                    rv, pin, seq = int(rv), int(pin), int(seq)
                    uid(ident)
                    if pin not in (0,1) or seq<0: raise ValueError()
                except (ValueError, TypeError, AttributeError, Problem):
                    raise Problem(400, 'invalid_cursor')
                if rv!=revision: raise Problem(409, 'list_changed')
                where += ['(c.pinned<? OR (c.pinned=? AND (c.sort_seq<? OR (c.sort_seq=? AND c.id<?))))']
                args += [pin, pin, seq, seq, ident]
                offset = 0
            rows = s.db.execute('SELECT c.* FROM conversations c WHERE '+' AND '.join(where)+
                                ' ORDER BY c.pinned DESC,c.sort_seq DESC,c.id DESC LIMIT ? OFFSET ?',
                                (*args, limit+1, offset)).fetchall()
            items = [self.dto(r) for r in rows[:limit]]
            next_cursor = None
            if len(rows)>limit:
                r = rows[limit-1]
                data = ':'.join(map(str, [revision,r['pinned'],r['sort_seq'],r['id']]))
                next_cursor = data+':'+self.cursor_mac(owner, scope, search, data)
            counts = {name:0 for name in SCOPES}
            for r in s.db.execute("SELECT CASE WHEN deleted_at IS NOT NULL THEN 'trash' WHEN archived_at IS NOT NULL THEN 'archived' ELSE 'active' END scope,COUNT(*) n FROM conversations WHERE owner=? GROUP BY scope", (owner,)):
                counts[r['scope']] = r['n']
            return {'items':items,'next_cursor':next_cursor,'revision':revision,'counts':counts,'scope':scope}

    def mutate(self, owner, cid, action, body, key):
        s = self.s
        s.member(owner)
        actions = {'rename':['title','expected_version'], 'pin':['pinned','expected_version'],
                   'archive':['archived','expected_version'], 'trash':['expected_version'],
                   'restore':['expected_version']}
        if action not in actions: raise Problem(400, 'invalid_request')
        shape(body, actions[action], actions[action])
        if type(body['expected_version']) is not int or body['expected_version']<0:
            raise Problem(400, 'invalid_request')
        if action=='rename' and (not isinstance(body['title'],str) or not body['title'].strip() or len(body['title'])>100):
            raise Problem(400, 'invalid_request')
        for field in ('pinned','archived'):
            if field in body and type(body[field]) is not bool: raise Problem(400,'invalid_request')
        key = s.key(key)
        with s.transaction():
            row = self.get(owner,cid,include_deleted=True)
            fingerprint,old = s.existing(owner,'conversation:'+cid+':'+action,key,body)
            if old: return self.dto(row)
            if row['version']!=body['expected_version']: raise Problem(409,'conversation_conflict')
            if row['deleted_at'] and action!='restore': raise Problem(404,'not_found')
            if action=='restore' and not row['deleted_at']: raise Problem(409,'conversation_not_deleted')
            if action=='rename': s.db.execute('UPDATE conversations SET title=? WHERE id=? AND owner=?',(body['title'].strip(),cid,owner))
            elif action=='pin': s.db.execute('UPDATE conversations SET pinned=? WHERE id=? AND owner=?',(int(body['pinned']),cid,owner))
            elif action=='archive': s.db.execute('UPDATE conversations SET archived_at=? WHERE id=? AND owner=?',(stamp() if body['archived'] else None,cid,owner))
            elif action=='trash':
                s.db.execute('UPDATE conversations SET deleted_at=? WHERE id=? AND owner=?',(stamp(),cid,owner))
                for job in s.db.execute("SELECT id,state FROM jobs WHERE owner=? AND conversation=? AND state IN ('queued','running','cancel_requested')",(owner,cid)).fetchall():
                    state = 'cancelled' if job['state']=='queued' else 'cancel_requested'
                    s.db.execute('UPDATE jobs SET state=?,updated=? WHERE id=? AND owner=?',(state,stamp(),job['id'],owner))
                    s.event(job['id'],'status',{'state':state,'conversation_deleted':True})
            else: s.db.execute('UPDATE conversations SET deleted_at=NULL WHERE id=? AND owner=?',(cid,owner))
            self.touch(owner,cid,content=True)
            result=self.dto(self.get(owner,cid,include_deleted=True))
            s.remember(owner,'conversation:'+cid+':'+action,key,fingerprint,result)
            return result
