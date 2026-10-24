# Copyright (c) 2025 Nous Research. MIT; see LICENSE and PROVENANCE.json.
# Selected upstream definitions are byte-for-byte; future annotations supports our Python 3.9 host.
from __future__ import annotations
import copy, json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from .compression_marker import elide, ELISION_MARKER_MAX_LEN
_HYGIENE_SETUP_ROLES = frozenset(("system", "session_meta"))
class ContextEngine(ABC):
    """Base class all context engines must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (e.g. 'compressor', 'lcm')."""

    # Token state: engines MUST maintain these; run_agent.py reads them directly.
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0
    # Compaction parameters (read by run_agent.py for preflight). protect_first_n counts
    # non-system head messages kept verbatim IN ADDITION to the always-protected system
    # prompt (3 keeps the historical head shape).
    # These control the preflight compression check. Subclasses may override via __init__ or property;
    # defaults are sensible for most engines. See #13754.
    threshold_percent: float = 0.75
    protect_first_n: int = 3
    protect_last_n: int = 6
    # False keeps successful automatic compaction passes silent (routine background
    # maintenance); warnings, errors and manual /compress still surface.
    emit_automatic_compaction_status: bool = True

    @abstractmethod
    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """Update tracked token usage after every LLM call.

        ``prompt_tokens``/``completion_tokens``/``total_tokens`` are always present; the
        canonical buckets (``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
        ``cache_write_tokens``, ``reasoning_tokens``) are optional on older hosts.
        """

    @abstractmethod
    def should_compress(self, prompt_tokens: int = None) -> bool:
        """Return True if compaction should fire this turn."""

    def should_compress_info(self, prompt_tokens: int = None) -> "tuple[bool, str | None]":
        """Return ``(should_compress, reason)``.

        Engines with block reasons (summary-LLM cooldown, anti-thrashing guard) override
        this so callers can warn instead of silently skipping; the default keeps plugin
        engines from raising AttributeError.
        """
        return self.should_compress(prompt_tokens), None

    @abstractmethod
    def compress(
        self, messages: List[Dict[str, Any]], current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None, force: bool = False, memory_context: str = "",
    ) -> List[Dict[str, Any]]:
        """Compact ``messages`` into a valid OpenAI-format list that fits the budget.

        ``focus_topic`` comes from manual ``/compress <focus>`` (prioritise that topic);
        ``force`` asks to bypass an engine-owned cooldown; ``memory_context`` is provider
        text for the handoff prompt. Older engines may omit optional parameters — the
        host filters them by signature.
        """

    def prune_tool_results_only(
        self, messages: List[Dict[str, Any]], current_tokens: int | None = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Deterministically trim old tool-result payloads without an LLM call.

        Runs on a low, cost-oriented trigger independent of ``should_compress`` so
        large-window engines reclaim re-sent tool output long before full compaction.
        Returns ``(messages, n_pruned)``; the default no-op keeps older engines safe.
        """
        return messages, 0

    def select_context(
        self, request_messages: List[Dict[str, Any]], *, conversation_messages: List[Dict[str, Any]] = None,
        incoming_message: Dict[str, Any] = None, budget_tokens: int = 0,
    ) -> List[Dict[str, Any]]:
        """Optionally *select* (replace) the context for THIS request, pre-generation.

        Runs on every provider request (also retries), independent of
        ``should_compress()``: ``compress()`` shrinks over-long context, this swaps in a
        different one (retrieval, topic routing, branch switching). Return ``None`` to
        leave the request unchanged. The returned list is request-only — it MUST NOT be
        treated as persisted transcript state (session DB history is untouched); unlike
        ``pre_llm_call`` it may replace the list. The host runs it before prompt
        cache-control and every request sanitizer, so a malformed replacement never
        reaches the provider and the default no-op keeps the request byte-identical;
        an engine that replaces the list changes its own cache prefix (breakpoints are
        re-derived on the selected list). ``request_messages`` is the assembled request
        (system prompt + history + ephemeral prefill); ``conversation_messages`` is the
        persisted history for reference only (do not mutate); ``budget_tokens`` is the
        model's context length or 0 if unknown.
        """
        return None

    def on_turn_complete(self, messages: List[Dict[str, Any]], usage: Dict[str, Any] = None, **kwargs: Any) -> None:
        """Observe a finished turn (complement of ``select_context()``) to index/update
        routing state for the next request.

        Best-effort, not guaranteed: fires from the normal finalization seam only; some
        abnormal early returns (content-policy block, provider terminal failure) skip it.
        ``messages`` is a read-only shallow copy (return value ignored; never rely on
        transcript mutation). ``usage`` has the ``update_from_response`` shape and is
        ``None`` when no provider response was reached (interrupt). ``kwargs`` may include
        ``turn_id``, ``task_id``, ``api_call_count``, ``interrupted``, ``failed``, ``turn_exit_reason``.
        """
        return None

    def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
        """Cheap rough check before the API call (no real token count yet); default skips."""
        return False

    def should_defer_preflight_to_real_usage(self, rough_tokens: int) -> bool:
        """True when preflight should trust recent real usage over the noisy rough
        estimate (avoids re-compacting after a compressed request already fit)."""
        return False

    def get_automatic_compaction_status_message(
        self, *, phase: str, default_message: str, **context: Any,
    ) -> str | None:
        """User-visible status for automatic compaction, or ``None`` to suppress it.

        ``phase`` is the host call site (``"preflight"`` / ``"compress"``); ``context``
        carries best-effort ``approx_tokens`` / ``threshold_tokens``. Warnings, errors
        and manual ``/compress`` are not governed by this hook.
        """
        return default_message if self.emit_automatic_compaction_status else None

    def has_content_to_compress(self, messages: List[Dict[str, Any]]) -> bool:
        """Preflight guard for gateway ``/compress``: False reports "nothing to
        compress yet" without an LLM call (e.g. transcript entirely protected)."""
        return True

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Session begins: load persisted state. kwargs may include hermes_home, platform, model."""

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Real session boundary (CLI exit, /reset, gateway expiry) — never per-turn."""

    def on_session_reset(self) -> None:
        """/new or /reset: reset per-session state (default: counters and token tracking)."""
        # Reset cross-call calibration state captured under the PREVIOUS model. These fields encode "the
        # provider proved this prompt fit" / "preflight can be deferred" decisions that are only valid for
        # the model that produced them. Carrying them across a switch to a smaller-context model would let
        # should_defer_preflight_to_real_usage() suppress a preflight compression the new model actually
        # needs — the exact oversized-send-after-switch failure in #23767. The new model's first response
        # repopulates them via update_from_response(). Setting last_prompt_tokens to 0 (NOT -1) is
        # deliberate: 0 is the documented "no real usage yet -> use the rough estimate" state, so the post-
        # response should_compress path falls back to estimate_request_tokens_rough rather than skipping
        # compression. -1 is a different sentinel (#36718, "compression just ran, await real usage") and
        # must not be set here.
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Tool schemas this engine exposes to the agent (default: none)."""
        return []

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        """Handle a call to one of this engine's tools; must return a JSON string.
        kwargs may include ``messages`` (live in-memory list)."""
        return json.dumps({"error": f"Unknown context engine tool: {name}"})

    def get_status(self) -> Dict[str, Any]:
        """Status dict with the standard fields run_agent.py expects."""
        # Clamp the -1 "compression just ran, awaiting real usage" sentinel to 0 so no
        # reader sees a negative usage_percent on the transitional turn.
        last_prompt = max(self.last_prompt_tokens, 0)
        return {
            "last_prompt_tokens": last_prompt,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": min(100, last_prompt / self.context_length * 100) if self.context_length else 0,
            "compression_count": self.compression_count,
        }

    def clone_for_agent(self) -> "ContextEngine":
        """Per-agent instance of a plugin-registered engine (the plugin system holds ONE shared
        instance; every AIAgent gets its own so a child's update_model() cannot mutate the parent's).
        Override when the engine holds uncopyable state (locks, DB connections): return a fresh
        engine sharing the durable backend and copying only mutable budget state."""
        return copy.deepcopy(self)

    def update_model(
        self, model: str, context_length: int, base_url: str = "", api_key: str = "",
        provider: str = "", api_mode: str = "",
    ) -> None:
        """Model switch / fallback: recompute threshold_tokens (override for more).

        Per-model threshold override (longest substring match), else the raw config
        percent — snapshotted ONCE so repeated switches fall back to the configured
        value, not the previous model's override.
        """
        self.context_length = context_length
        from .context import resolve_model_threshold
        if not hasattr(self, "_config_threshold_percent"):
            self._config_threshold_percent = self.threshold_percent
        self._base_threshold_percent = resolve_model_threshold(
            model, getattr(self, "model_thresholds", {}), self._config_threshold_percent, provider)
        self.threshold_percent = self._base_threshold_percent
        self.threshold_tokens = int(context_length * self.threshold_percent)


_LEAN_USER_MESSAGES_BUDGET_CHARS = 24_000  # ~6K tokens


_LEAN_USER_MESSAGE_MAX_CHARS = 4_000


_LEAN_USER_MESSAGES_HEADING = "## User Messages (verbatim, newest first)"


def _lean_recovery_stub(tool_name: str, content_len: int, session_id: str) -> str:
    """One-line replacement for a demoted tail tool result."""
    hint = f" Recover with session_search(query=..., session_id='{session_id}')" if session_id else ""
    return (
        f"[{tool_name or 'tool'} output demoted at compaction — {content_len:,} "
        f"chars preserved in session history.{hint}]"
    )


_SYNTHETIC_USER_ROW_PREFIXES = (
    "[System:", "[CONTEXT", "[PRIOR CONTEXT", "[IMPORTANT: Background", "[Your active task list",
    "[Planning state preserved", "[ASYNC DELEGATION", "[OUT-OF-BAND", "Cronjob Response:",
)


def _synthetic_user_row(content: str) -> bool:
    """True for scaffolding user rows that carry no real user words."""
    if not isinstance(content, str) or not content.strip():
        return True
    return content.lstrip().startswith(_SYNTHETIC_USER_ROW_PREFIXES)


def _build_verbatim_user_section(turns: List[Dict[str, Any]]) -> str:
    """Compacted region's REAL user messages verbatim, newest-first under a char budget (straddler truncated); "" if none."""
    collected: list[str] = []
    used = 0
    for msg in reversed(turns):
        if msg.get("role") != "user":
            continue
        content = _content_text_for_contains(msg.get("content"))
        if _synthetic_user_row(content):
            continue
        remaining = _LEAN_USER_MESSAGES_BUDGET_CHARS - used
        if remaining <= 0:
            break
        text = content.strip()
        if len(text) > remaining and remaining <= ELISION_MARKER_MAX_LEN:
            break  # no room for marker + content: a marker-only quote would overshoot the budget
        text = elide(text, min(_LEAN_USER_MESSAGE_MAX_CHARS, remaining))
        collected.append("> " + text.replace("\n", "\n> "))
        used += len(text)
    if not collected:
        return ""
    return (
        "\n\n" + _LEAN_USER_MESSAGES_HEADING + "\n"
        + "\n\n".join(collected)
        + "\n(Every real user message from the compacted region, quoted "
        "verbatim. These are the user's actual words and override any "
        "paraphrase of them above.)"
    )


def _last_index_with_role(messages: "List[Dict[str, Any]]", role: str) -> int:
    """Index of the newest dict message with ``role``, or -1."""
    return max((i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == role), default=-1)


def _last_assistant_index(messages: "List[Dict[str, Any]]") -> int:
    """Newest assistant message index, or -1 (the one turn whose thinking may replay; see ``_NEWEST_TURN_ONLY_BUDGET_KEYS``)."""
    return _last_index_with_role(messages, "assistant")


def _pending_tool_round(messages: "List[Dict[str, Any]]") -> range:
    """Indices of the tool results the transcript ends with — a round the model has not answered yet; empty when
    the transcript ends in any other row. /steer rows after the round do not answer it: a steer is delivered
    after the newest tool result before the next API call, and two can land in one iteration (one when the tool
    batch ends, one before the request), so every contiguous trailing steer row is skipped."""
    end = len(messages)
    while end and messages[end - 1].get("display_kind") == STEER_DISPLAY_KIND:
        end -= 1
    start = end
    while start > 0 and messages[start - 1].get("role") == "tool":
        start -= 1
    return range(start, end)


def _content_text_for_contains(content: Any) -> str:
    """Return a best-effort text view of message content (for substring checks only)."""
    if isinstance(content, list):
        return "\n".join(t for t in map(_part_text, content) if isinstance(t, str) and t)
    return "" if content is None else content if isinstance(content, str) else str(content)


def bound_model_input_without_hygiene(history: List[Any], limit: int) -> List[Any]:
    """Fail-closed in-context bound for a turn where hygiene has not landed (#111988).

    Keeps the leading ``system``/``session_meta`` setup rows plus the newest tail, total <= ``limit``.
    Deterministic (the same transcript always yields the same cut) and payload-only: the stored
    transcript is never touched, so the agent's durable-prefix slice (``history_offset``) is
    unaffected. Returns ``history`` unchanged — same object — when nothing needs dropping, so the
    landed-compression and below-the-limit paths stay byte-identical.
    """
    if len(history) <= limit:
        return history
    head_end = 0
    while (head_end < len(history) and isinstance(history[head_end], dict)
           and history[head_end].get("role") in _HYGIENE_SETUP_ROLES):
        head_end += 1
    # Always leave room for the newest row: a setup-only payload would answer nothing.
    head_end = min(head_end, limit - 1)
    tail_start = len(history) - (limit - head_end)
    # Never start the kept tail on a tool result: its parent assistant(tool_calls) row is dropped
    # with it, and an orphaned tool result is an invalid sequence for every provider.
    while (tail_start < len(history) and isinstance(history[tail_start], dict)
           and history[tail_start].get("role") == "tool"):
        tail_start += 1
    return history[:head_end] + history[tail_start:]



def _part_text(item: Any) -> Optional[str]:
    """Text of a content part: the string itself, a dict's ``text``, else None."""
    return item if isinstance(item, str) else item.get("text") if isinstance(item, dict) else None


STEER_DISPLAY_KIND = "steer"


def _model_threshold_key_rank(key: str, model: str, provider: str) -> "tuple[int, int] | None":
    """Match rank for one ``model_thresholds`` key, or None when it does not apply.
    ``"<provider>:<substr>"`` keys apply only on that provider; bare keys apply on every route.
    The same slug means different windows on different routes (Codex caps Astra at 272K; OpenRouter
    serves the full window), so a bare ``astra: 0.85`` written for Codex silently leaks everywhere.
    Rank = (substring length, scoped): the most specific model match wins, scope breaks ties."""
    scope, sep, substr = key.partition(":")
    if not sep:
        return (len(key), 0) if key in model else None
    return (len(substr), 1) if scope.strip().lower() == provider and substr in model else None


def resolve_model_threshold(
    model: str, model_thresholds: dict[str, float] | None, default: float, provider: str = "",
) -> float:
    """Per-model threshold: longest matching ``model_thresholds`` key wins, else ``default``.
    Keys are substrings of the model name, optionally provider-scoped as ``"<provider>:<substr>"``
    (a scoped key outranks a bare one of the same substring). Module-level so plugin context
    engines can reuse it."""
    if not model_thresholds or not model:
        return default
    provider = (provider or "").strip().lower()
    ranked = ((_model_threshold_key_rank(key, model, provider), key) for key in model_thresholds)
    best = max(((rank, key) for rank, key in ranked if rank is not None), default=None)
    return float(model_thresholds[best[1]]) if best else default

