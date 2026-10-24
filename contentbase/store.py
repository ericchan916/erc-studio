"""Technical outbox and execution journal, never a replacement business ledger."""
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from .errors import BaseError


def digest(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise BaseError("invalid_json_value") from None
    return hashlib.sha256(encoded).hexdigest()


def operation_scope(data):
    binding = data.get("binding_hash") or data.get("inputs", {}).get("binding")
    identity = data.get("plan", {}).get("identity") or data.get("inputs", {}).get("identity")
    if not binding or not identity:
        return None
    value = {"binding": binding, "identity": identity}
    return digest(value)


def process_generation(pid):
    """Verify a process generation and ignore zombies; a PID alone is not ownership."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return "unverified"
    proc = Path("/proc") / str(pid) / "stat"
    if proc.exists():
        try:
            fields = proc.read_text().rsplit(")", 1)[1].split()
            if fields[0] in ("Z", "X"):
                return None
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return "proc:" + boot + ":" + fields[19]
        except (OSError, IndexError, ValueError):
            return "unverified"
    try:
        process = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="],
                                 capture_output=True, text=True, timeout=2,
                                 env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"})
    except (OSError, subprocess.TimeoutExpired):
        return "unverified"
    fields = process.stdout.split()
    if process.returncode or not fields or fields[-1].startswith("Z"):
        return None
    if len(fields) < 6:
        return "unverified"
    return "ps:" + digest({"pid": pid, "start": " ".join(fields[:-1])})


def validate_key(key):
    if not isinstance(key, str) or not 8 <= len(key) <= 128 or any(ord(c) < 32 for c in key):
        raise BaseError("idempotency_key_required")


class Store:
    def __init__(self, path):
        if os.name != "posix":
            raise BaseError("platform_support_pending")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise BaseError("state_database_not_regular")
        if self.path.exists() and self.path.stat().st_nlink != 1:
            raise BaseError("state_database_shared_inode")
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            except FileExistsError:
                if self.path.is_symlink():
                    raise BaseError("state_database_not_regular") from None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, request_key TEXT NOT NULL,
                    input_hash TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, run_pid INTEGER, run_token TEXT, scope_hash TEXT,
                    UNIQUE(kind, request_key))""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT,
                    event TEXT NOT NULL, created REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS resource_locks (
                    resource TEXT PRIMARY KEY, owner TEXT NOT NULL, pid INTEGER NOT NULL, run_token TEXT)""")
            db.execute("CREATE TABLE IF NOT EXISTS workspace_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(operations)")}
            if "run_pid" not in columns:
                db.execute("ALTER TABLE operations ADD COLUMN run_pid INTEGER")
            if "scope_hash" not in columns:
                db.execute("ALTER TABLE operations ADD COLUMN scope_hash TEXT")
            if "run_token" not in columns:
                db.execute("ALTER TABLE operations ADD COLUMN run_token TEXT")
            lock_columns = {row[1] for row in db.execute("PRAGMA table_info(resource_locks)")}
            if "run_token" not in lock_columns:
                db.execute("ALTER TABLE resource_locks ADD COLUMN run_token TEXT")
            for old in db.execute("SELECT id,data FROM operations WHERE scope_hash IS NULL").fetchall():
                scope = operation_scope(json.loads(old["data"]))
                if scope:
                    db.execute("UPDATE operations SET scope_hash=? WHERE id=?", (scope, old["id"]))
            db.execute("CREATE INDEX IF NOT EXISTS operations_scope_idx ON operations(scope_hash,state,created)")
            interrupted = db.execute("SELECT id,state,run_pid,run_token FROM operations WHERE state IN ('applying','submitting','preparing')").fetchall()
            for row in interrupted:
                generation = process_generation(row["run_pid"])
                if row["run_token"] and row["run_token"] != "unverified" and row["run_token"] == generation:
                    continue
                state = "unknown" if row["state"] != "preparing" else "preparation_interrupted"
                changed = db.execute("UPDATE operations SET state=?,updated=?,run_pid=NULL,run_token=NULL WHERE id=? AND state=? AND run_pid IS ? AND run_token IS ?", (state, time.time(), row["id"], row["state"], row["run_pid"], row["run_token"])).rowcount
                if changed:
                    db.execute("INSERT INTO events(operation_id,event,created) VALUES(?,?,?)", (row["id"], state, time.time()))
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW)
        except OSError:
            raise BaseError("state_database_not_regular") from None
        db = None
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise BaseError("state_database_shared_or_nonregular")
            os.fchmod(fd, 0o600)
            db = sqlite3.connect(self.path, timeout=30)
            after = os.stat(self.path, follow_symlinks=False)
            if not stat.S_ISREG(after.st_mode) or (after.st_dev, after.st_ino, after.st_nlink) != (before.st_dev, before.st_ino, 1):
                raise BaseError("state_database_replaced")
            opened = db.execute("PRAGMA database_list").fetchone()[2]
            if Path(opened).resolve() != self.path.resolve():
                raise BaseError("state_database_replaced")
            db.row_factory = sqlite3.Row
            with db:
                yield db
        except sqlite3.Error as error:
            code = "state_database_busy" if "locked" in str(error).lower() or "busy" in str(error).lower() else "state_database_error"
            raise BaseError(code) from None
        finally:
            if db:
                db.close()
            os.close(fd)

    @staticmethod
    def decode(row):
        data = json.loads(row["data"])
        return {"id": row["id"], "kind": row["kind"], "state": row["state"],
                "request_key": row["request_key"],
                "digest": row["input_hash"], "confirmation_digest": data.get("confirmation"), "data": data,
                "created_at": row["created"], "updated_at": row["updated"]}

    def create(self, kind, key, inputs, data, state="prepared"):
        validate_key(key)
        hashed = digest(inputs)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM operations WHERE kind=? AND request_key=?", (kind, key)).fetchone()
            if old:
                if old["input_hash"] != hashed:
                    raise BaseError("idempotency_conflict")
                return self.decode(old), False
            oid = "op_" + uuid.uuid4().hex
            now = time.time()
            pid = os.getpid() if state in ("preparing", "applying", "submitting") else None
            generation = process_generation(pid) if pid else None
            if pid and generation in (None, "unverified"):
                raise BaseError("worker_generation_unverified")
            db.execute("INSERT INTO operations(id,kind,request_key,input_hash,state,data,created,updated,run_pid,run_token,scope_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (oid, kind, key, hashed, state, json.dumps(data, ensure_ascii=False), now, now, pid, generation, operation_scope(data)))
            db.execute("INSERT INTO events(operation_id,event,created) VALUES(?,?,?)", (oid, state, now))
            row = db.execute("SELECT * FROM operations WHERE id=?", (oid,)).fetchone()
            return self.decode(row), True

    def get(self, oid):
        if not isinstance(oid, str) or not 1 <= len(oid) <= 128:
            raise BaseError("operation_id_required")
        with self.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (oid,)).fetchone()
        if not row:
            raise BaseError("operation_not_found")
        return self.decode(row)

    def lookup(self, kind, key):
        validate_key(key)
        with self.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE kind=? AND request_key=?", (kind, key)).fetchone()
        return self.decode(row) if row else None

    def pin_operator(self, identity):
        """A single-user workspace never silently adopts a different CLI user."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM workspace_metadata WHERE key='operator_identity'").fetchone()
            if row:
                if json.loads(row["value"]) != identity:
                    raise BaseError("workspace_operator_changed")
                return
            legacy = {}
            for row in db.execute("SELECT data FROM operations"):
                data = json.loads(row["data"])
                owner = data.get("plan", {}).get("identity") or data.get("inputs", {}).get("identity")
                if owner:
                    legacy[digest(owner)] = owner
            if len(legacy) > 1:
                raise BaseError("workspace_operator_ambiguous")
            if legacy and next(iter(legacy.values())) != identity:
                raise BaseError("workspace_operator_changed")
            db.execute("INSERT INTO workspace_metadata VALUES('operator_identity',?)", (json.dumps(identity, ensure_ascii=False),))

    def scoped_page(self, scopes, limit=200, offset=0):
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
            raise BaseError("invalid_pagination")
        placeholders = ",".join("?" for _ in scopes)
        query = ("SELECT * FROM operations WHERE scope_hash IN (" + placeholders + ") "
                 "ORDER BY CASE WHEN state='unknown' THEN 0 "
                 "WHEN state IN ('applying','submitting','preparing') THEN 1 "
                 "WHEN state IN ('prepared','approved','ready','preparation_interrupted') THEN 2 "
                 "WHEN state IN ('failed','preparation_failed') THEN 3 ELSE 4 END, created DESC, id "
                 "LIMIT ? OFFSET ?")
        with self.connect() as db:
            rows = db.execute(query, (*scopes, limit + 1, offset)).fetchall()
        more = len(rows) > limit
        return {"items": [self.decode(row) for row in rows[:limit]], "has_more": more,
                "next_offset": offset + limit if more else None}

    def transition(self, oid, expected, target, data=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM operations WHERE id=?", (oid,)).fetchone()
            if not row or row["state"] not in expected:
                raise BaseError("operation_state_conflict")
            value = data if data is not None else json.loads(row["data"])
            pid = os.getpid() if target in ("preparing", "applying", "submitting") else None
            generation = process_generation(pid) if pid else None
            if pid and generation in (None, "unverified"):
                raise BaseError("worker_generation_unverified")
            db.execute("UPDATE operations SET state=?,data=?,updated=?,run_pid=?,run_token=? WHERE id=?", (target, json.dumps(value, ensure_ascii=False), time.time(), pid, generation, oid))
            db.execute("INSERT INTO events(operation_id,event,created) VALUES(?,?,?)", (oid, target, time.time()))
        return self.get(oid)

    def listing(self):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM operations ORDER BY created DESC LIMIT 200")]

    def all_operations(self):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM operations")]

    def events(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 200")]

    @contextmanager
    def resource_lock(self, resource):
        owner = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM resource_locks WHERE resource=?", (resource,)).fetchone()
            if previous:
                generation = process_generation(previous["pid"])
                if generation == "unverified" or (previous["run_token"] and generation == previous["run_token"]):
                    raise BaseError("resource_operation_in_progress")
                db.execute("DELETE FROM resource_locks WHERE resource=? AND owner=?", (resource, previous["owner"]))
            generation = process_generation(os.getpid())
            if generation in (None, "unverified"):
                raise BaseError("worker_generation_unverified")
            db.execute("INSERT INTO resource_locks(resource,owner,pid,run_token) VALUES(?,?,?,?)", (resource, owner, os.getpid(), generation))
        try:
            yield
        finally:
            with self.connect() as db:
                db.execute("DELETE FROM resource_locks WHERE resource=? AND owner=?", (resource, owner))
