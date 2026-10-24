#!/usr/bin/env python3
"""Unified notification queue, deduplication and CardKit delivery orchestration."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from notification_cards import build_card, validate_card
from notification_sender import LarkCliCardSender
from notification_state import (
    STATE_CANCELLED,
    STATE_EXPIRED,
    STATE_FAILED,
    STATE_PENDING,
    STATE_SENDING,
    STATE_SENT,
    InvalidTransition,
)
from notification_store import (
    REGISTRY_ACTIVE,
    REGISTRY_PERMANENT,
    REGISTRY_RESOLVED,
    NotificationStore,
)

NAMESPACE = uuid.UUID("7d1bfa28-9c2e-4f0a-b6d3-2c5f0a9e8d21")
DEDUP_POLICIES = ("active", "once")


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return {} if default is None else default
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


class NotificationCenter:
    def __init__(
        self,
        config_path: Path,
        store: NotificationStore | None = None,
        sender: Any = None,
        now: dt.datetime | None = None,
        config_data: dict[str, Any] | None = None,
    ):
        self.config_path = Path(config_path).expanduser().resolve()
        self.config = dict(config_data) if config_data is not None else load_json(self.config_path)
        self.zone = ZoneInfo(str(self.config.get("time_zone") or "Asia/Shanghai"))
        self._validate()
        delivery = self.config.get("delivery") if isinstance(self.config.get("delivery"), dict) else {}
        self.batch_limit = self._delivery_int(delivery, "batch_limit", 10)
        self.max_attempts = self._delivery_int(delivery, "max_attempts", 3)
        self.retry_backoff_seconds = self._delivery_int(delivery, "retry_backoff_seconds", 300)
        self.sending_timeout_seconds = self._delivery_int(delivery, "sending_timeout_seconds", 120)
        self.default_dedup_policy = str(self.config.get("default_dedup_policy") or "active")
        self.database_path = self._path(self.config.get("database_path"), "notification_center.db")
        raw_health = self.config.get("health_path", "health.json")
        self.health_path = None if raw_health is None else self._path(raw_health, "health.json")
        self._owns_store = store is None
        self.store = store or NotificationStore(self.database_path)
        self.sender = sender or LarkCliCardSender(
            str(self.config["lark_cli"]), str(self.config["feishu_profile"])
        )
        self.now = (now or dt.datetime.now(self.zone)).astimezone(self.zone)

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def delivery_receipt(self, notification_id: int) -> dict[str, Any]:
        """Read one durable receipt; process() statistics never prove delivery.

        Legacy rows marked sent without a non-empty platform message ID remain
        unconfirmed. This method is read-only and never substitutes a local ID.
        """
        item = self.store.get_notification(int(notification_id))
        state = str(item["state"]) if item else "missing"
        raw_id = item.get("message_id") if item else None
        message_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else None
        return {
            "notification_id": int(notification_id),
            "state": state,
            "message_id": message_id,
            "delivered": state == STATE_SENT and message_id is not None,
        }

    def delivery_health(self, now: dt.datetime | None = None) -> dict[str, Any]:
        """Read current backlog, excluding recovered/superseded historical faults."""
        moment = self._now(now)
        result = {"pending_count": 0, "sending_count": 0, "dead_letter_count": 0,
                  "oldest_pending_age_seconds": 0}
        for item in self.store.delivery_backlog():
            if item["state"] == STATE_FAILED:
                registry = (self.store.get_registry(item["source"], str(item["dedup_key"]))
                            if item.get("dedup_key") else None)
                if registry and int(registry["notification_id"]) != int(item["id"]):
                    continue
                result["dead_letter_count"] += 1
                continue
            if self._obsolete_registry(item):
                continue
            result["pending_count" if item["state"] == STATE_PENDING else "sending_count"] += 1
            created = parse_time(item.get("created_at"))
            if created:
                result["oldest_pending_age_seconds"] = max(
                    result["oldest_pending_age_seconds"], int((moment - created).total_seconds())
                )
        return result

    def _path(self, value: Any, fallback: str) -> Path:
        path = Path(str(value or fallback)).expanduser()
        return path if path.is_absolute() else (self.config_path.parent / path).resolve()

    @staticmethod
    def _delivery_int(delivery: dict[str, Any], key: str, fallback: int) -> int:
        value = delivery.get(key)
        return int(value) if value is not None else fallback

    def _validate(self) -> None:
        required = ("lark_cli", "feishu_profile", "channels")
        missing = [key for key in required if not self.config.get(key)]
        if missing:
            raise ValueError("config_missing:" + ",".join(missing))
        channels = self.config["channels"]
        if not isinstance(channels, list) or not channels:
            raise ValueError("channels_required")
        ids = []
        for channel in channels:
            if not isinstance(channel, dict) or not channel.get("id"):
                raise ValueError("channel_shape_invalid")
            if not channel.get("chat_id") and not channel.get("allow_dynamic_chat"):
                raise ValueError("channel_shape_invalid")
            ids.append(str(channel["id"]))
        if len(ids) != len(set(ids)):
            raise ValueError("channel_ids_duplicated")
        policy = str(self.config.get("default_dedup_policy") or "active")
        if policy not in DEDUP_POLICIES:
            raise ValueError("dedup_policy_invalid")

    def _channel(self, channel_id: str) -> dict[str, Any]:
        for channel in self.config["channels"]:
            if str(channel["id"]) == str(channel_id):
                return channel
        raise ValueError(f"unknown_channel:{channel_id}")

    def _now(self, value: dt.datetime | None = None) -> dt.datetime:
        moment = value or self.now
        return moment if moment.tzinfo else moment.replace(tzinfo=self.zone)

    def _idempotency_key(self, uuid_value: str) -> str:
        return str(uuid.uuid5(NAMESPACE, f"notification:{uuid_value}"))

    # -- submission -----------------------------------------------------------

    def submit(
        self,
        *,
        source: str,
        channel: str,
        card: dict[str, Any] | None = None,
        title: str | None = None,
        lines: list[str] | None = None,
        note: str | None = None,
        mentions: list[dict[str, Any]] | None | Any = None,
        dedup_key: str | None = None,
        dedup_policy: str | None = None,
        expires_at: dt.datetime | str | None = None,
        chat_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        if not str(source or "").strip():
            raise ValueError("source_required")
        channel_config = self._channel(channel)
        target_chat_id = str(channel_config.get("chat_id") or "")
        if chat_id is not None:
            if not channel_config.get("allow_dynamic_chat"):
                raise ValueError(f"dynamic_chat_not_allowed:{channel}")
            target_chat_id = str(chat_id)
            if not target_chat_id.startswith("oc_"):
                raise ValueError(f"dynamic_chat_id_invalid:{target_chat_id[:6]}")
        policy = str(dedup_policy or self.default_dedup_policy)
        if policy not in DEDUP_POLICIES:
            raise ValueError(f"dedup_policy_invalid:{policy}")
        if card is not None:
            final_card = card
        else:
            final_mentions = channel_config.get("mentions") if mentions is None else mentions
            final_card = build_card(title, lines, final_mentions, note)
        validate_card(final_card)
        if expires_at is None or isinstance(expires_at, dt.datetime):
            expiry: dt.datetime | None = expires_at
        else:
            expiry = parse_time(expires_at)
            if expiry is None:
                raise ValueError(f"expires_at_invalid:{expires_at}")
        moment = self._now(now)
        if dedup_key:
            duplicate = self._blocked_duplicate(source, dedup_key)
            if duplicate is not None:
                return duplicate
        notification_id = self.store.insert_notification(
            uuid_value=str(uuid.uuid4()),
            source=source,
            channel_id=str(channel_config["id"]),
            chat_id=target_chat_id,
            card=final_card,
            dedup_key=dedup_key,
            dedup_policy=policy,
            max_attempts=self.max_attempts,
            expires_at=expiry,
            now=moment,
        )
        if dedup_key:
            self.store.upsert_registry(
                source=source,
                dedup_key=dedup_key,
                policy=policy,
                notification_id=notification_id,
                state=REGISTRY_ACTIVE,
                now=moment,
            )
        return {
            "notification_id": notification_id,
            "duplicate": False,
            "state": STATE_PENDING,
            "dedup_key": dedup_key,
            "dedup_policy": policy,
        }

    def _blocked_duplicate(self, source: str, dedup_key: str) -> dict[str, Any] | None:
        registry = self.store.get_registry(source, dedup_key)
        if not registry:
            return None
        if registry["state"] == REGISTRY_PERMANENT:
            existing = self.store.get_notification(int(registry["notification_id"]))
            return self._duplicate_result(registry, existing)
        if registry["state"] != REGISTRY_ACTIVE:
            return None
        existing = self.store.get_notification(int(registry["notification_id"]))
        if existing and existing["state"] in (STATE_PENDING, STATE_SENDING, STATE_SENT):
            return self._duplicate_result(registry, existing)
        return None

    @staticmethod
    def _duplicate_result(registry: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "notification_id": int(registry["notification_id"]),
            "duplicate": True,
            "state": existing["state"] if existing else None,
            "dedup_key": registry["dedup_key"].split("\n", 1)[-1],
            "dedup_policy": registry["policy"],
        }

    # -- lifecycle control ------------------------------------------------------

    def resolve(self, source: str, dedup_key: str, now: dt.datetime | None = None) -> dict[str, Any]:
        moment = self._now(now)
        registry = self.store.get_registry(source, dedup_key)
        if not registry:
            return {"resolved": False, "reason": "unknown_key"}
        if registry["state"] == REGISTRY_PERMANENT:
            return {"resolved": False, "reason": "permanent"}
        self.store.set_registry_state(source, dedup_key, REGISTRY_RESOLVED, moment)
        cancelled: list[int] = []
        current = self.store.get_notification(int(registry["notification_id"]))
        if current and current["state"] == STATE_FAILED:
            self.store.apply_transition(
                int(current["id"]), STATE_CANCELLED, now=moment, reason="resolved_failed_delivery"
            )
            cancelled.append(int(current["id"]))
        for item in self.store.pending_by_dedup_key(source, dedup_key):
            if item["state"] != STATE_PENDING:
                continue
            self.store.apply_transition(
                int(item["id"]), STATE_CANCELLED, now=moment, reason="resolved_before_send"
            )
            cancelled.append(int(item["id"]))
        return {"resolved": True, "cancelled_notification_ids": cancelled}

    def resolve_source(self, source: str, now: dt.datetime | None = None) -> dict[str, Any]:
        resolved_keys: list[str] = []
        cancelled: list[int] = []
        for registry in self.store.registries_for_source(source):
            current = self.store.get_notification(int(registry["notification_id"]))
            if registry["state"] != REGISTRY_ACTIVE and not (current and current["state"] == STATE_FAILED):
                continue
            key = str(registry["dedup_key"]).split("\n", 1)[-1]
            outcome = self.resolve(source, key, now=now)
            if outcome.get("resolved"):
                resolved_keys.append(key)
                cancelled.extend(outcome.get("cancelled_notification_ids") or [])
        return {"resolved_keys": resolved_keys, "cancelled_notification_ids": cancelled}

    def cancel(
        self, notification_id: int, reason: str | None = None, now: dt.datetime | None = None
    ) -> dict[str, Any]:
        moment = self._now(now)
        item = self.store.get_notification(notification_id)
        if item is None:
            raise ValueError(f"notification_not_found:{notification_id}")
        current = item["state"]
        if current in (STATE_SENT, STATE_CANCELLED, STATE_EXPIRED):
            return {"cancelled": False, "state": current, "reason": "already_terminal"}
        if current != STATE_PENDING and current != STATE_FAILED:
            raise InvalidTransition(f"cancel_not_allowed:{current}")
        self.store.apply_transition(
            notification_id, STATE_CANCELLED, now=moment, reason=reason or "cancelled"
        )
        self._release_registry(item, moment)
        return {"cancelled": True, "state": STATE_CANCELLED}

    def _release_registry(self, item: dict[str, Any], moment: dt.datetime) -> None:
        if not item.get("dedup_key"):
            return
        registry = self.store.get_registry(item["source"], str(item["dedup_key"]))
        if registry and int(registry["notification_id"]) == int(item["id"]):
            self.store.set_registry_state(item["source"], str(item["dedup_key"]), REGISTRY_RESOLVED, moment)

    def _obsolete_registry(self, item: dict[str, Any]) -> bool:
        if not item.get("dedup_key"):
            return False
        registry = self.store.get_registry(item["source"], str(item["dedup_key"]))
        return bool(registry and (
            registry["state"] != REGISTRY_ACTIVE
            or int(registry["notification_id"]) != int(item["id"])
        ))

    def reconcile(self, now: dt.datetime | None = None) -> dict[str, list[int]]:
        """Recover interrupted delivery and expire stale intent, without sending.

        Live sending requests remain untouched. Resolving a key only prevents
        their retry after the delivery timeout; it does not retract a sent card.
        Callers may then refresh business-dependent pending payloads before process.
        """
        moment = self._now(now)
        result: dict[str, list[int]] = {"recovered": [], "expired": [], "cancelled": []}
        for item in self.store.stale_sending(moment, self.sending_timeout_seconds):
            obsolete = self._obsolete_registry(item)
            self._handle_failure(item, moment, "sending_timeout", "发送超时，进程可能中断")
            notification_id = int(item["id"])
            result["recovered"].append(notification_id)
            if obsolete:
                self.cancel(notification_id, reason="resolved_or_superseded_before_retry", now=moment)
                result["cancelled"].append(notification_id)
        # Query after recovery so even zero-backoff interrupted sends expire
        # before they can enter this cycle's delivery list.
        for item in self.store.expirable(moment):
            notification_id = int(item["id"])
            self.store.apply_transition(notification_id, STATE_EXPIRED, now=moment, reason="expired")
            self._release_registry(item, moment)
            result["expired"].append(notification_id)
        return result

    # -- delivery -----------------------------------------------------------------

    def process(
        self,
        now: dt.datetime | None = None,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        moment = self._now(now)
        batch = int(limit or self.batch_limit)
        try:
            summary = self._process(moment, batch, dry_run)
            if dry_run:
                return 0, summary
            state_counts = self.store.state_counts()
            backlog = self.delivery_health(now=moment)
            status = "degraded" if backlog["dead_letter_count"] or summary["expired"] else "ok"
            health = {
                **backlog,
                "status": status,
                "checked_at": moment.isoformat(),
                "sent": len(summary["sent"]),
                "retried": len(summary["retried"]),
                "failed": len(summary["failed"]),
                "expired": len(summary["expired"]),
                "recovered": len(summary["recovered"]),
                "pending": state_counts.get(STATE_PENDING, 0),
                "state_counts": state_counts,
                "last_error": None,
            }
            if self.health_path is not None:
                atomic_json(self.health_path, health)
            return 0, summary
        except Exception as error:
            message = f"{type(error).__name__}:{str(error)[:300]}"
            if self.health_path is not None:
                atomic_json(
                    self.health_path,
                    {
                        "status": "error",
                        "checked_at": moment.isoformat(),
                        "last_error": message,
                    },
                )
            return 2, {"error": message}

    def _process(
        self, moment: dt.datetime, batch: int, dry_run: bool
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "processed_at": moment.isoformat(),
            "dry_run": dry_run,
            "due": [],
            "sent": [],
            "retried": [],
            "failed": [],
            "expired": [],
            "recovered": [],
            "cancelled": [],
        }
        if dry_run:
            summary["due"] = [int(item["id"]) for item in self.store.due_pending(moment, batch)]
            summary["expirable"] = [int(item["id"]) for item in self.store.expirable(moment)]
            summary["stale_sending"] = [int(item["id"]) for item in self.store.stale_sending(moment, self.sending_timeout_seconds)]
            return summary
        summary.update(self.reconcile(now=moment))
        due = self.store.due_pending(moment, batch)
        summary["due"] = [int(item["id"]) for item in due]
        for item in due:
            if self._obsolete_registry(item):
                self.cancel(int(item["id"]), reason="resolved_or_superseded_before_send", now=moment)
                summary["cancelled"].append(int(item["id"]))
                continue
            state = self.store.apply_transition(
                int(item["id"]), STATE_SENDING, now=moment, reason="deliver"
            )
            try:
                outcome = self.sender.send(
                    str(state["chat_id"]), state["card"], self._idempotency_key(str(state["uuid"]))
                )
                message_id = outcome.get("message_id") if isinstance(outcome, dict) else None
                if not isinstance(message_id, str) or not message_id.strip():
                    raise RuntimeError("notification_delivery_missing_message_id")
                message_id = message_id.strip()
            except Exception as error:
                final = self._handle_failure(
                    state, moment, "delivery_error", f"{type(error).__name__}:{str(error)[:300]}"
                )
                if final["state"] == STATE_FAILED:
                    summary["failed"].append(int(item["id"]))
                else:
                    summary["retried"].append(int(item["id"]))
            else:
                self.store.apply_transition(
                    int(item["id"]),
                    STATE_SENT,
                    now=moment,
                    reason="delivered",
                    fields={"sent_at": moment, "message_id": message_id},
                )
                if state["dedup_key"] and state["dedup_policy"] == "once":
                    self.store.set_registry_state(
                        state["source"], str(state["dedup_key"]), REGISTRY_PERMANENT, moment
                    )
                summary["sent"].append(int(item["id"]))
        return summary

    def _handle_failure(
        self, item: dict[str, Any], moment: dt.datetime, reason: str, detail: str
    ) -> dict[str, Any]:
        attempts = int(item["attempts"]) + 1
        fields = {"attempts": attempts, "last_error": detail}
        if attempts >= int(item["max_attempts"]):
            failed = self.store.apply_transition(
                int(item["id"]), STATE_FAILED, now=moment, reason=reason, detail=detail, fields=fields
            )
            self._release_registry(failed, moment)
            return failed
        delay = dt.timedelta(seconds=self.retry_backoff_seconds * attempts)
        return self.store.apply_transition(
            int(item["id"]),
            STATE_PENDING,
            now=moment,
            reason=reason,
            detail=detail,
            fields={**fields, "next_attempt_at": moment + delay},
        )

    # -- queries ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        counts = self.store.state_counts()
        due = self.store.due_pending(self._now(), 1)
        if self.health_path is None:
            health: dict[str, Any] = {"status": "disabled"}
        else:
            health = load_json(self.health_path, {"status": "missing"})
        return {
            "state_counts": counts,
            "registry_counts": self.store.registry_counts(),
            "has_due": bool(due),
            "health": health,
        }

    def show(self, notification_id: int | None = None, source: str | None = None, dedup_key: str | None = None) -> dict[str, Any]:
        if notification_id is not None:
            item = self.store.get_notification(int(notification_id))
        elif source and dedup_key:
            item = self.store.registry_notification(source, dedup_key)
        else:
            raise ValueError("show_requires_id_or_dedup_key")
        if item is None:
            return {"found": False}
        return {"found": True, "notification": item, "events": self.store.events(int(item["id"]))}


def main() -> int:
    parser = argparse.ArgumentParser(description="内容助手统一通知中心")
    parser.add_argument("--config", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit_parser = subparsers.add_parser("submit", help="登记一条通知（只写本地队列）")
    submit_parser.add_argument("--source", required=True)
    submit_parser.add_argument("--channel", required=True)
    submit_parser.add_argument("--card-file", type=Path)
    submit_parser.add_argument("--title")
    submit_parser.add_argument("--line", action="append", default=[])
    submit_parser.add_argument("--note")
    submit_parser.add_argument("--no-channel-mentions", action="store_true")
    submit_parser.add_argument("--dedup-key")
    submit_parser.add_argument("--dedup-policy", choices=DEDUP_POLICIES)
    expiry = submit_parser.add_mutually_exclusive_group()
    expiry.add_argument("--ttl-seconds", type=int)
    expiry.add_argument("--expires-at")

    process_parser = subparsers.add_parser("process", help="投递到期通知")
    process_parser.add_argument("--limit", type=int)
    process_parser.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("status", help="查看队列状态")

    show_parser = subparsers.add_parser("show", help="查看通知与事件")
    show_parser.add_argument("--id", type=int)
    show_parser.add_argument("--source")
    show_parser.add_argument("--dedup-key")

    resolve_parser = subparsers.add_parser("resolve", help="解除去重键（恢复后允许复发提醒）")
    resolve_parser.add_argument("--source", required=True)
    resolve_parser.add_argument("--key", required=True)

    cancel_parser = subparsers.add_parser("cancel", help="取消未发送通知")
    cancel_parser.add_argument("--id", type=int, required=True)
    cancel_parser.add_argument("--reason")

    args = parser.parse_args()
    center = NotificationCenter(args.config)
    try:
        if args.command == "submit":
            card = None
            if args.card_file:
                card = json.loads(args.card_file.read_text())
            expires_at = args.expires_at
            if args.ttl_seconds is not None:
                expires_at = dt.datetime.now(center.zone) + dt.timedelta(seconds=args.ttl_seconds)
            result = center.submit(
                source=args.source,
                channel=args.channel,
                card=card,
                title=args.title,
                lines=args.line,
                note=args.note,
                mentions=[] if args.no_channel_mentions else None,
                dedup_key=args.dedup_key,
                dedup_policy=args.dedup_policy,
                expires_at=expires_at,
            )
            code = 0
        elif args.command == "process":
            code, result = center.process(limit=args.limit, dry_run=args.dry_run)
        elif args.command == "status":
            result, code = center.status(), 0
        elif args.command == "show":
            result, code = center.show(args.id, args.source, args.dedup_key), 0
        elif args.command == "resolve":
            result, code = center.resolve(args.source, args.key), 0
        else:
            result, code = center.cancel(args.id, args.reason), 0
    finally:
        center.close()
    print(json.dumps({"ok": code == 0, **result}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
