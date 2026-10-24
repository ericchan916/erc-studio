"""Cherry Studio local Agent API client."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from bridge_utils import load_cherry_api_key, utc_now


@dataclass(frozen=True)
class AskResult:
    text: str
    agent_resume_id: str


class ExecutionUnknown(RuntimeError):
    """The request may have executed tools; automatic replay is unsafe."""


class ExecutionRejected(RuntimeError):
    """The request was explicitly rejected before any execution activity."""


class CherryClient:
    def __init__(
        self,
        base_url: str,
        state_path: Path,
        db_path: Path,
        agent_id: str,
        agent_timeout_seconds: int = 180,
    ):
        self.base_url = base_url.rstrip("/")
        self.state_path = state_path
        self.db_path = db_path
        self.agent_id = agent_id
        self.agent_timeout_seconds = agent_timeout_seconds

    def _request(
        self, method: str, path: str, payload: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> urllib.response.addinfourl:
        headers = {
            "Authorization": f"Bearer {load_cherry_api_key(self.state_path)}",
            "Content-Type": "application/json",
        }
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        return urllib.request.urlopen(request, timeout=timeout)

    def health(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=5) as response:
                return response.status == 200
        except Exception:
            return False

    def channel_active(self, channel_id: str) -> bool:
        with self._request("GET", f"/v1/channels/{channel_id}") as response:
            value = json.load(response)
        active = value.get("is_active", value.get("isActive"))
        if not isinstance(active, bool):
            raise RuntimeError("Cherry channel response missing active state")
        return active

    def set_channel_active(self, channel_id: str, active: bool) -> bool:
        with self._request(
            "PATCH", f"/v1/channels/{channel_id}", {"is_active": active}
        ) as response:
            value = json.load(response)
        current = value.get("is_active", value.get("isActive"))
        if not isinstance(current, bool):
            raise RuntimeError("Cherry channel update missing active state")
        if current != active:
            raise RuntimeError(f"Cherry channel active state mismatch: {current}")
        return current

    def get_agent(self) -> Dict[str, Any]:
        with self._request("GET", f"/v1/agents/{self.agent_id}") as response:
            return json.load(response)

    def session_exists(self, session_id: str) -> bool:
        try:
            with self._request(
                "GET", f"/v1/agents/{self.agent_id}/sessions/{session_id}"
            ) as response:
                return response.status == 200
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return False
            raise

    def get_session(self, session_id: str) -> Dict[str, Any]:
        with self._request(
            "GET", f"/v1/agents/{self.agent_id}/sessions/{session_id}"
        ) as response:
            return json.load(response)

    def set_session_instructions(self, session_id: str, instructions: str) -> None:
        with self._request(
            "PATCH",
            f"/v1/agents/{self.agent_id}/sessions/{session_id}",
            {"instructions": instructions},
        ) as response:
            value = json.load(response)
        if str(value.get("instructions") or "") != instructions:
            raise RuntimeError("Cherry session instructions update mismatch")

    def create_session(self, name: str) -> str:
        agent = self.get_agent()
        keys = (
            "description", "accessible_paths", "instructions", "model",
            "plan_model", "small_model", "mcps", "allowed_tools", "configuration",
        )
        payload = {key: agent.get(key) for key in keys if agent.get(key) is not None}
        payload["name"] = name
        with self._request(
            "POST", f"/v1/agents/{self.agent_id}/sessions", payload
        ) as response:
            session = json.load(response)
        return str(session["id"])

    def ask(
        self, session_id: str, content: str, on_text=None, on_status=None,
    ) -> AskResult:
        try:
            return self._ask_stream(session_id, content, on_text, on_status)
        except (ExecutionRejected, ExecutionUnknown):
            raise
        except urllib.error.HTTPError as error:
            # These HTTP responses reject the submitted request itself. Timeouts,
            # conflicts and server failures are not evidence that no tools ran.
            if error.code in {400, 401, 402, 403, 404, 413, 415, 422, 429}:
                raise ExecutionRejected("Cherry request rejected: HTTP %s" % error.code) from error
            raise ExecutionUnknown("Cherry execution response uncertain: HTTP %s" % error.code) from error
        except Exception as error:
            raise ExecutionUnknown("Cherry execution response uncertain: %s" % type(error).__name__) from error

    def _ask_stream(
        self,
        session_id: str,
        content: str,
        on_text=None,
        on_status=None,
    ) -> AskResult:
        path = f"/v1/agents/{self.agent_id}/sessions/{session_id}/messages"
        stream_text: List[str] = []
        final_snapshot = ""
        agent_resume_id = ""
        errors: List[str] = []
        verified_pre_execution_errors: List[bool] = []
        has_text = False
        execution_activity = False
        finished = False
        with self._request(
            "POST", path, {"content": content}, timeout=self.agent_timeout_seconds
        ) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    finished = True
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    raise ExecutionUnknown("Cherry malformed execution stream")
                if not isinstance(event, dict):
                    raise ExecutionUnknown("Cherry malformed execution event")
                event_type = str(event.get("type") or "")
                if event_type == "finish":
                    finished = True
                is_init = (
                    event_type == "raw" and isinstance(event.get("rawValue"), dict)
                    and event["rawValue"].get("type") == "init"
                )
                if not is_init and event_type not in {"start", "start-step", "finish-step", "finish", "error"}:
                    execution_activity = True
                if event.get("type") == "raw":
                    raw_value = event.get("rawValue") or {}
                    if raw_value.get("type") == "init" and raw_value.get("session_id"):
                        agent_resume_id = str(raw_value["session_id"])
                if event.get("type") == "error":
                    err = event.get("error") or {}
                    errors.append(str(err.get("message") or err))
                    # Verified against Cherry's createMessage handler: these
                    # identity checks emit SSE error + EOF before dispatch.
                    verified_pre_execution_errors.append(
                        err.get("type") == "not_found"
                        and err.get("code") in {"agent_not_found", "session_not_found"}
                    )
                    continue
                event_type = str(event.get("type") or "")
                if not has_text and on_status:
                    if event_type == "start-step":
                        on_status("正在分析任务…")
                    elif event_type == "tool-input-start":
                        on_status("正在调用工具…")
                    elif event_type in ("tool-output-available", "tool-input-end"):
                        on_status("工具已返回，正在整理…")
                if event.get("type") != "text-delta":
                    continue
                text = str(event.get("text") or "")
                raw = ((event.get("providerMetadata") or {}).get("raw") or {})
                if raw.get("type") == "assistant":
                    final_snapshot = text
                else:
                    stream_text.append(text)
                has_text = True
                if on_text:
                    on_text(final_snapshot or "".join(stream_text))
        if errors:
            rejection_terms = (
                "insufficient balance", "payment required", "invalid api key",
                "unauthorized", "rate limit", "too many requests",
            )
            if not execution_activity and all(
                verified or any(term in error.lower() for term in rejection_terms)
                for error, verified in zip(errors, verified_pre_execution_errors)
            ):
                raise ExecutionRejected("; ".join(errors))
            raise ExecutionUnknown("Cherry stream ended with an uncertain execution error")
        if not finished:
            raise ExecutionUnknown("Cherry execution stream ended without a terminal marker")
        answer = (final_snapshot or "".join(stream_text)).strip()
        if not answer:
            raise ExecutionUnknown("Cherry returned an empty response")
        if not agent_resume_id:
            raise ExecutionUnknown("Cherry response missing agent resume ID")
        return AskResult(answer, agent_resume_id)

    def persist_exchange(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
        agent_resume_id: str,
        event_id: str,
    ) -> None:
        """Persist the API exchange so Cherry resumes the same SDK session.

        Cherry 1.9.13's public Agent SSE endpoint executes the Agent but does
        not set its internal `persist` option. A narrowly scoped SQLite insert
        reproduces Cherry's own `persistHeadlessExchange` shape without
        changing application code.
        """
        now = utc_now()
        topic_id = f"agent-session:{session_id}"
        metadata = json.dumps(
            {"bridge_event_id": event_id}, ensure_ascii=False, separators=(",", ":")
        )
        agent = self.get_agent()

        def payload(role: str, text: str) -> Dict[str, Any]:
            message_id = str(uuid.uuid4())
            block_id = str(uuid.uuid4())
            message: Dict[str, Any] = {
                "id": message_id,
                "role": role,
                "assistantId": self.agent_id,
                "topicId": topic_id,
                "createdAt": now,
                "status": "success",
                "blocks": [block_id],
            }
            if role == "assistant":
                message["modelId"] = agent.get("model")
            return {
                "message": message,
                "blocks": [
                    {
                        "id": block_id,
                        "messageId": message_id,
                        "type": "main_text",
                        "createdAt": now,
                        "status": "success",
                        "content": text,
                    }
                ],
            }

        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                """
                SELECT 1 FROM session_messages
                WHERE session_id=? AND metadata=? LIMIT 1
                """,
                (session_id, metadata),
            ).fetchone()
            if not exists:
                for role, text in (("user", user_text), ("assistant", assistant_text)):
                    conn.execute(
                        """
                        INSERT INTO session_messages(
                            session_id, role, content, metadata,
                            created_at, updated_at, agent_session_id
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            session_id,
                            role,
                            json.dumps(
                                payload(role, text),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            metadata,
                            now,
                            now,
                            agent_resume_id,
                        ),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
