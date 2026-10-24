"""Real local bytes and HTTP; vendor actions are explicit synthetic fixtures."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from contentbase.errors import BaseError
from contentbase.server import make_server, media_range
from contentbase.store import digest
from contentbase.cli import main, parser
import test_service as fixture


class MediaServiceTests(unittest.TestCase):
    setUp = fixture.ServiceTests.setUp
    tearDown = fixture.ServiceTests.tearDown
    publish = fixture.ServiceTests.publish

    def test_unreviewable_original_filename_stops_before_vendor_upload(self):
        for name in ("bad\nname.png", "bad\\name.png"):
            path = self.root / name
            path.write_bytes(fixture.synthetic_png())
            with self.subTest(name=name), self.assertRaisesRegex(BaseError, "media_name_invalid"):
                self.service.publish_prepare("rec_EXAMPLE", "ACCOUNT_EXAMPLE", "小红书", "imageText", "cloud", [(str(path), "image")], {}, "filename-example-key")
        self.assertEqual((self.yxer.uploads, self.yxer.submits), (0, 0))

    def test_upload_reads_frozen_copy_not_mutable_original(self):
        seen = []
        def upload(path, platform, usage):
            self.image.write_bytes(b"changed original")
            seen.append((path, Path(path).read_bytes(), usage))
            return {"key": "RESOURCE_EXAMPLE", "size": len(seen[-1][1])}
        self.yxer.upload = upload
        op = self.publish()
        self.assertEqual(seen[0][1], fixture.synthetic_png())
        self.assertNotEqual(seen[0][0], str(self.image))
        self.assertEqual(self.service.media_bytes(op["id"], 0)["data"], fixture.synthetic_png())
        self.assertEqual(Path(seen[0][0]).stat().st_mode & 0o777, 0o400)
        self.assertEqual(self.yxer.submits, 0)

    def test_source_changes_between_inspect_and_freeze_stop_before_upload(self):
        self.yxer.validate_fields = lambda *args: self.image.write_bytes(fixture.synthetic_png() + b"changed")
        with self.assertRaises(BaseError):
            self.publish()
        self.assertEqual(self.yxer.uploads, 0)
        self.assertEqual(self.service.inspect()[0]["state"], "preparation_failed")

    def test_original_deletion_after_preparation_does_not_change_review(self):
        op = self.publish()
        self.image.unlink()
        review = self.service.media_review(op["id"])
        self.assertEqual(review["media"][0]["sha256"], hashlib.sha256(fixture.synthetic_png()).hexdigest())
        self.assertNotIn(str(self.root), json.dumps(review))
        result = self.service.publish_submit(op["id"], op["confirmation_digest"], True, review["media_review_digest"])
        self.assertEqual(result["state"], "submitted")

    def test_review_and_submit_reject_frozen_tamper(self):
        op = self.publish()
        entry = op["data"]["frozen_media"][0]
        frozen = Path(self.service.frozen_media.path(op["id"], entry))
        os.chmod(frozen, 0o600)
        frozen.write_bytes(fixture.synthetic_png() + b"changed")
        for action in (lambda: self.service.media_review(op["id"]),
                       lambda: self.service.media_bytes(op["id"], 0),
                       lambda: self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])):
            with self.assertRaises(BaseError):
                action()
        self.assertEqual(self.yxer.submits, 0)

    def test_tamper_during_vendor_revalidation_stops_submit(self):
        op = self.publish()
        frozen = Path(self.service.frozen_media.path(op["id"], op["data"]["frozen_media"][0]))
        def mutate():
            os.chmod(frozen, 0o600)
            frozen.write_bytes(fixture.synthetic_png() + b"changed")
        self.yxer.revalidate_hook = mutate
        with self.assertRaises(BaseError):
            self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.assertEqual(self.yxer.submits, 0)

    def test_explicit_media_digest_is_required_for_new_submit(self):
        op = self.publish()
        for ack in (None, "wrong-digest", True):
            with self.subTest(ack=ack), self.assertRaisesRegex(BaseError, "explicit_frozen_media_review_required"):
                self.service.publish_submit(op["id"], op["confirmation_digest"], True, ack)
        self.assertEqual((self.yxer.preflights, self.yxer.submits), (1, 0))

    def test_old_ready_has_no_inferred_media_approval_or_automatic_reupload(self):
        op = self.publish()
        data = copy.deepcopy(op["data"])
        data.pop("frozen_media")
        data.pop("media_review_digest")
        self.service.store.transition(op["id"], {"ready"}, "ready", data)
        with self.assertRaisesRegex(BaseError, "legacy_publish_media_requires_new_review"):
            self.service.publish_submit(op["id"], op["confirmation_digest"], True)
        self.assertEqual((self.yxer.uploads, self.yxer.submits), (1, 0))

    def test_ack_does_not_replace_manifest_or_confirmation_integrity(self):
        op = self.publish()
        data = copy.deepcopy(op["data"])
        data["frozen_media"][0]["name"] = "changed.png"
        data["media_review_digest"] = digest(data["frozen_media"])
        self.service.store.transition(op["id"], {"ready"}, "ready", data)
        with self.assertRaisesRegex(BaseError, "publish_confirmation_changed"):
            self.service.media_review(op["id"])

    def test_private_review_package_is_exact_and_repeatably_verified(self):
        op = self.publish()
        package = self.service.export_publish_review(op["id"])
        self.assertEqual(package, self.service.export_publish_review(op["id"]))
        self.assertEqual(Path(package["index_path"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.yxer.submits, 0)
        Path(package["index_path"]).write_text("tampered")
        with self.assertRaises(BaseError):
            self.service.export_publish_review(op["id"])

    def test_cli_review_destination_never_overwrites_existing_or_source(self):
        op = self.publish()
        destination = self.root.resolve() / "my-review"
        result = self.service.export_publish_review(op["id"], destination)
        self.assertEqual(Path(result["directory"]), destination)
        with self.assertRaises(BaseError):
            self.service.export_publish_review(op["id"], destination)
        source = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(BaseError, "review_package_must_be_outside_source"):
            self.service.export_publish_review(op["id"], source / "new-private-review")

    def test_cli_review_exports_and_submit_shows_exact_packet_before_human_ack(self):
        op = self.publish()
        args = parser().parse_args(["publish-review", op["id"], "--destination", str(self.root.resolve() / "cli-review")])
        self.assertEqual(args.command, "publish-review")
        with patch("contentbase.cli.load_config", return_value=fixture.CONFIG), patch("contentbase.service.Service", return_value=self.service):
            with patch("sys.stdout", io.StringIO()) as out:
                main(["publish-review", op["id"], "--destination", str(self.root.resolve() / "cli-review")])
            self.assertTrue(json.loads(out.getvalue())["ok"])
            with patch("sys.stdout", io.StringIO()) as out, patch("sys.stderr", io.StringIO()) as err, patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="CONFIRM"):
                main(["publish-submit", op["id"], "--digest", op["confirmation_digest"]])
            self.assertEqual(json.loads(out.getvalue())["data"]["state"], "submitted")
            self.assertIn("index.html", err.getvalue())
            self.assertIn(op["data"]["media_review_digest"], err.getvalue())
        self.assertEqual(self.yxer.submits, 1)

    def test_cli_non_tty_never_submits_even_with_review_file(self):
        op = self.publish()
        with patch("contentbase.cli.load_config", return_value=fixture.CONFIG), patch("contentbase.service.Service", return_value=self.service), patch("sys.stdout", io.StringIO()) as out, patch("sys.stdin", io.StringIO("CONFIRM\n")):
            with self.assertRaises(SystemExit) as result:
                main(["publish-submit", op["id"], "--digest", op["confirmation_digest"]])
        self.assertEqual(result.exception.code, 2)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], "human_tty_confirmation_required")
        self.assertEqual(self.yxer.submits, 0)


class MediaHttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MediaServiceTests()
        self.fixture.setUp()
        self.service = self.fixture.service
        self.op = self.fixture.publish()
        self.scope = self.service.identity_fingerprint()
        self.server, self.token = make_server(self.service, 0, "example-access")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%s" % self.server.server_port
        self.media = "/api/operations/%s/media/0" % self.op["id"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.fixture.tearDown()

    def request(self, path, headers=None, body=None):
        combined = {"Authorization": "Bearer " + self.token, "X-Contentbase-Identity": self.scope, "Content-Type": "application/json"}
        combined.update(headers or {})
        request = Request(self.url + path, headers=combined, data=json.dumps(body).encode() if body is not None else None)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_bytes_are_scoped_authenticated_and_have_no_query_secret(self):
        status, headers, raw = self.request(self.media)
        self.assertEqual((status, raw), (200, fixture.synthetic_png()))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request(self.media, {"Authorization": ""})[0], 401)
        self.assertEqual(self.request(self.media, {"X-Contentbase-Identity": ""})[0], 400)
        self.service.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
        self.assertEqual(self.request(self.media)[0], 409)

    def test_single_range_exact_suffix_and_unsatisfiable(self):
        raw = fixture.synthetic_png()
        for value, expected in (("bytes=2-8", raw[2:9]), ("bytes=-5", raw[-5:]), ("bytes=3-", raw[3:]), ("bytes=0-999", raw)):
            with self.subTest(value=value):
                status, headers, result = self.request(self.media, {"Range": value})
                self.assertEqual((status, result), (206, expected))
                self.assertTrue(headers["Content-Range"].endswith("/" + str(len(raw))))
        for value in ("bytes=999-", "bytes=-0", "bytes=5-2", "bytes=1-2,4-5", "bytes=-", "items=1-2"):
            with self.subTest(value=value):
                status, headers, result = self.request(self.media, {"Range": value})
                self.assertEqual((status, result), (416, b""))
                self.assertEqual(headers["Content-Range"], "bytes */%s" % len(raw))

    def test_review_has_only_safe_media_route_and_export_is_fixed_destination(self):
        status, headers, raw = self.request("/api/operations/%s/review" % self.op["id"])
        self.assertEqual(status, 200)
        data = json.loads(raw)["data"]
        self.assertEqual(data["media"][0]["preview_url"], self.media)
        self.assertNotIn(str(self.fixture.root), raw.decode())
        path = "/api/operations/%s/review-export" % self.op["id"]
        self.assertEqual(self.request(path, body={"destination": "/tmp/arbitrary"})[0], 409)
        self.assertEqual(self.request(path, body={})[0], 200)
        self.assertEqual(self.fixture.yxer.submits, 0)

    def test_http_submit_requires_exact_media_review_not_only_approved_boolean(self):
        body = {"operation_id": self.op["id"], "digest": self.op["confirmation_digest"], "approved": True}
        headers = {"X-Contentbase-Provider": digest(self.fixture.yxer.provider_binding())}
        self.assertEqual(self.request("/api/publish/submit", headers, body)[0], 409)
        self.assertEqual(self.fixture.yxer.submits, 0)
        body["reviewed_media_digest"] = self.op["data"]["media_review_digest"]
        self.assertEqual(self.request("/api/publish/submit", headers, body)[0], 200)
        self.assertEqual(self.fixture.yxer.submits, 1)


if __name__ == "__main__":
    unittest.main()
