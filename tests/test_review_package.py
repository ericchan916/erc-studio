"""Real local preview assets, private package verification and hostile metadata."""
import copy
from contextlib import contextmanager
import io
import tracemalloc
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

from contentbase.errors import BaseError
from contentbase.review_package import export_review, verify_review_package


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0")) + chunk(b"IEND", b"")


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class ReviewPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data = png()
        self.review = {"operation_id": "op_REVIEW_EXAMPLE", "confirmation_digest": "a" * 64, "media_review_digest": "b" * 64,
                       "title": "冻结标题", "body": "完整第一段\n\n完整第二段", "platform": "平台", "account_id": "ACCOUNT_EXAMPLE",
                       "kind": "imageText", "channel": "local", "expires_at": time.time() + 1800,
                       "media": [{"index": 0, "name": "image.png", "usage": "image", "size": len(self.data), "sha256": sha256(self.data).hexdigest(), "mime": "image/png"}]}
    def reader(self, index):
        return {"data": self.data, "size": len(self.data), "sha256": sha256(self.data).hexdigest(), "mime": "image/png"}
    def test_real_png_and_complete_package_reopen(self):
        target = self.root / "review"
        result = export_review(self.review, self.reader, target)
        self.assertEqual(result["directory"], str(target))
        manifest = json.loads((target / "review.json").read_text())
        self.assertEqual(manifest["body"], self.review["body"])
        self.assertFalse(manifest["approved"])
        self.assertFalse(manifest["published"])
        self.assertEqual(sha256((target / "review.json").read_bytes()).hexdigest(), result["manifest_digest"])
        self.assertEqual((target / "media/00-image.png").read_bytes(), self.data)
        compressed = self.data[self.data.index(b"IDAT") + 4:-16]
        self.assertEqual(zlib.decompress(compressed), b"\0\xff\0\0")
        for line in (target / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(sha256((target / name).read_bytes()).hexdigest(), digest)
        for name, mode in (("", 0o700), ("media", 0o700), ("index.html", 0o600), ("review.json", 0o600), ("SHA256SUMS", 0o600), ("media/00-image.png", 0o400)):
            self.assertEqual((target / name).stat().st_mode & 0o777, mode)
        page = (target / "index.html").read_text()
        self.assertIn("完整第一段\n\n完整第二段", page)
        self.assertIn("仅审核", page)
        self.assertIn(self.review["confirmation_digest"], page)
        self.assertIn(self.review["media"][0]["sha256"], page)
    def test_html_and_filename_injection_is_text_not_active_elements(self):
        for field in ("title", "body", "platform", "account_id"):
            self.review[field] = '<script>alert("fixture")</script><img src="https://example.invalid/" onerror="fixture()">'
        self.review["media"][0]["name"] = '<img src=x onerror=fixture()>.png'
        target = self.root / "review"
        export_review(self.review, self.reader, target)
        page = (target / "index.html").read_text()
        tags = Tags()
        tags.feed(page)
        self.assertFalse(any(tag in {"script", "form", "iframe", "object", "input", "button"} for tag, attrs in tags.tags))
        imgs = [attrs for tag, attrs in tags.tags if tag == "img"]
        self.assertEqual(len(imgs), 1)
        self.assertEqual(imgs[0]["src"], "media/00-image.png")
        self.assertNotIn("onerror", imgs[0])
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("connect-src &#x27;none&#x27;", page)
    def test_reader_metadata_size_hash_mime_and_bytes_cannot_be_forged(self):
        for key, value in (("size", True), ("size", len(self.data) + 1), ("sha256", "c" * 64), ("mime", "video/mp4"), ("data", b"wrong bytes")):
            with self.subTest(key=key, value_type=type(value).__name__):
                packet = self.reader(0)
                packet[key] = value
                target = self.root / ("bad-" + key)
                with self.assertRaises(BaseError):
                    export_review(self.review, lambda _: packet, target)
                self.assertFalse(target.exists())
                self.assertEqual(list(self.root.glob(".review-stage-*")), [])
    def test_missing_media_and_relabelled_original_metadata_stop_export(self):
        for reader in (lambda _: None, lambda _: (_ for _ in ()).throw(FileNotFoundError())):
            with self.assertRaises(BaseError):
                export_review(self.review, reader, self.root / "missing")
        changed = copy.deepcopy(self.review)
        changed["media"][0]["mime"] = "image/jpeg"
        with self.assertRaises(BaseError):
            export_review(changed, self.reader, self.root / "changed")
        self.assertEqual(list(self.root.glob(".review-stage-*")), [])
    def test_existing_directory_or_symlink_is_not_overwritten(self):
        target = self.root / "existing"
        target.mkdir()
        sentinel = target / "sentinel"
        sentinel.write_text("preserved")
        with self.assertRaises(BaseError):
            export_review(self.review, self.reader, target)
        self.assertEqual(sentinel.read_text(), "preserved")
        link = self.root / "linked"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(BaseError):
            export_review(self.review, self.reader, link)
        self.assertTrue(link.is_symlink())
    def test_parent_chain_symlink_and_traversal_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        for target in (alias / "review", self.root / ".." / "traversal"):
            with self.assertRaises(BaseError):
                export_review(self.review, self.reader, target)
        self.assertEqual(list(outside.iterdir()), [])
        for name in ("../image.png", "nested/image.png", "nested\\image.png"):
            bad = copy.deepcopy(self.review)
            bad["media"][0]["name"] = name
            with self.assertRaises(BaseError):
                export_review(bad, self.reader, self.root / "bad-name")
    def test_destination_cannot_be_inside_source(self):
        import contentbase.review_package as module
        with patch.object(module, "SOURCE_ROOT", self.root):
            with self.assertRaises(BaseError):
                export_review(self.review, self.reader, self.root / "review")
    def test_video_and_cover_keep_original_order_and_bytes(self):
        video = struct.pack(">I", 24) + b"ftypisom" + b"\0\0\0\0isommp42"
        cover = self.review["media"][0]
        cover["index"], cover["usage"] = 1, "cover"
        item = {"index": 0, "name": "video.mp4", "usage": "video", "size": len(video), "sha256": sha256(video).hexdigest(), "mime": "video/mp4"}
        self.review["kind"], self.review["media"] = "video", [item, cover]
        def reader(index):
            return {"data": video, "size": len(video), "sha256": sha256(video).hexdigest(), "mime": "video/mp4"} if index == 0 else self.reader(index)
        target = self.root / "video-review"
        export_review(self.review, reader, target)
        self.assertEqual((target / "media/00-video.mp4").read_bytes(), video)
        self.assertEqual((target / "media/01-cover.png").read_bytes(), self.data)
        tags = Tags()
        tags.feed((target / "index.html").read_text())
        self.assertEqual([attrs["src"] for tag, attrs in tags.tags if tag in {"video", "img"}], ["media/00-video.mp4", "media/01-cover.png"])
    def test_repeated_verification_does_not_rewrite_a_valid_package(self):
        target = self.root / "review"
        original = export_review(self.review, self.reader, target)
        dates = {str(path): path.stat().st_mtime_ns for path in target.rglob("*")}
        self.assertEqual(verify_review_package(self.review, target), original)
        self.assertEqual(original["manifest_path"], str(target / "review.json"))
        self.assertEqual(dates, {str(path): path.stat().st_mtime_ns for path in target.rglob("*")})
    def test_reopen_rejects_metadata_html_asset_permissions_and_extra_files(self):
        mutations = (
            lambda target: (target / "review.json").write_text("{}"),
            lambda target: (target / "index.html").write_text("changed HTML"),
            lambda target: (target / "SHA256SUMS").write_text("changed checksum"),
            lambda target: (target / "extra.txt").write_text("extra"),
            lambda target: (target / "media/extra.png").write_bytes(self.data),
            lambda target: (target / "media/00-image.png").chmod(0o600),
            lambda target: target.chmod(0o755),
        )
        for index, mutate in enumerate(mutations):
            target = self.root / ("review-" + str(index))
            export_review(self.review, self.reader, target)
            mutate(target)
            with self.assertRaises(BaseError):
                verify_review_package(self.review, target)
    def test_reopen_rejects_media_symlink_and_changed_approval_context(self):
        target = self.root / "review"
        export_review(self.review, self.reader, target)
        changed = copy.deepcopy(self.review)
        changed["account_id"] = "OTHER_ACCOUNT_EXAMPLE"
        with self.assertRaises(BaseError):
            verify_review_package(changed, target)
        image = target / "media/00-image.png"
        outside = self.root / "outside.png"
        outside.write_bytes(self.data)
        outside.chmod(0o400)
        image.unlink()
        image.symlink_to(outside)
        with self.assertRaises(BaseError):
            verify_review_package(self.review, target)

    def test_quicktime_preserves_mov_extension_and_bytes(self):
        movie = struct.pack(">I", 20) + b"ftypqt  " + b"\0\0\0\0qt  "
        cover = copy.deepcopy(self.review["media"][0])
        cover["index"], cover["usage"] = 1, "cover"
        self.review["kind"] = "video"
        self.review["media"] = [{"index": 0, "name": "video.mov", "usage": "video", "size": len(movie), "sha256": sha256(movie).hexdigest(), "mime": "video/quicktime"}, cover]
        def reader(index):
            return {"data": movie, "size": len(movie), "sha256": sha256(movie).hexdigest(), "mime": "video/quicktime"} if index == 0 else self.reader(index)
        target = self.root / "movie-review"
        export_review(self.review, reader, target)
        self.assertEqual((target / "media/00-video.mov").read_bytes(), movie)
        self.assertEqual(json.loads((target / "review.json").read_text())["media"][0]["mime"], "video/quicktime")

    def test_directory_listing_uses_a_fresh_anchored_description_after_file_io(self):
        import contentbase.review_package as module
        folder = self.root / "enumeration"
        folder.mkdir()
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in ("review.json", "index.html", "SHA256SUMS"):
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                os.write(descriptor, b"enumeration-fixture")
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                while os.read(descriptor, 1024):
                    pass
                os.close(descriptor)
            self.assertEqual(set(module._entries(directory)), {"review.json", "index.html", "SHA256SUMS"})
            self.assertEqual(set(module._entries(directory)), {"review.json", "index.html", "SHA256SUMS"})
        finally:
            os.close(directory)

    def test_concurrent_destination_reservation_is_rejected_atomically(self):
        import contentbase.review_package as module
        target = self.root / "review"
        original = module._rename_exclusive
        def raced(parent, source, destination):
            target.mkdir()
            original(parent, source, destination)
        with patch.object(module, "_rename_exclusive", side_effect=raced):
            with self.assertRaises(BaseError):
                export_review(self.review, self.reader, target)
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(list(self.root.glob(".review-stage-*")), [])


class MediaLayerAuditTests(unittest.TestCase):
    def setUp(self):
        import test_service as fixture
        self.fixture = fixture.ServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.service = self.fixture.service

    def test_failed_upload_with_snapshot_has_structured_review_error(self):
        def quota_error(*args):
            raise BaseError("fixture_quota", category="quota")
        self.fixture.yxer.upload = quota_error
        with self.assertRaises(BaseError):
            self.fixture.publish()
        operation = self.service.inspect()[0]
        self.assertTrue(operation["data"].get("frozen_media"))
        with self.assertRaises(BaseError):
            self.service.media_review(operation["id"])

    def test_actor_change_during_frozen_read_stops_previous_private_bytes(self):
        operation = self.fixture.publish()
        original = self.service.frozen_media.read
        def changed(*args, **kwargs):
            packet = original(*args, **kwargs)
            self.fixture.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
            return packet
        with patch.object(self.service.frozen_media, "read", side_effect=changed):
            with self.assertRaises(BaseError):
                self.service.media_bytes(operation["id"], 0, 0, 3)

    def test_actor_change_during_full_review_stops_previous_private_metadata(self):
        operation = self.fixture.publish()
        original = self.service.frozen_media.verify
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.fixture.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
            return result
        with patch.object(self.service.frozen_media, "verify", side_effect=changed):
            with self.assertRaises(BaseError):
                self.service.media_review(operation["id"])

    def test_actual_service_exports_through_stream_context_not_full_bytes(self):
        operation = self.fixture.publish()
        with patch.object(self.service, "media_bytes", side_effect=AssertionError("full-buffer reader must not be used")):
            packet = self.service.export_publish_review(operation["id"])
        self.assertTrue(Path(packet["manifest_path"]).is_file())
        self.assertEqual(self.fixture.yxer.submits, 0)

    def test_giant_range_integer_remains_structured_range_error(self):
        from contentbase.server import media_range
        with self.assertRaises(BaseError):
            media_range("bytes=" + "9" * 5000 + "-", 100)


class ReviewStreamTests(unittest.TestCase):
    setUp = ReviewPackageTests.setUp

    def packet(self, stream, item=None):
        item = item or self.review["media"][0]
        return {"stream": stream, "size": item["size"], "sha256": item["sha256"], "mime": item["mime"],
                "content_length": item["size"], "start": 0, "end": item["size"] - 1, "range_sha256": item["sha256"]}

    def context_reader(self, packet):
        @contextmanager
        def reader(index):
            try:
                yield packet
            finally:
                packet["stream"].close()
        return reader

    def test_context_stream_real_png_uses_only_bounded_reads_and_closes(self):
        calls = []
        class Reader(io.BytesIO):
            def read(inner, amount=-1):
                calls.append(amount)
                self.assertTrue(0 < amount <= 1024 * 1024)
                return super().read(amount)
        stream = Reader(self.data)
        target = self.root / "stream-review"
        result = export_review(self.review, self.context_reader(self.packet(stream)), target)
        self.assertTrue(stream.closed)
        self.assertEqual((target / "media/00-image.png").read_bytes(), self.data)
        self.assertEqual(result, verify_review_package(self.review, target))
        self.assertTrue(calls)

    def test_plain_stream_dictionary_requires_an_owning_context(self):
        stream = io.BytesIO(self.data)
        self.addCleanup(stream.close)
        with self.assertRaisesRegex(BaseError, "review_media_context_required"):
            export_review(self.review, lambda index: self.packet(stream), self.root / "unowned-stream")
        self.assertFalse((self.root / "unowned-stream").exists())
        self.assertEqual(list(self.root.glob(".review-stage-*")), [])

    def test_zero_short_extra_and_corrupt_streams_cleanup_own_staging(self):
        for index, data in enumerate((b"", self.data[:-1], self.data + b"extra", self.data[:-1] + b"x")):
            with self.subTest(index=index):
                stream = io.BytesIO(data)
                target = self.root / ("invalid-stream-" + str(index))
                with self.assertRaises(BaseError):
                    export_review(self.review, self.context_reader(self.packet(stream)), target)
                self.assertTrue(stream.closed)
                self.assertFalse(target.exists())
                self.assertEqual(list(self.root.glob(".review-stage-*")), [])

    def test_full_range_metadata_is_exact_and_boolean_fields_are_rejected(self):
        for index, (key, value) in enumerate((("size", True), ("content_length", True), ("content_length", len(self.data) - 1),
                                             ("start", 1), ("end", len(self.data) - 2), ("sha256", "c" * 64),
                                             ("range_sha256", "c" * 64), ("mime", "image/jpeg"))):
            stream = io.BytesIO(self.data)
            packet = self.packet(stream)
            packet[key] = value
            with self.subTest(key=key), self.assertRaises(BaseError):
                export_review(self.review, self.context_reader(packet), self.root / ("metadata-" + str(index)))
            self.assertTrue(stream.closed)
            self.assertEqual(list(self.root.glob(".review-stage-*")), [])

    def test_invalid_or_raising_readers_remain_typed_and_cleanup(self):
        class Reader:
            def __init__(inner, mode):
                inner.mode, inner.closed = mode, False
            def read(inner, amount):
                if inner.mode == "raise":
                    raise OSError("synthetic read error")
                if inner.mode == "oversized":
                    return b"x" * (amount + 1)
                return None if inner.mode == "none" else "text is not binary"
            def close(inner):
                inner.closed = True
        for mode in ("raise", "oversized", "none", "text"):
            stream = Reader(mode)
            with self.subTest(mode=mode), self.assertRaises(BaseError):
                export_review(self.review, self.context_reader(self.packet(stream)), self.root / mode)
            self.assertTrue(stream.closed)
            self.assertEqual(list(self.root.glob(".review-stage-*")), [])

    def test_context_enter_and_exit_failures_do_not_deliver_package(self):
        @contextmanager
        def enter_failure(index):
            raise BaseError("synthetic_context_enter")
            yield
        @contextmanager
        def exit_failure(index):
            with io.BytesIO(self.data) as stream:
                yield self.packet(stream)
            raise BaseError("synthetic_context_exit")
        for index, reader in enumerate((enter_failure, exit_failure)):
            target = self.root / ("context-error-" + str(index))
            with self.assertRaises(BaseError):
                export_review(self.review, reader, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(self.root.glob(".review-stage-*")), [])

    def test_context_cannot_suppress_a_copy_integrity_failure(self):
        packet = self.packet(io.BytesIO(b"short"))
        class Suppressor:
            def __enter__(inner):
                return packet
            def __exit__(inner, *args):
                packet["stream"].close()
                return True
        with self.assertRaises(BaseError):
            export_review(self.review, lambda index: Suppressor(), self.root / "suppressed")
        self.assertFalse((self.root / "suppressed").exists())
        self.assertEqual(list(self.root.glob(".review-stage-*")), [])

    def test_large_generated_video_has_bounded_python_allocation(self):
        total = 32 * 1024 * 1024
        header = struct.pack(">I", 24) + b"ftypisom" + b"\0\0\0\0isommp42"
        calls = []
        class Generated:
            def __init__(inner):
                inner.position, inner.closed = 0, False
            def read(inner, amount=-1):
                if not 0 < amount <= 1024 * 1024:
                    raise AssertionError("unbounded stream read")
                calls.append(amount)
                size = min(amount, total - inner.position)
                if size == 0:
                    return b""
                prefix = header[inner.position:inner.position + size] if inner.position < len(header) else b""
                inner.position += size
                return prefix + b"v" * (size - len(prefix))
            def close(inner):
                inner.closed = True
        source, hashed = Generated(), sha256()
        while block := source.read(1024 * 1024):
            hashed.update(block)
        item = {"index": 0, "name": "synthetic-video.mp4", "usage": "video", "size": total, "sha256": hashed.hexdigest(), "mime": "video/mp4"}
        cover = copy.deepcopy(self.review["media"][0])
        cover["index"], cover["usage"] = 1, "cover"
        self.review["kind"], self.review["media"] = "video", [item, cover]
        video = Generated()
        @contextmanager
        def reader(index):
            stream = video if index == 0 else io.BytesIO(self.data)
            try:
                yield self.packet(stream, item if index == 0 else cover)
            finally:
                stream.close()
        tracemalloc.start()
        try:
            target = self.root / "large-review"
            export_review(self.review, reader, target)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(video.closed)
        self.assertEqual((target / "media/00-video.mp4").stat().st_size, total)
        self.assertLess(peak, 8 * 1024 * 1024, "32 MiB video must not be buffered as a whole asset")
        self.assertTrue(all(0 < amount <= 1024 * 1024 for amount in calls))


if __name__ == "__main__":
    unittest.main()
