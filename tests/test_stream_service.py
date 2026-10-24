"""Bounded media snapshots and HTTP cleanup; no real vendor operations."""
from contextlib import contextmanager
from http.client import IncompleteRead
import io
import copy
from pathlib import Path
from unittest.mock import patch
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from contentbase.errors import BaseError
from contentbase.config import private_write, load_config
from contentbase.service import Service
import test_media_service as media_fixture
from test_service import synthetic_png


class StreamServiceTests(unittest.TestCase):
    setUp = media_fixture.MediaServiceTests.setUp
    tearDown = media_fixture.MediaServiceTests.tearDown
    publish = media_fixture.MediaServiceTests.publish

    def test_snapshot_closes_and_preserves_original_bytes(self):
        op = self.publish()
        with self.service.media_stream(op["id"], 0) as packet:
            reader = packet["stream"]
            self.assertEqual(reader.read(16) + reader.read(1024), synthetic_png())
            self.assertEqual(packet["content_length"], len(synthetic_png()))
            self.assertNotIn("path", packet)
        self.assertTrue(reader.closed)
        self.assertEqual(self.yxer.submits, 0)

    def test_two_controller_slots_have_no_third_snapshot_then_release(self):
        op = self.publish()
        with self.service.media_stream(op["id"], 0), self.service.media_stream(op["id"], 0):
            with self.assertRaisesRegex(BaseError, "media_stream_busy"):
                with self.service.media_stream(op["id"], 0):
                    self.fail("third stream must remain closed")
        with self.service.media_stream(op["id"], 0) as packet:
            self.assertEqual(packet["stream"].read(1024), synthetic_png())

    def test_consumer_exception_closes_snapshot_and_releases_slot(self):
        op = self.publish()
        with self.assertRaisesRegex(RuntimeError, "synthetic consumer interruption"):
            with self.service.media_stream(op["id"], 0) as packet:
                reader = packet["stream"]
                raise RuntimeError("synthetic consumer interruption")
        self.assertTrue(reader.closed)
        with self.service.media_stream(op["id"], 0), self.service.media_stream(op["id"], 0):
            pass

    def test_identity_switch_during_snapshot_never_yields_old_material(self):
        op = self.publish()
        original = self.service.frozen_media.open_snapshot
        captured = []
        @contextmanager
        def changed(*args, **kwargs):
            with original(*args, **kwargs) as packet:
                captured.append(packet["stream"])
                self.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
                yield packet
        with patch.object(self.service.frozen_media, "open_snapshot", changed):
            with self.assertRaises(BaseError):
                with self.service.media_stream(op["id"], 0):
                    self.fail("old operator material was yielded")
        self.assertTrue(captured[0].closed)
        self.feishu.user["open_id"] = "ou_EXAMPLE"
        with self.service.media_stream(op["id"], 0), self.service.media_stream(op["id"], 0):
            pass

    def test_identity_switch_during_export_stream_exit_stops_package_completion(self):
        op = self.publish()
        with self.assertRaises(BaseError):
            with self.service.media_stream(op["id"], 0) as packet:
                reader = packet["stream"]
                reader.read(1024)
                self.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
        self.assertTrue(reader.closed)

    def test_export_and_http_path_do_not_use_whole_bytes_helper(self):
        op = self.publish()
        with patch.object(self.service, "media_bytes", side_effect=AssertionError("whole bytes path used")):
            result = self.service.export_publish_review(op["id"])
        self.assertTrue(result["index_path"].endswith("index.html"))
        self.assertEqual(self.yxer.submits, 0)

    def test_private_old_config_snapshot_recovers_unknown_without_new_write(self):
        config_path = self.root / "private-original-config.json"
        private_write(config_path, self.service.config)
        op = self.service.draft("rec_EXAMPLE", "标题", "正文", "config-recovery-example")
        self.service.approve(op["id"], op["confirmation_digest"], True)
        self.feishu.failure = True
        with self.assertRaises(BaseError):
            self.service.apply(op["id"])
        changed = copy.deepcopy(self.service.config)
        changed["feishu"]["stages"] = {"reviewed": "另一显示标签"}
        other = Service(changed, self.service.root, self.feishu, self.yxer)
        self.assertEqual(other.inspect(), [])
        with self.assertRaisesRegex(BaseError, "operation_not_found"):
            other.verify_write(op["id"])
        restored = Service(load_config(config_path), self.service.root, self.feishu, self.yxer)
        self.assertEqual(restored.inspect(op["id"])["state"], "unknown")
        self.assertEqual(restored.verify_write(op["id"])["state"], "applied")
        self.assertEqual((self.feishu.writes, self.yxer.submits), (1, 0))
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)


class StreamHttpTests(unittest.TestCase):
    setUp = media_fixture.MediaHttpTests.setUp
    tearDown = media_fixture.MediaHttpTests.tearDown
    request = media_fixture.MediaHttpTests.request

    def test_http_uses_snapshot_reader_instead_of_materializing_full_bytes(self):
        with patch.object(self.service, "media_bytes", side_effect=AssertionError("full bytes helper used")):
            status, headers, body = self.request(self.media, {"Range": "bytes=1-5"})
        self.assertEqual((status, body), (206, synthetic_png()[1:6]))
        self.assertEqual(headers["Content-Length"], "5")

    def test_http_snapshot_capacity_returns_typed_429_without_business_effects(self):
        with self.service.media_stream(self.op["id"], 0), self.service.media_stream(self.op["id"], 0):
            status, _, body = self.request(self.media)
        self.assertEqual(status, 429)
        self.assertIn(b"media_stream_busy", body)
        self.assertEqual(self.fixture.yxer.submits, 0)

    def bad_stream_request(self, make_stream, size):
        captured = []
        @contextmanager
        def reader(*args, **kwargs):
            stream = make_stream()
            captured.append(stream)
            try:
                yield {"stream": stream, "content_length": size, "size": size, "start": 0, "end": size - 1, "mime": "image/png"}
            finally:
                stream.close()
        headers = {"Authorization": "Bearer " + self.token, "X-Contentbase-Identity": self.scope}
        with patch.object(self.service, "media_stream", reader):
            with urlopen(Request(self.url + self.media, headers=headers), timeout=5) as response:
                self.assertEqual(response.status, 200)
                with self.assertRaises(IncompleteRead) as failure:
                    response.read()
        self.assertTrue(captured[0].closed)
        return failure.exception.partial

    def test_short_body_closes_connection_without_appending_a_json_error(self):
        partial = self.bad_stream_request(lambda: io.BytesIO(synthetic_png()), len(synthetic_png()) + 10)
        self.assertEqual(partial, synthetic_png())
        self.assertNotIn(b'{"ok"', partial)

    def test_closed_reader_exception_after_headers_is_not_a_second_http_response(self):
        class BadReader(io.BytesIO):
            def read(self, *args):
                raise ValueError("synthetic reader failure")
        self.assertEqual(self.bad_stream_request(BadReader, 10), b"")

    def test_wrong_actor_and_missing_scope_still_stop_before_opening_stream(self):
        with patch.object(self.service.frozen_media, "open_snapshot") as snapshot:
            self.assertEqual(self.request(self.media, {"X-Contentbase-Identity": ""})[0], 400)
            self.service.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
            self.assertEqual(self.request(self.media)[0], 409)
            snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
