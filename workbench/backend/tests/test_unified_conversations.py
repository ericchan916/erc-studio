import copy
import json
import sys
import tempfile
import threading
import time
import unittest
import uuid
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Service
from domain import Problem
from conversation_context import StudioContextEngine, estimate_tokens
from vendor.hermes.context import ContextEngine, _pending_tool_round, bound_model_input_without_hygiene
from server import handler_for, ThreadingHTTPServer


class Sender:
    def ensure_active(self, member):
        pass


class Model:
    def __init__(self):
        self.calls = []
        self.release = threading.Event()
        self.release.set()

    def complete(self, history, name):
        self.calls.append(copy.deepcopy(history))
        if not self.release.wait(5):
            raise AssertionError('test_model_wait_expired')
        return 'answer:'+history[-1]['content']

    def summarize(self, previous, messages, focus=None):
        return '摘要：保留目标与约束。'


class UnifiedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a, self.b = str(uuid.uuid4()), str(uuid.uuid4())
        self.model = Model()
        self.cfg = {'state_dir': self.tmp.name, 'auth_pepper': 'fixture-pepper', 'feishu_profile': 'fixture',
                    'unified_conversations': {'enabled': True},
                    'members': [{'principal_id': owner, 'handle': handle, 'name': handle,
                                 'tenant_key': 'fixture-tenant', 'open_id': 'ou_'+handle}
                                for owner, handle in [(self.a, 'a'), (self.b, 'b')]]}
        self.s = Service(self.cfg, self.model, Sender())

    def tearDown(self):
        self.model.release.set()
        self.s.close()
        self.tmp.cleanup()

    def wait(self, receipt):
        for _ in range(300):
            job = self.s.job(self.a, receipt['job_id'])
            if job['state'] in ('succeeded', 'failed', 'cancelled', 'execution_unknown'):
                return job
            time.sleep(.01)
        self.fail('job_did_not_finish')

    def send(self, channel, source, text, cid=None):
        return self.s.unified.submit(self.a, channel, source, text, cid)

    def test_three_channels_use_one_transcript_and_one_context(self):
        a = self.send('web', 'web-1', 'web-private-marker')
        self.wait(a)
        b = self.send('feishu', 'om_fixture_1', 'followup from feishu')
        self.wait(b)
        c = self.send('app', 'app-1', 'followup from native')
        self.wait(c)
        self.assertEqual({a['conversation_id'], b['conversation_id'], c['conversation_id']}, {a['conversation_id']})
        self.assertEqual([r['content'] for r in self.model.calls[-1]],
                         ['web-private-marker', 'answer:web-private-marker', 'followup from feishu',
                          'answer:followup from feishu', 'followup from native'])
        self.assertEqual(len(self.s.messages(self.a, a['conversation_id'])['items']), 6)

    def test_viewing_history_does_not_change_active_binding(self):
        a = self.send('web', '1', 'A')
        self.wait(a)
        new = self.s.create_conversation(self.a, {'title': 'B'}, str(uuid.uuid4()))
        self.s.messages(self.a, new['conversation_id'])
        self.assertEqual(self.s.unified.active(self.a)['conversation_id'], a['conversation_id'])

    def test_explicit_binding_is_versioned_and_owner_scoped(self):
        a = self.send('web', '1', 'A')
        self.wait(a)
        b = self.s.create_conversation(self.a, {'title': 'B'}, str(uuid.uuid4()))['conversation_id']
        revision = self.s.unified.active(self.a)['revision']
        result = self.s.unified.bind(self.a, b, revision)
        self.assertEqual(result['revision'], revision+1)
        with self.assertRaises(Problem) as e:
            self.s.unified.bind(self.a, a['conversation_id'], revision)
        self.assertEqual(e.exception.code, 'conversation_binding_conflict')
        with self.assertRaises(Problem) as e:
            self.s.unified.bind(self.b, b)
        self.assertEqual(e.exception.status, 404)

    def test_replay_after_switch_keeps_original_job_and_does_not_switch_back(self):
        first = self.send('feishu', 'om_replay', 'original')
        self.wait(first)
        other = self.s.create_conversation(self.a, {'title': 'other'}, str(uuid.uuid4()))['conversation_id']
        self.s.unified.bind(self.a, other)
        again = self.send('feishu', 'om_replay', 'original')
        self.assertEqual(again['job_id'], first['job_id'])
        self.assertEqual(self.s.unified.active(self.a)['conversation_id'], other)
        self.assertEqual(len(self.model.calls), 1)

    def test_same_source_changed_content_is_a_conflict(self):
        first = self.send('feishu', 'om_1', 'original')
        self.wait(first)
        with self.assertRaises(Problem) as e:
            self.send('feishu', 'om_1', 'different')
        self.assertEqual(e.exception.code, 'idempotency_conflict')

    def test_source_id_namespaces_and_owners_are_independent(self):
        first = self.send('web', 'same', 'A')
        self.wait(first)
        b = self.s.unified.submit(self.b, 'web', 'same', 'B')
        c = self.send('app', 'same', 'C')
        self.wait(c)
        self.assertNotEqual(b['conversation_id'], first['conversation_id'])
        self.assertNotEqual(c['job_id'], first['job_id'])
        self.assertNotIn('B', json.dumps(self.model.calls[-1]))

    def test_queued_questions_keep_logical_question_answer_order(self):
        self.model.release.clear()
        first = self.send('web', '1', 'first')
        second = self.send('feishu', '2', 'second')
        third = self.send('app', '3', 'third')
        self.model.release.set()
        self.wait(third)
        self.assertEqual([x['text'] for x in self.s.messages(self.a, first['conversation_id'])['items']],
                         ['first', 'answer:first', 'second', 'answer:second', 'third', 'answer:third'])
        self.assertEqual([x['content'] for x in self.model.calls[1]], ['first', 'answer:first', 'second'])
        self.assertNotIn('third', json.dumps(self.model.calls[1]))

    def test_source_metadata_is_not_mirrored_as_duplicate_messages(self):
        first = self.send('feishu', 'om_source', '  原文\n ')
        self.wait(first)
        rows = self.s.messages(self.a, first['conversation_id'])['items']
        self.assertEqual(rows[0]['text'], '  原文\n ')
        self.assertEqual(rows[0]['source_refs'], [{'channel': 'feishu', 'source_id': 'om_source'}])
        self.assertEqual(rows[1]['source_refs'], rows[0]['source_refs'])
        self.assertEqual(len(rows), 2)

    def test_pagination_has_stable_logical_cursor_without_duplicates(self):
        first = self.send('web', '1', 'first')
        self.wait(first)
        for i in range(2, 8):
            self.wait(self.send('app', str(i), str(i)))
        page = self.s.messages(self.a, first['conversation_id'], limit=3, offset=-1)
        seen = page['items']
        while page['older_cursor']:
            page = self.s.messages(self.a, first['conversation_id'], limit=3, offset=-1, before=int(page['older_cursor']))
            seen = page['items']+seen
        self.assertEqual(len(seen), 14)
        self.assertEqual(len({r['message_id'] for r in seen}), 14)
        self.assertEqual([r['role'] for r in seen], ['user', 'assistant']*7)

    def test_restart_keeps_bindings_sources_and_original_rows(self):
        first = self.send('web', 'first', 'persistent')
        self.wait(first)
        before = [dict(r) for r in self.s.db.execute('SELECT * FROM messages ORDER BY seq')]
        self.s.close()
        self.s = Service(self.cfg, self.model, Sender())
        self.assertEqual(self.s.unified.active(self.a)['conversation_id'], first['conversation_id'])
        self.assertEqual([dict(r) for r in self.s.db.execute('SELECT * FROM messages ORDER BY seq')], before)
        self.assertEqual(self.send('web', 'first', 'persistent')['job_id'], first['job_id'])
        self.assertEqual(len(self.model.calls), 1)

    def test_unknown_execution_blocks_new_same_conversation_without_new_rows(self):
        first = self.send('web', 'first', 'persistent')
        self.wait(first)
        with self.s.transaction():
            self.s.db.execute("UPDATE jobs SET state='execution_unknown' WHERE id=?", (first['job_id'],))
        before = self.s.one('SELECT count(*) FROM messages')[0]
        with self.assertRaises(Problem) as e:
            self.send('feishu', 'next', 'do not replay')
        self.assertEqual(e.exception.code, 'conversation_busy_or_unknown')
        self.assertEqual(self.s.one('SELECT count(*) FROM messages')[0], before)

    def test_trash_late_answer_is_cancelled_and_original_history_preserved(self):
        self.model.release.clear()
        first = self.send('web', 'first', 'preserve')
        for _ in range(100):
            if self.s.job(self.a, first['job_id'])['state'] == 'running':
                break
            time.sleep(.01)
        cid = first['conversation_id']
        self.s.update_conversation(self.a, cid, 'trash', {'expected_version': self.s.check_conv(self.a, cid)['version']}, str(uuid.uuid4()))
        self.model.release.set()
        # job() hides trashed conversations, inspect the actual persisted task.
        for _ in range(100):
            if self.s.one('SELECT state FROM jobs WHERE id=?', (first['job_id'],))[0] == 'cancelled':
                break
            time.sleep(.01)
        self.assertEqual(self.s.one('SELECT count(*) FROM messages WHERE conversation=?', (cid,))[0], 1)
        self.assertIsNone(self.s.unified.active(self.a)['conversation_id'])

    def test_other_member_cannot_read_history_or_assemble_context(self):
        first = self.send('web', 'first', 'private')
        self.wait(first)
        with self.assertRaises(Problem):
            self.s.messages(self.b, first['conversation_id'])
        job = dict(self.s.check_job(self.a, first['job_id']))
        job['owner'] = self.b
        with self.assertRaises(Problem):
            self.s.context.assemble(job, [{'role': 'user', 'content': 'private'}])

    def test_real_http_web_and_native_adapters_share_context_and_sources(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.s))
        host = '127.0.0.1:'+str(server.server_port)
        self.cfg['allowed_hosts'] = [host]
        self.cfg['allowed_origins'] = ['http://'+host]
        token = self.s.grant_oauth({'tenant_key': 'fixture-tenant', 'open_id': 'ou_a'})['session_credential']
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body=None, native=False):
            headers = {'Authorization': 'Bearer '+token, 'Idempotency-Key': str(uuid.uuid4())}
            headers.update({'X-Studio-Native': '1'} if native else {'Origin': 'http://'+host})
            if body is not None:
                headers['Content-Type'] = 'application/json'
            req = urllib.request.Request('http://'+host+path,
                data=json.dumps(body).encode() if body is not None else None, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        try:
            self.assertEqual(request('/v1/conversations/active'), {'conversation_id': None, 'revision': 0})
            first = request('/v1/conversations/start-message',
                            {'title': 'HTTP QA', 'text': 'browser marker', 'client_message_id': str(uuid.uuid4())})
            self.wait(first)
            cid = first['conversation_id']
            second = request('/v1/conversations/'+cid+'/messages',
                             {'text': 'native follows', 'client_message_id': str(uuid.uuid4())}, native=True)
            self.wait(second)
            history = request('/v1/conversations/'+cid+'/messages')['items']
            self.assertEqual([r['source_refs'][0]['channel'] for r in history], ['web', 'web', 'app', 'app'])
            self.assertIn('browser marker', json.dumps(self.model.calls[-1]))
            other = self.s.create_conversation(self.a, {'title': 'other'}, str(uuid.uuid4()))['conversation_id']
            active = request('/v1/conversations/active')
            result = request('/v1/conversations/'+other+'/continue', {'expected_revision': active['revision']})
            self.assertEqual(result['conversation_id'], other)
            with self.assertRaises(urllib.error.HTTPError) as e:
                request('/v1/conversations/'+cid+'/continue', {'expected_revision': active['revision']})
            self.assertEqual(e.exception.code, 409)
            with self.assertRaises(urllib.error.HTTPError) as e:
                request('/v1/conversations/'+cid+'/messages', {'text': 'spoof', 'client_message_id': str(uuid.uuid4()), 'owner': self.b})
            self.assertEqual(e.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()

    def test_continuation_replay_is_idempotent_after_other_device_switches(self):
        first = self.send('web', '1', 'A')
        self.wait(first)
        other = self.s.create_conversation(self.a, {'title': 'B'}, str(uuid.uuid4()))['conversation_id']
        body = {'expected_revision': self.s.unified.active(self.a)['revision']}
        key = str(uuid.uuid4())
        receipt = self.s.unified.activate(self.a, other, body, key)
        self.s.unified.bind(self.a, first['conversation_id'])
        self.assertEqual(self.s.unified.activate(self.a, other, body, key), receipt)
        self.assertEqual(self.s.unified.active(self.a)['conversation_id'], first['conversation_id'])

    def test_context_summary_cache_is_persistent_exact_and_owner_scoped(self):
        first = self.send('web', '1', 'A')
        self.wait(first)
        job = self.s.check_job(self.a, first['job_id'])
        self.cfg['unified_conversations']['context_length'] = 2048
        history = ContextTests().history()
        counter = []
        def summarize(*args):
            counter.append(True)
            return 'trusted summary of data'
        self.model.summarize = summarize
        a = self.s.context.assemble(job, history)
        calls = len(counter)
        self.assertTrue(calls)
        self.assertEqual(self.s.context.assemble(job, history), a)
        self.assertEqual(len(counter), calls)
        changed = copy.deepcopy(history)
        changed[-1]['content'] = 'new exact current question'
        self.assertEqual(self.s.context.assemble(job, changed)[-1], changed[-1])
        self.assertGreater(len(counter), calls)
        self.assertEqual(self.s.one('SELECT count(*) FROM conversation_context_cache WHERE owner=?', (self.b,))[0], 0)

    def test_new_schema_keeps_original_job_shape_and_is_opt_in(self):
        self.assertEqual(len(self.s.db.execute('PRAGMA table_info(jobs)').fetchall()), 9)
        self.assertEqual(self.s.one('PRAGMA user_version')[0], 2)
        self.assertTrue(self.s.info(self.a)['capabilities']['shared_context'])
        self.s.close()
        self.cfg.pop('unified_conversations')
        self.s = Service(self.cfg, self.model, Sender())
        self.assertFalse(self.s.info(self.a)['capabilities']['shared_context'])

    def test_unknown_queue_head_requires_explicit_retry_and_preserves_answer_order(self):
        first = self.send('web', '1', 'first')
        self.wait(first)
        cid = first['conversation_id']
        with self.s.transaction():
            # Reproduce a process death after provider completion but before its
            # final transaction, while a later question is already admitted.
            second = self.send('feishu', '2', 'second')
            answer = self.s.one('SELECT answer FROM conversation_turns WHERE job=?', (first['job_id'],))['answer']
            self.s.db.execute('UPDATE conversation_turns SET answer=NULL WHERE job=?', (first['job_id'],))
            self.s.db.execute('DELETE FROM messages WHERE id=?', (answer,))
            self.s.db.execute("UPDATE jobs SET state='execution_unknown' WHERE id=?", (first['job_id'],))
        time.sleep(.25)
        self.assertEqual(self.s.job(self.a, second['job_id'])['state'], 'queued')
        body = {'client_message_id': str(uuid.uuid4()), 'expected_version': self.s.check_conv(self.a, cid)['version']}
        with self.assertRaises(Problem) as e:
            self.s.retry_job(self.a, first['job_id'], body, str(uuid.uuid4()))
        self.assertEqual(e.exception.code, 'retry_confirmation_required')
        retry = self.s.retry_job(self.a, first['job_id'], {**body, 'acknowledge_unknown': True}, str(uuid.uuid4()))
        self.wait(second)
        self.assertEqual([r['text'] for r in self.s.messages(self.a, cid)['items']],
                         ['first', 'answer:first', 'second', 'answer:second'])
        self.assertEqual([r['content'] for r in self.model.calls[-1]], ['first', 'answer:first', 'second'])
        self.assertEqual(self.send('web', '1', 'first')['job_id'], retry['job_id'])


class ContextTests(unittest.TestCase):
    def history(self):
        return [{'role': 'system', 'content': 'persona invariant'}]+[
            {'role': 'user' if i % 2 == 0 else 'assistant', 'content': ('历史'+str(i)+' ')*24}
            for i in range(50)]+[{'role': 'user', 'content': 'current exact question'}]

    def test_upstream_contract_and_model_threshold_helpers_are_usable(self):
        e = StudioContextEngine(lambda *args: 'summary', 2048)
        self.assertIsInstance(e, ContextEngine)
        e.update_model('fixture', 4096)
        self.assertEqual(e.threshold_tokens, 3072)
        self.assertEqual(list(_pending_tool_round([{'role': 'tool', 'content': 'x'}, {'role': 'user', 'display_kind': 'steer'}])), [0])

    def test_compaction_preserves_system_head_current_tail_and_raw_input(self):
        history = self.history()
        original = copy.deepcopy(history)
        calls = []
        def summarize(previous, chunk, focus):
            calls.append(chunk)
            return '已确认目标与约束；历史仍在原记录。'
        engine = StudioContextEngine(summarize, 2048)
        compact = engine.compress(history)
        self.assertEqual(history, original)
        self.assertEqual(compact[0], history[0])
        self.assertEqual(compact[-1], history[-1])
        self.assertEqual(compact[-6:], history[-6:])
        self.assertLessEqual(estimate_tokens(compact), engine.threshold_tokens)
        self.assertTrue(calls)

    def test_missing_summary_provider_is_explicit_not_silent_truncation(self):
        with self.assertRaises(Problem) as e:
            StudioContextEngine(None, 2048).compress(self.history())
        self.assertEqual(e.exception.code, 'context_summary_unavailable')

    def test_active_tool_round_is_never_cut(self):
        e = StudioContextEngine(lambda *args: 'summary', 2048)
        messages = self.history()+[{'role': 'assistant', 'tool_calls': [{'id': 'a'}]},
                                   {'role': 'tool', 'tool_call_id': 'a', 'content': 'exact tool result'}]
        compact = e.compress(messages)
        self.assertEqual(compact[-3:], messages[-3:])
        self.assertNotEqual(compact[0]['role'], 'tool')

    def test_provider_quota_error_propagates_without_vendor_fallback(self):
        def no_quota(*args):
            raise Problem(502, 'model_http_402')
        with self.assertRaises(Problem) as e:
            StudioContextEngine(no_quota, 2048).compress(self.history())
        self.assertEqual(e.exception.code, 'model_http_402')

    def test_oversized_current_turn_does_not_get_truncated(self):
        h = [{'role': 'user', 'content': '中文'*3000}]
        with self.assertRaises(Problem) as e:
            StudioContextEngine(lambda *args: 'summary', 2048).compress(h)
        self.assertEqual(e.exception.code, 'context_active_turn_too_large')
        self.assertEqual(len(h[0]['content']), 6000)

    def test_small_history_does_not_invoke_summary(self):
        def unexpected(*args):
            raise AssertionError('summary_should_not_run')
        h = [{'role': 'user', 'content': 'small'}]
        self.assertEqual(StudioContextEngine(unexpected).compress(h), h)

    def test_upstream_bound_drops_orphan_tool_result_and_does_not_mutate(self):
        h = [{'role': 'system', 'content': 's'}, {'role': 'assistant', 'tool_calls': [{'id': 'a'}]},
             {'role': 'tool', 'tool_call_id': 'a', 'content': 't'}, {'role': 'user', 'content': 'u'}]
        before = copy.deepcopy(h)
        self.assertEqual(bound_model_input_without_hygiene(h, 3), [h[0], h[-1]])
        self.assertEqual(h, before)


if __name__ == '__main__':
    unittest.main()
