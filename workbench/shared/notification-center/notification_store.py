#!/usr/bin/env python3
"""SQLite persistence for the Cyber ContentBase notification center."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from notification_state import STATE_PENDING, ensure_transition

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  card_json TEXT NOT NULL,
  dedup_key TEXT,
  dedup_policy TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL,
  next_attempt_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sent_at TEXT,
  expires_at TEXT,
  last_error TEXT,
  message_id TEXT
);
CREATE INDEX IF NOT EXISTS notifications_due
  ON notifications(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS notifications_dedup
  ON notifications(dedup_key, state);
CREATE TABLE IF NOT EXISTS dedup_registry(
  dedup_key TEXT PRIMARY KEY,
  policy TEXT NOT NULL,
  notification_id INTEGER NOT NULL,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  notification_id INTEGER NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  reason TEXT,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_notification
  ON events(notification_id, id);
"""

REGISTRY_ACTIVE = "active"
REGISTRY_RESOLVED = "resolved"
REGISTRY_PERMANENT = "permanent"


def to_storage(value: dt.datetime) -> str:
    moment = value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def from_storage(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


class NotificationStore:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        with self.connection:
            self.connection.executescript(SCHEMA)
            row = self.connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row["value"]) != SCHEMA_VERSION:
                raise ValueError(f"schema_version_unsupported:{row['value']}")

    def _row_to_notification(self, row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["card"] = json.loads(value.pop("card_json"))
        return value

    # -- notification writes -------------------------------------------------

    def insert_notification(
        self,
        *,
        uuid_value: str,
        source: str,
        channel_id: str,
        chat_id: str,
        card: dict[str, Any],
        dedup_key: str | None,
        dedup_policy: str,
        max_attempts: int,
        expires_at: dt.datetime | None,
        now: dt.datetime,
    ) -> int:
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO notifications(
                  uuid, source, channel_id, chat_id, card_json, dedup_key,
                  dedup_policy, state, attempts, max_attempts, next_attempt_at,
                  created_at, updated_at, expires_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    uuid_value,
                    source,
                    channel_id,
                    chat_id,
                    json.dumps(card, ensure_ascii=False, separators=(",", ":")),
                    dedup_key,
                    dedup_policy,
                    STATE_PENDING,
                    0,
                    max_attempts,
                    to_storage(now),
                    to_storage(now),
                    to_storage(now),
                    to_storage(expires_at) if expires_at else None,
                ),
            )
            notification_id = int(cursor.lastrowid)
        self.log_event(
            notification_id, None, STATE_PENDING, "submitted", now=now
        )
        return notification_id

    def apply_transition(
        self,
        notification_id: int,
        target: str,
        *,
        now: dt.datetime,
        reason: str | None = None,
        detail: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = dict(fields or {})
        with self.connection:
            row = self.connection.execute(
                "SELECT state FROM notifications WHERE id=?", (notification_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"notification_not_found:{notification_id}")
            current = str(row["state"])
            ensure_transition(current, target)
            assignments = ["state=?", "updated_at=?"]
            values: list[Any] = [target, to_storage(now)]
            for key, value in updates.items():
                if isinstance(value, dt.datetime):
                    value = to_storage(value)
                assignments.append(f"{key}=?")
                values.append(value)
            values.append(notification_id)
            self.connection.execute(
                f"UPDATE notifications SET {', '.join(assignments)} WHERE id=?", values
            )
        self.log_event(notification_id, current, target, reason, detail, now)
        return self.get_notification(notification_id) or {}

    def log_event(
        self,
        notification_id: int,
        from_state: str | None,
        to_state: str,
        reason: str | None,
        detail: str | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        moment = to_storage(now or dt.datetime.now(dt.timezone.utc))
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO events(notification_id, from_state, to_state, reason, detail, created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (notification_id, from_state, to_state, reason, detail, moment),
            )

    # -- notification reads --------------------------------------------------

    def get_notification(self, notification_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM notifications WHERE id=?", (notification_id,)
        ).fetchone()
        return self._row_to_notification(row) if row else None

    def find_by_uuid(self, uuid_value: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM notifications WHERE uuid=?", (uuid_value,)
        ).fetchone()
        return self._row_to_notification(row) if row else None

    def due_pending(self, now: dt.datetime, limit: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM notifications
            WHERE state=? AND (next_attempt_at IS NULL OR next_attempt_at<=?)
            ORDER BY COALESCE(next_attempt_at, created_at), id
            LIMIT ?
            """,
            (STATE_PENDING, to_storage(now), limit),
        ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def expirable(self, now: dt.datetime) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM notifications
            WHERE state IN ('pending','failed')
              AND expires_at IS NOT NULL AND expires_at<=?
            ORDER BY id
            """,
            (to_storage(now),),
        ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def stale_sending(self, now: dt.datetime, timeout_seconds: int) -> list[dict[str, Any]]:
        cutoff = to_storage(now - dt.timedelta(seconds=timeout_seconds))
        rows = self.connection.execute(
            "SELECT * FROM notifications WHERE state='sending' AND updated_at<? ORDER BY id",
            (cutoff,),
        ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def pending_by_dedup_key(self, source: str, dedup_key: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM notifications WHERE source=? AND dedup_key=? AND state IN ('pending','sending') ORDER BY id",
            (source, dedup_key),
        ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def events(self, notification_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM events WHERE notification_id=? ORDER BY id DESC LIMIT ?",
            (notification_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def delivery_backlog(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM notifications WHERE state IN ('pending','sending','failed') ORDER BY id"
        ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def state_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT state, COUNT(*) AS total FROM notifications GROUP BY state"
        ).fetchall()
        counts = {row["state"]: int(row["total"]) for row in rows}
        return counts

    # -- dedup registry --------------------------------------------------

    @staticmethod
    def registry_key(source: str, dedup_key: str) -> str:
        return f"{source}\n{dedup_key}"

    def get_registry(self, source: str, dedup_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM dedup_registry WHERE dedup_key=?",
            (self.registry_key(source, dedup_key),),
        ).fetchone()
        return dict(row) if row else None

    def registries_for_source(self, source: str) -> list[dict[str, Any]]:
        prefix = self.registry_key(source, "")
        rows = self.connection.execute(
            "SELECT * FROM dedup_registry WHERE substr(dedup_key,1,?)=? ORDER BY dedup_key",
            (len(prefix), prefix),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_registry(
        self,
        *,
        source: str,
        dedup_key: str,
        policy: str,
        notification_id: int,
        state: str,
        now: dt.datetime,
    ) -> None:
        key = self.registry_key(source, dedup_key)
        resolved_at = to_storage(now) if state == REGISTRY_RESOLVED else None
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO dedup_registry(dedup_key, policy, notification_id, state, created_at, updated_at, resolved_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(dedup_key) DO UPDATE SET
                  policy=excluded.policy,
                  notification_id=excluded.notification_id,
                  state=excluded.state,
                  updated_at=excluded.updated_at,
                  resolved_at=excluded.resolved_at
                """,
                (key, policy, notification_id, state, to_storage(now), to_storage(now), resolved_at),
            )

    def set_registry_state(
        self, source: str, dedup_key: str, state: str, now: dt.datetime
    ) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE dedup_registry SET state=?, updated_at=?, resolved_at=? WHERE dedup_key=?",
                (
                    state,
                    to_storage(now),
                    to_storage(now) if state == REGISTRY_RESOLVED else None,
                    self.registry_key(source, dedup_key),
                ),
            )

    def registry_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT state, COUNT(*) AS total FROM dedup_registry GROUP BY state"
        ).fetchall()
        return {row["state"]: int(row["total"]) for row in rows}

    def registry_notification(self, source: str, dedup_key: str) -> dict[str, Any] | None:
        registry = self.get_registry(source, dedup_key)
        if not registry:
            return None
        return self.get_notification(int(registry["notification_id"]))
