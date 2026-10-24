#!/usr/bin/env python3
"""lark-cli CardKit delivery adapter; interactive cards only, never plain text."""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

from notification_cards import validate_card


class LarkCliCardSender:
    def __init__(
        self,
        binary: str,
        profile: str,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        self.binary = binary
        self.profile = profile
        self.runner = runner

    def send(self, chat_id: str, card: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        validate_card(card)
        if not str(chat_id or "").strip():
            raise ValueError("chat_id_required")
        result = self.runner(
            [
                self.binary,
                "--profile",
                self.profile,
                "im",
                "+messages-send",
                "--as",
                "bot",
                "--chat-id",
                chat_id,
                "--msg-type",
                "interactive",
                "--content",
                json.dumps(card, ensure_ascii=False, separators=(",", ":")),
                "--idempotency-key",
                idempotency_key,
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"feishu_send_exit_{result.returncode}:{result.stderr[-300:]}")
        try:
            response = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError(f"feishu_send_bad_response:{result.stdout[:200]}")
        if not isinstance(response, dict):
            raise RuntimeError("feishu_send_bad_response:object_required")
        if response.get("ok") is False or response.get("code") not in (None, 0):
            raise RuntimeError(f"feishu_send_rejected:{response.get('error') or response.get('msg')}")
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        message_id = data.get("message_id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise RuntimeError("feishu_send_missing_message_id")
        return {"message_id": message_id.strip()}
