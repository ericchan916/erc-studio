import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from contentbase.server import make_server
from contentbase.service import Service
from test_service import CONFIG, FakeFeishu, FakeYxer


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vendor = FakeYxer()
        service = Service(CONFIG, Path(self.temp.name), FakeFeishu(), self.vendor)
        self.service = service
        self.server, self.token = make_server(service, 0, "test-access-value")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, path, body=None, auth=True, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if auth:
            h["Authorization"] = "Bearer " + self.token
            if "X-Contentbase-Identity" not in h:
                h["X-Contentbase-Identity"] = self.service.identity_fingerprint()
            from contentbase.store import digest
            if "X-Contentbase-Provider" not in h:
                h["X-Contentbase-Provider"] = digest(self.vendor.provider_binding())
        request = Request(self.root + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        try:
            raw = response.read().decode()
            return response.status, raw, response.headers
        finally:
            response.close()

    def test_static_page_and_health_do_not_leak_token_or_business(self):
        status, raw, headers = self.request("/", auth=False)
        self.assertEqual(status, 200)
        self.assertNotIn(self.token, raw)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, raw, _ = self.request("/api/health", auth=False)
        self.assertFalse(json.loads(raw)["data"]["business_verified"])

    def test_auth_host_origin_and_path_boundaries(self):
        self.assertEqual(self.request("/api/operations", auth=False)[0], 401)
        self.assertEqual(self.request("/api/sync", {}, headers={"Origin": "https://example.invalid"})[0], 409)
        self.assertEqual(self.request("/api/operations", headers={"Host": "example.invalid"})[0], 400)
        self.assertEqual(self.request("/../../config.json")[0], 400)

    def test_http_agent_plan_and_human_approval_match_cli_protocol(self):
        status, raw, _ = self.request("/api/draft", {"record_id": "rec_EXAMPLE", "title": "示例标题", "body": "网页正文", "key": "web-example-key"})
        self.assertEqual(status, 200)
        op = json.loads(raw)["data"]
        self.assertEqual(self.request("/api/apply", {"operation_id": op["id"]})[0], 409)
        self.assertEqual(self.request("/api/approve", {"operation_id": op["id"], "digest": op["confirmation_digest"], "approved": True})[0], 200)
        self.assertEqual(self.request("/api/apply", {"operation_id": op["id"]})[0], 200)

    def test_web_preparation_rejects_arbitrary_host_file_path(self):
        status, raw, _ = self.request("/api/publish/prepare", {"record_id": "rec_EXAMPLE", "account_id": "ACCOUNT_EXAMPLE", "platform": "小红书", "kind": "imageText", "channel": "cloud", "media_ids": ["/etc/passwd"], "key": "web-example-key"})
        self.assertEqual(status, 409)
        self.assertEqual(self.vendor.uploads, 0)

    def test_bad_types_and_unknown_routes_are_error_envelopes(self):
        self.assertEqual(self.request("/api/draft", {"record_id": "rec_EXAMPLE"})[0], 400)
        self.assertEqual(self.request("/api/other", {})[0], 409)

    def test_mutation_requires_a_verified_client_identity(self):
        body = {"record_id": "rec_EXAMPLE", "title": "标题", "body": "正文", "key": "header-audit-key"}
        self.assertEqual(self.request("/api/draft", body, headers={"X-Contentbase-Identity": ""})[0], 409)
        self.assertEqual(self.service.store.all_operations(), [])

    def test_client_identity_change_never_writes_old_draft(self):
        old = self.service.identity_fingerprint()
        self.service.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
        status, raw, _ = self.request("/api/draft", {"record_id": "rec_EXAMPLE", "title": "标题", "body": "旧草稿", "key": "header-audit-key"},
                                      headers={"X-Contentbase-Identity": old})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(raw)["error"]["code"], "workspace_operator_changed")
        self.assertEqual(self.service.store.all_operations(), [])

    def test_provider_context_change_blocks_preparation_before_upload(self):
        from contentbase.store import digest
        old = digest(self.vendor.provider_binding())
        self.vendor.provider["provider_hash"] = "OTHER_PROVIDER_EXAMPLE"
        status, raw, _ = self.request("/api/publish/prepare", {}, headers={"X-Contentbase-Provider": old})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(raw)["error"]["code"], "client_provider_changed")
        self.assertEqual(self.vendor.uploads, 0)

    def test_nonascii_bad_authentication_is_401_not_a_server_exception(self):
        self.assertEqual(self.request("/api/operations", auth=False, headers={"Authorization": "Bearer café"})[0], 401)


if __name__ == "__main__":
    unittest.main()
