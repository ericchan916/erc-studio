import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parent/'shared/bridge'))
from conversation_protocol import ProtocolError, canonical, endpoint, headers_for, read_key, request, verify, verify_response
from bridge_studio_execution import server_for


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.keyfile = Path(self.tmp.name)/'ipc.key'
        self.keyfile.write_text('1'*64)
        self.keyfile.chmod(0o600)
        self.key = read_key(self.keyfile)

    def tearDown(self):
        self.tmp.cleanup()

    def test_signed_packet_binds_method_path_timestamp_nonce_and_exact_bytes(self):
        raw = canonical({'text': '原文\n retained', 'owner': str(uuid.uuid4())})
        headers = headers_for(self.key, 'POST', '/internal/studio/execute', raw, clock=lambda: 100)
        verify(self.key, 'POST', '/internal/studio/execute', raw, headers, clock=lambda: 100)
        for method, path, body in [('GET', '/internal/studio/execute', raw),
                                  ('POST', '/internal/studio/cancel', raw),
                                  ('POST', '/internal/studio/execute', raw+b' ')]:
            with self.assertRaises(ProtocolError):
                verify(self.key, method, path, body, headers, clock=lambda: 100)
        with self.assertRaises(ProtocolError):
            verify(self.key, 'POST', '/internal/studio/execute', raw, headers, clock=lambda: 161)

    def test_signed_browser_cookie_and_proxy_headers_are_still_denied(self):
        raw = canonical({'fixture': True})
        headers = headers_for(self.key, 'POST', '/internal/studio/status', raw)
        for name in ('Origin', 'Cookie', 'Forwarded', 'X-Forwarded-For', 'X-Forwarded-Proto'):
            with self.subTest(name=name), self.assertRaises(ProtocolError) as e:
                verify(self.key, 'POST', '/internal/studio/status', raw, {**headers, name: 'fixture'})
            self.assertEqual(e.exception.code, 'studio_internal_origin_denied')

    def test_response_must_prove_key_possession_and_bind_to_the_request_nonce(self):
        raw = canonical({'state': 'succeeded', 'text': 'verified answer'})
        nonce = uuid.uuid4().hex
        headers = headers_for(self.key, 'RESPONSE', '/internal/studio/execute', raw, nonce=nonce)
        verify_response(self.key, '/internal/studio/execute', raw, headers, nonce)
        for body, incoming, expected in [(raw+b' ', headers, nonce), (raw, {}, nonce),
                                          (raw, headers, uuid.uuid4().hex)]:
            with self.assertRaises(ProtocolError) as e:
                verify_response(self.key, '/internal/studio/execute', body, incoming, expected)
            self.assertTrue(e.exception.uncertain)

    def test_key_requires_private_regular_file_and_rejects_symlink(self):
        self.keyfile.chmod(0o644)
        with self.assertRaises(ProtocolError):
            read_key(self.keyfile)
        self.keyfile.chmod(0o600)
        link = Path(self.tmp.name)/'link'
        link.symlink_to(self.keyfile)
        with self.assertRaises(ProtocolError):
            read_key(link)
        self.keyfile.write_text('invalid fixture key')
        with self.assertRaises(ProtocolError):
            read_key(self.keyfile)

    def test_only_explicit_loopback_endpoints_and_internal_routes_are_accepted(self):
        self.assertEqual(endpoint('http://127.0.0.1:8844/'), 'http://127.0.0.1:8844')
        for url in ('https://127.0.0.1:8844', 'http://localhost:8844', 'http://192.168.1.2:8844',
                    'http://user:secret@127.0.0.1:8844', 'http://127.0.0.1:8844/path',
                    'http://127.0.0.1:8844/?key=1', 'http://127.0.0.1:80', 'file:///private'):
            with self.subTest(url=url), self.assertRaises(ProtocolError):
                endpoint(url)
        with self.assertRaises(ProtocolError):
            request('http://127.0.0.1:8844', '/v1/publish/jobs', {}, self.keyfile)

    def test_actual_loopback_server_verifies_signatures_before_dispatch(self):
        calls = []
        class Execution:
            def execute(self, body):
                calls.append(body)
                return {'state': 'succeeded', 'text': body['text']}
            def status(self, body):
                return {'state': 'succeeded'}
            def cancel(self, body):
                return {'accepted': True}
        server = server_for(Execution(), self.keyfile, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:'+str(server.server_port)
        try:
            packet = request(origin, '/internal/studio/execute', {'text': '  中文原文\n '}, self.keyfile)
            self.assertEqual(packet['text'], '  中文原文\n ')
            self.assertEqual(len(calls), 1)
            raw = canonical({'text': 'unsigned'})
            req = urllib.request.Request(origin+'/internal/studio/execute', data=raw,
                                         headers={'Content-Type': 'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 401)
            e.exception.close()
            self.assertEqual(len(calls), 1)
            headers = headers_for(self.key, 'POST', '/internal/studio/execute', raw)
            headers['Origin'] = origin
            req = urllib.request.Request(origin+'/internal/studio/execute', data=raw, headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 403)
            e.exception.close()
            self.assertEqual(len(calls), 1)
        finally:
            server.shutdown()
            server.server_close()

    def test_client_does_not_follow_redirect_or_forward_a_signed_packet(self):
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.send_response(302)
                self.send_header('Location', '/redirect-target')
                self.end_headers()
            def do_GET(self):
                raise AssertionError('signed transport followed redirect')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(ProtocolError) as e:
                request('http://127.0.0.1:'+str(server.server_port), '/internal/studio/execute', {}, self.keyfile)
            self.assertEqual(e.exception.code, 'studio_transport_http_302')
            self.assertTrue(e.exception.uncertain)
        finally:
            server.shutdown()
            server.server_close()

    def test_process_on_same_port_cannot_forge_an_unsigned_success_response(self):
        class Impostor(BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def do_POST(self):
                raw=canonical({'state':'succeeded','text':'forged'})
                self.send_response(200)
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Impostor)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with self.assertRaises(ProtocolError) as e:
                request('http://127.0.0.1:'+str(server.server_port),'/internal/studio/execute',{},self.keyfile)
            self.assertEqual(e.exception.code,'studio_response_authentication_failed')
            self.assertTrue(e.exception.uncertain)
        finally:
            server.shutdown();server.server_close()


if __name__ == '__main__':
    unittest.main()
