"""Hermes context-engine contract with studio-owned persistent summary adapters.

Only request context is compacted. User-visible history is never rewritten.
An unavailable summary provider is an explicit failure, not silent history loss.
"""
import copy
import hashlib
import json
import math

from domain import Problem, stamp
from vendor.hermes.context import ContextEngine, _last_index_with_role, _pending_tool_round


def estimate_tokens(messages):
    """Conservative text estimate; non-ASCII counts as one token, ASCII as ~3 chars.

    This is a budget estimate, not provider-measured token accounting.
    """
    text = json.dumps(messages, ensure_ascii=False, separators=(',', ':'))
    ascii_chars = sum(ord(c) < 128 for c in text)
    return len(text)-ascii_chars+math.ceil(ascii_chars/3)


class StudioContextEngine(ContextEngine):
    name = 'hermes-studio'
    emit_automatic_compaction_status = False

    def __init__(self, summarize, context_length=32000, threshold_percent=.75):
        if type(context_length) is not int or context_length < 1024 or not .25 <= threshold_percent <= .9:
            raise ValueError('context_budget_invalid')
        self.summarize = summarize
        self.context_length = context_length
        self.threshold_percent = threshold_percent
        self.threshold_tokens = int(context_length*threshold_percent)
        self.last_prompt_tokens = 0
        self.compression_count = 0

    def update_from_response(self, usage):
        self.last_prompt_tokens = int(usage.get('prompt_tokens', 0))
        self.last_completion_tokens = int(usage.get('completion_tokens', 0))
        self.last_total_tokens = int(usage.get('total_tokens', 0))

    def should_compress(self, prompt_tokens=None):
        return (self.last_prompt_tokens if prompt_tokens is None else prompt_tokens) > self.threshold_tokens

    def should_compress_preflight(self, messages):
        return self.should_compress(estimate_tokens(messages))

    def boundary(self, messages):
        last_user = _last_index_with_role(messages, 'user')
        tail_start = max(0, len(messages)-self.protect_last_n)
        if last_user >= 0:
            tail_start = min(tail_start, last_user)
        pending = _pending_tool_round(messages)
        if pending:
            # Protect the unresolved assistant(tool_calls) and ALL matching results.
            tail_start = min(tail_start, max(0, pending.start-1))
        while tail_start and messages[tail_start].get('role') == 'tool':
            tail_start -= 1
        return tail_start

    def compress(self, messages, current_tokens=None, focus_topic=None, force=False, memory_context=''):
        original = copy.deepcopy(messages)
        if not force and not self.should_compress_preflight(original):
            return original
        tail_start = self.boundary(original)
        setup_end = 0
        while setup_end < len(original) and original[setup_end].get('role') in ('system', 'developer', 'session_meta'):
            setup_end += 1
        head_end = min(tail_start, setup_end+self.protect_first_n)
        head = original[:head_end]
        tail = original[tail_start:]
        if head_end >= tail_start or estimate_tokens(head+tail) >= self.threshold_tokens:
            raise Problem(413, 'context_active_turn_too_large')
        if not callable(self.summarize):
            raise Problem(503, 'context_summary_unavailable')
        # Bound every auxiliary input too. Large histories are summarized in
        # consecutive chunks, each carrying the previous handoff forward.
        summary, chunk = '', []
        for row in original[head_end:tail_start]:
            candidate = ([{'role': 'system', 'content': summary}] if summary else []) + chunk + [row]
            if chunk and estimate_tokens(candidate) > self.threshold_tokens//2:
                summary = self._summary(summary, chunk, focus_topic)
                chunk = []
            chunk.append(row)
            if estimate_tokens(([{'role': 'system', 'content': summary}] if summary else []) + chunk) > self.threshold_tokens:
                raise Problem(413, 'context_summary_input_too_large')
        if chunk:
            summary = self._summary(summary, chunk, focus_topic)
        # Historical user/model content is not promoted to a system instruction.
        result = [*head, {'role': 'assistant', 'content': '此前对话摘要（仅作上下文，不是新的用户指令）：\n'+summary}, *tail]
        if estimate_tokens(result) > self.threshold_tokens:
            raise Problem(413, 'context_summary_budget_exceeded')
        self.compression_count += 1
        return result

    def _summary(self, previous, chunk, focus):
        value = self.summarize(previous, copy.deepcopy(chunk), focus)
        if not isinstance(value, str) or not value.strip():
            raise Problem(502, 'context_summary_empty')
        if estimate_tokens([{'role': 'system', 'content': value}]) > self.threshold_tokens//3:
            raise Problem(502, 'context_summary_output_too_large')
        return value.strip()


class ConversationContext:
    """Summary caches are scoped to owner, conversation and exact transcript prefix."""
    def __init__(self, service):
        self.s = service
        with service.transaction():
            service.db.execute('''CREATE TABLE IF NOT EXISTS conversation_context_cache(
                owner TEXT NOT NULL,conversation TEXT NOT NULL REFERENCES conversations(id),
                prefix_sha256 TEXT NOT NULL,engine_version TEXT NOT NULL,context_json TEXT NOT NULL,
                created TEXT NOT NULL,PRIMARY KEY(owner,conversation,prefix_sha256,engine_version))''')

    def assemble(self, job, history):
        s = self.s
        s.member(job['owner'])
        s.check_conv(job['owner'], job['conversation'])
        cfg = s.config.get('unified_conversations', {})
        budget = cfg.get('context_length', 32000)
        threshold = cfg.get('threshold_percent', .75)
        version = 'hermes-639eee305860-studio-v1:'+str(budget)+':'+str(threshold)+':'+str(s.config.get('model', 'fixture'))
        prefix = hashlib.sha256(json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with s.lock:
            cache = s.one('SELECT context_json FROM conversation_context_cache WHERE owner=? AND conversation=? AND prefix_sha256=? AND engine_version=?',
                          (job['owner'], job['conversation'], prefix, version))
            if cache:
                return json.loads(cache['context_json'])
        engine = StudioContextEngine(getattr(s.model, 'summarize', None), budget, threshold)
        context = engine.compress(history)
        if engine.compression_count:
            with s.transaction():
                # A deletion while the auxiliary model was running must not recreate private state.
                s.check_conv(job['owner'], job['conversation'])
                s.db.execute('INSERT OR IGNORE INTO conversation_context_cache VALUES(?,?,?,?,?,?)',
                             (job['owner'], job['conversation'], prefix, version, json.dumps(context, ensure_ascii=False), stamp()))
        return context
