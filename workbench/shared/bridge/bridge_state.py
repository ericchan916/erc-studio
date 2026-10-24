"""SQLite persistence: sessions, events, auth queue, pending inputs."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from bridge_utils import utc_now


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    chat_id TEXT PRIMARY KEY,
                    chat_type TEXT NOT NULL,
                    cherry_session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    response_text TEXT,
                    cherry_session_id TEXT,
                    agent_resume_id TEXT,
                    exchange_persisted INTEGER NOT NULL DEFAULT 0,
                    execution_state TEXT NOT NULL DEFAULT 'not_started',
                    stream_card_id TEXT,
                    stream_message_id TEXT,
                    stream_update_count INTEGER NOT NULL DEFAULT 0,
                    stream_started_at TEXT,
                    stream_completed_at TEXT,
                    next_retry_at REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_retry_idx
                ON events(status, next_retry_at);
                CREATE INDEX IF NOT EXISTS events_message_idx
                ON events(chat_id, message_id);
                CREATE TABLE IF NOT EXISTS poll_cursors (
                    chat_id TEXT PRIMARY KEY,
                    last_position INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_requests (
                    auth_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    device_code TEXT NOT NULL,
                    verification_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_requests_status_idx
                ON auth_requests(status, expires_at);
                CREATE TABLE IF NOT EXISTS pending_inputs (
                    message_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS pending_inputs_lookup_idx
                ON pending_inputs(chat_id, sender_id, status, expires_at);
                CREATE TABLE IF NOT EXISTS workflow_contexts (
                    chat_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    active_workflow_id TEXT NOT NULL,
                    subject_type TEXT,
                    subject_id TEXT,
                    subject_title TEXT,
                    stage TEXT,
                    pending_action TEXT,
                    context_json TEXT NOT NULL,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS event_reconciliations (
                    event_id TEXT PRIMARY KEY,
                    outcome TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    reconciled_at TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(events)")
            }
            additions = {
                "response_text": "TEXT",
                "cherry_session_id": "TEXT",
                "agent_resume_id": "TEXT",
                "exchange_persisted": "INTEGER NOT NULL DEFAULT 0",
                # Do not infer that pre-migration processing events never ran.
                "execution_state": "TEXT NOT NULL DEFAULT 'legacy'",
                "stream_card_id": "TEXT",
                "stream_message_id": "TEXT",
                "stream_update_count": "INTEGER NOT NULL DEFAULT 0",
                "stream_started_at": "TEXT",
                "stream_completed_at": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE events ADD COLUMN {name} {declaration}"
                    )

    def session_for(self, chat_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cherry_session_id FROM chat_sessions WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def set_session(self, chat_id: str, chat_type: str, session_id: str) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions(
                    chat_id, chat_type, cherry_session_id, created_at, updated_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_type=excluded.chat_type,
                    cherry_session_id=excluded.cherry_session_id,
                    updated_at=excluded.updated_at
                """,
                (chat_id, chat_type, session_id, now, now),
            )

    def set_workflow_context(self, value: Dict[str, Any]) -> Dict[str, Any]:
        chat_id = str(value.get("chat_id") or "").strip()
        workflow_id = str(value.get("active_workflow_id") or "").strip()
        if not chat_id or not workflow_id:
            raise ValueError("workflow_context_identity_missing")
        subject = value.get("subject") or {}
        if not isinstance(subject, dict):
            raise ValueError("workflow_context_subject_invalid")
        context = value.get("context") or {}
        if not isinstance(context, dict):
            raise ValueError("workflow_context_payload_invalid")
        expires_at = str(value.get("expires_at") or "").strip() or None
        if expires_at:
            try:
                parsed = dt.datetime.fromisoformat(expires_at)
            except ValueError as error:
                raise ValueError("workflow_context_expiry_invalid") from error
            if parsed.tzinfo is None:
                raise ValueError("workflow_context_expiry_timezone_missing")
            if parsed.astimezone(dt.timezone.utc) <= dt.datetime.now(dt.timezone.utc):
                raise ValueError("workflow_context_expiry_not_future")
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_contexts(
                    chat_id,version,active_workflow_id,subject_type,subject_id,
                    subject_title,stage,pending_action,context_json,expires_at,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    version=excluded.version,
                    active_workflow_id=excluded.active_workflow_id,
                    subject_type=excluded.subject_type,
                    subject_id=excluded.subject_id,
                    subject_title=excluded.subject_title,
                    stage=excluded.stage,
                    pending_action=excluded.pending_action,
                    context_json=excluded.context_json,
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (
                    chat_id,
                    int(value.get("version") or 1),
                    workflow_id,
                    str(subject.get("type") or ""),
                    str(subject.get("id") or ""),
                    str(subject.get("title") or ""),
                    str(value.get("stage") or ""),
                    str(value.get("pending_action") or ""),
                    json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                    expires_at,
                    now,
                    now,
                ),
            )
        result = self.workflow_context(chat_id)
        if not result:
            raise RuntimeError("workflow_context_write_verify_failed")
        return result

    @staticmethod
    def _expired(expires_at: Optional[str]) -> bool:
        if not expires_at:
            return False
        try:
            value = dt.datetime.fromisoformat(expires_at)
        except ValueError:
            return True
        if value.tzinfo is None:
            return True
        return value.astimezone(dt.timezone.utc) <= dt.datetime.now(dt.timezone.utc)

    def workflow_context(self, chat_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_contexts WHERE chat_id=?", (chat_id,)
            ).fetchone()
            if not row:
                return None
            if self._expired(row["expires_at"]):
                conn.execute("DELETE FROM workflow_contexts WHERE chat_id=?", (chat_id,))
                return None
        return {
            "version": int(row["version"]),
            "chat_id": str(row["chat_id"]),
            "active_workflow_id": str(row["active_workflow_id"]),
            "subject": {
                "type": str(row["subject_type"] or ""),
                "id": str(row["subject_id"] or ""),
                "title": str(row["subject_title"] or ""),
            },
            "stage": str(row["stage"] or ""),
            "pending_action": str(row["pending_action"] or ""),
            "context": json.loads(row["context_json"] or "{}"),
            "updated_at": str(row["updated_at"]),
            "expires_at": str(row["expires_at"] or "") or None,
        }

    def clear_workflow_context(
        self, chat_id: str, subject_id: Optional[str] = None
    ) -> bool:
        with self._connect() as conn:
            if subject_id is None:
                cursor = conn.execute(
                    "DELETE FROM workflow_contexts WHERE chat_id=?", (chat_id,)
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM workflow_contexts WHERE chat_id=? AND subject_id=?",
                    (chat_id, subject_id),
                )
        return cursor.rowcount == 1

    def add_event(
        self, event: Dict[str, Any], pending_limit: int = 0,
        poll_position: Optional[int] = None,
    ) -> bool:
        event_id = str(event.get("event_id") or event.get("message_id") or "")
        if not event_id:
            return False
        now = utc_now()
        chat_id = str(event.get("chat_id") or "")
        message_id = str(event.get("message_id") or event_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Keep historical duplicates intact; serialize the lookup + insert
            # instead of adding a unique index that would fail on old databases.
            exists = conn.execute(
                "SELECT 1 FROM events WHERE event_id=? OR (chat_id=? AND message_id=?) LIMIT 1",
                (event_id, chat_id, message_id),
            ).fetchone()
            if exists:
                if poll_position is not None:
                    self._set_poll_cursor(conn, chat_id, poll_position)
                return False
            if pending_limit > 0:
                pending = self._consume_pending_inputs(
                    conn, chat_id, str(event.get("sender_id") or ""), event_id, pending_limit
                )
                if pending:
                    event = dict(event, pending_inputs=pending)
            cur = conn.execute(
                """
                INSERT INTO events(
                    event_id, message_id, chat_id, sender_id, payload_json,
                    status, attempts, execution_state, created_at, updated_at
                ) VALUES(?,?,?,?,?,'received',0,'not_started',?,?)
                """,
                (
                    event_id,
                    message_id,
                    chat_id,
                    str(event.get("sender_id") or ""),
                    json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            if poll_position is not None:
                self._set_poll_cursor(conn, chat_id, poll_position)
            return cur.rowcount == 1

    def claim_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        now_ts = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM events
                WHERE event_id=? AND status IN ('received','failed')
                  AND next_retry_at<=?
                """,
                (event_id, now_ts),
            ).fetchone()
            if not row:
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE events SET status='processing', attempts=attempts+1,
                    updated_at=? WHERE event_id=?
                """,
                (utc_now(), event_id),
            )
            conn.commit()
            return json.loads(row["payload_json"])

    def mark_done(self, event_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events SET status='done', last_error=NULL,
                    next_retry_at=0, updated_at=? WHERE event_id=?
                """,
                (utc_now(), event_id),
            )

    def result_for(self, event_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT response_text, cherry_session_id, agent_resume_id,
                       exchange_persisted, execution_state
                FROM events WHERE event_id=?
                """,
                (event_id,),
            ).fetchone()
        return dict(row) if row else {}

    def save_inference(
        self,
        event_id: str,
        session_id: str,
        response_text: str,
        agent_resume_id: str,
        exchange_persisted: bool = False,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events SET response_text=?, cherry_session_id=?,
                    agent_resume_id=?, exchange_persisted=?, execution_state='completed', updated_at=?
                WHERE event_id=?
                """,
                (
                    response_text,
                    session_id,
                    agent_resume_id,
                    1 if exchange_persisted else 0,
                    utc_now(),
                    event_id,
                ),
            )

    def mark_exchange_persisted(self, event_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events SET exchange_persisted=1, updated_at=?
                WHERE event_id=?
                """,
                (utc_now(), event_id),
            )

    def mark_execution_started(self, event_id: str, session_id: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET execution_state='executing',"
                "cherry_session_id=COALESCE(NULLIF(?,''),cherry_session_id),updated_at=? "
                "WHERE event_id=? AND status='processing'",
                (session_id, utc_now(), event_id),
            )

    def mark_execution_unknown(self, event_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET status='execution_unknown',execution_state='unknown',"
                "last_error='Agent execution outcome unknown; manual reconciliation required',"
                "next_retry_at=0,updated_at=? WHERE event_id=?",
                (utc_now(), event_id),
            )

    def reconcile_execution_unknown(
        self, event_id: str, outcome: str, evidence: str
    ) -> Dict[str, Any]:
        allowed = {"committed", "no_effect", "superseded"}
        outcome = str(outcome or "").strip()
        evidence = str(evidence or "").strip()
        if outcome not in allowed:
            raise ValueError("reconciliation_outcome_invalid")
        if not evidence or len(evidence) > 4000:
            raise ValueError("reconciliation_evidence_invalid")
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if not row:
                raise ValueError("reconciliation_event_missing")
            existing = conn.execute(
                "SELECT outcome,evidence,reconciled_at FROM event_reconciliations WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing:
                if (
                    row["status"] != "reconciled"
                    or existing["outcome"] != outcome
                    or existing["evidence"] != evidence
                ):
                    raise ValueError("reconciliation_conflict")
                conn.commit()
                return {
                    "event_id": event_id,
                    "outcome": str(existing["outcome"]),
                    "evidence": str(existing["evidence"]),
                    "reconciled_at": str(existing["reconciled_at"]),
                    "deduplicated": True,
                }
            if row["status"] != "execution_unknown":
                raise ValueError("reconciliation_status_invalid")
            conn.execute(
                "INSERT INTO event_reconciliations(event_id,outcome,evidence,reconciled_at) "
                "VALUES(?,?,?,?)",
                (event_id, outcome, evidence, now),
            )
            conn.execute(
                "UPDATE events SET status='reconciled',execution_state=?,last_error=NULL,"
                "next_retry_at=0,updated_at=? WHERE event_id=?",
                (f"reconciled_{outcome}", now, event_id),
            )
            conn.commit()
        return {
            "event_id": event_id,
            "outcome": outcome,
            "evidence": evidence,
            "reconciled_at": now,
            "deduplicated": False,
        }

    def mark_execution_rejected(self, event_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET execution_state='not_started',updated_at=? WHERE event_id=?",
                (utc_now(), event_id),
            )

    def save_stream_start(
        self, event_id: str, card_id: str, message_id: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events SET stream_card_id=?,stream_message_id=?,
                    stream_update_count=0,stream_started_at=?,
                    stream_completed_at=NULL,updated_at=? WHERE event_id=?
                """,
                (card_id, message_id, utc_now(), utc_now(), event_id),
            )

    def save_stream_complete(self, event_id: str, update_count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events SET stream_update_count=?,stream_completed_at=?,
                    updated_at=? WHERE event_id=?
                """,
                (update_count, utc_now(), utc_now(), event_id),
            )

    def mark_failed(self, event_id: str, error: str, delay_seconds: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events SET status='failed', last_error=?,
                    next_retry_at=?, updated_at=? WHERE event_id=?
                """,
                (error[:1000], time.time() + delay_seconds, utc_now(), event_id),
            )

    def wait_for_auth(
        self,
        auth_id: str,
        event_id: str,
        chat_id: str,
        sender_id: str,
        scopes: List[str],
        device_code: str,
        verification_url: str,
        expires_at: float,
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO auth_requests(
                    auth_id,event_id,chat_id,sender_id,scopes_json,
                    device_code,verification_url,status,expires_at,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'pending',?,?,?)
                ON CONFLICT(event_id) DO UPDATE SET
                    auth_id=excluded.auth_id,
                    scopes_json=excluded.scopes_json,
                    device_code=excluded.device_code,
                    verification_url=excluded.verification_url,
                    status='pending',
                    expires_at=excluded.expires_at,
                    last_error=NULL,
                    updated_at=excluded.updated_at
                """,
                (
                    auth_id,
                    event_id,
                    chat_id,
                    sender_id,
                    json.dumps(scopes, separators=(",", ":")),
                    device_code,
                    verification_url,
                    expires_at,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE events SET status='waiting_auth', last_error=NULL, execution_state='awaiting_auth',
                    next_retry_at=0, updated_at=? WHERE event_id=?
                """,
                (now, event_id),
            )
            conn.commit()

    def pending_auth_requests(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM auth_requests
                WHERE status='pending' AND expires_at>? ORDER BY created_at
                """,
                (time.time(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def complete_auth(self, auth_id: str) -> Optional[Dict[str, str]]:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT event_id,chat_id FROM auth_requests WHERE auth_id=?",
                (auth_id,),
            ).fetchone()
            if not row:
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE auth_requests SET status='authorized',last_error=NULL,
                    updated_at=? WHERE auth_id=?
                """,
                (now, auth_id),
            )
            conn.execute(
                """
                UPDATE events SET status='received',last_error=NULL,
                    next_retry_at=0,response_text=NULL,agent_resume_id=NULL,
                    exchange_persisted=0,execution_state='not_started',updated_at=? WHERE event_id=?
                """,
                (now, row["event_id"]),
            )
            conn.commit()
            return {"event_id": str(row["event_id"]), "chat_id": str(row["chat_id"])}

    def fail_auth(self, auth_id: str, error: str) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT event_id FROM auth_requests WHERE auth_id=?", (auth_id,)
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE auth_requests SET status='expired',last_error=?,
                        updated_at=? WHERE auth_id=?
                    """,
                    (error[:1000], now, auth_id),
                )
                conn.execute(
                    """
                    UPDATE events SET status='auth_expired',last_error=?,
                        updated_at=? WHERE event_id=?
                    """,
                    (error[:1000], now, row["event_id"]),
                )
            conn.commit()

    def retryable(self, max_retries: int) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, chat_id FROM events
                WHERE status IN ('received','failed') AND attempts<?
                  AND next_retry_at<=? ORDER BY created_at ASC LIMIT 100
                """,
                (max_retries, time.time()),
            ).fetchall()
        return [dict(row) for row in rows]

    def wait_isolated_auth(self, event_id):
        with self._connect() as conn:
            conn.execute("UPDATE events SET status='waiting_isolated_auth',execution_state='awaiting_auth',last_error=NULL,updated_at=? WHERE event_id=? AND status='processing'", (utc_now(),event_id))

    def resume_isolated_auth(self, event_id, success=True):
        with self._connect() as conn:
            return bool(conn.execute(
                "UPDATE events SET status=?,execution_state='not_started',next_retry_at=0,updated_at=? WHERE event_id=? AND status='waiting_isolated_auth'",
                ('received' if success else 'auth_expired',utc_now(),event_id)).rowcount)

    def reset_stale(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events SET status='execution_unknown',execution_state='unknown',
                    next_retry_at=0,
                    last_error='Agent execution interrupted; manual reconciliation required',updated_at=?
                WHERE status='processing' AND COALESCE(response_text,'')=''
                    AND execution_state IN ('executing','legacy')
                """,
                (utc_now(),),
            )
            conn.execute(
                """
                UPDATE events SET status='failed', next_retry_at=0,
                    last_error='bridge restarted during processing', updated_at=?
                WHERE status='processing'
                """,
                (utc_now(),),
            )

    def metrics(self, max_retries: int = 5) -> Dict[str, int]:
        with self._connect() as conn:
            sessions = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
            events = conn.execute(
                """
                SELECT
                    COALESCE(SUM(status NOT IN ('done','reconciled')), 0) AS unfinished,
                    COALESCE(SUM(status IN ('processing','waiting_auth','waiting_isolated_auth') OR
                        (status IN ('received','failed') AND attempts < ?)), 0) AS pending,
                    COALESCE(SUM(status IN ('received','failed') AND attempts < ?
                        AND next_retry_at <= ?), 0) AS retryable,
                    COALESCE(SUM(status IN ('received','failed') AND attempts >= ?), 0) AS exhausted,
                    COALESCE(SUM(status='execution_unknown'), 0) AS execution_unknown,
                    COALESCE(SUM(status='reconciled'), 0) AS reconciled
                FROM events
                """,
                (max_retries, max_retries, time.time(), max_retries),
            ).fetchone()
            pending_auth = conn.execute(
                "SELECT COUNT(*) FROM auth_requests WHERE status='pending'"
            ).fetchone()[0]
            pending_inputs = conn.execute(
                "SELECT COUNT(*) FROM pending_inputs WHERE status='pending' AND expires_at>?",
                (time.time(),),
            ).fetchone()[0]
            workflow_contexts = conn.execute(
                "SELECT expires_at FROM workflow_contexts"
            ).fetchall()
            active_contexts = sum(
                1 for row in workflow_contexts if not self._expired(row[0])
            )
        return {
            "session_count": int(sessions),
            "pending_event_count": int(events["pending"]),
            "retryable_event_count": int(events["retryable"]),
            "exhausted_event_count": int(events["exhausted"]),
            "terminal_event_count": int(events["unfinished"] - events["pending"]),
            "execution_unknown_event_count": int(events["execution_unknown"]),
            "reconciled_event_count": int(events["reconciled"]),
            "pending_auth_count": int(pending_auth),
            "pending_input_count": int(pending_inputs),
            "active_workflow_context_count": int(active_contexts),
        }

    def stage_pending_input(
        self, event: Dict[str, Any], ttl_seconds: int,
        poll_position: Optional[int] = None,
    ) -> bool:
        message_id = str(event.get("message_id") or "")
        chat_id = str(event.get("chat_id") or "")
        sender_id = str(event.get("sender_id") or "")
        if not message_id or not chat_id or not sender_id:
            return False
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE pending_inputs SET status='expired',updated_at=? "
                "WHERE status='pending' AND expires_at<=?",
                (now, time.time()),
            )
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO pending_inputs(
                    message_id,chat_id,sender_id,payload_json,status,expires_at,
                    created_at,updated_at
                ) VALUES(?,?,?,?,'pending',?,?,?)
                """,
                (
                    message_id,
                    chat_id,
                    sender_id,
                    json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                    time.time() + max(1, int(ttl_seconds)),
                    now,
                    now,
                ),
            )
            if poll_position is not None:
                self._set_poll_cursor(conn, chat_id, poll_position)
        return cursor.rowcount == 1

    def consume_pending_inputs(
        self,
        chat_id: str,
        sender_id: str,
        consumer_id: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._consume_pending_inputs(conn, chat_id, sender_id, consumer_id, limit)

    @staticmethod
    def _consume_pending_inputs(
        conn: sqlite3.Connection, chat_id: str, sender_id: str,
        consumer_id: str, limit: int,
    ) -> List[Dict[str, Any]]:
        now_ts = time.time()
        now = utc_now()
        conn.execute(
            "UPDATE pending_inputs SET status='expired',updated_at=? "
            "WHERE status='pending' AND expires_at<=?",
            (now, now_ts),
        )
        rows = conn.execute(
            """
            SELECT message_id,payload_json FROM pending_inputs
            WHERE chat_id=? AND sender_id=? AND status='pending' AND expires_at>?
            ORDER BY created_at ASC LIMIT ?
            """,
            (chat_id, sender_id, now_ts, max(1, int(limit))),
        ).fetchall()
        if rows:
            conn.executemany(
                "UPDATE pending_inputs SET status='consumed',consumed_by=?,updated_at=? "
                "WHERE message_id=? AND status='pending'",
                [(consumer_id, now, str(row["message_id"])) for row in rows],
            )
        return [json.loads(row["payload_json"]) for row in rows]

    def poll_cursor(self, chat_id: str) -> Optional[int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_position FROM poll_cursors WHERE chat_id=?", (chat_id,)
            ).fetchone()
        return int(row[0]) if row else None

    def set_poll_cursor(self, chat_id: str, position: int) -> None:
        with self._connect() as conn:
            self._set_poll_cursor(conn, chat_id, position)

    @staticmethod
    def _set_poll_cursor(conn: sqlite3.Connection, chat_id: str, position: int) -> None:
        conn.execute(
            """
            INSERT INTO poll_cursors(chat_id,last_position,updated_at)
            VALUES(?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
                last_position=MAX(last_position,excluded.last_position),
                updated_at=excluded.updated_at
            """,
            (chat_id, position, utc_now()),
        )
