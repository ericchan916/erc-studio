"""Anonymous range snapshots; synthetic files only, no provider operations."""
import copy
import hashlib
import os
from pathlib import Path
import stat
import struct
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
import zlib
from contentbase.errors import BaseError
from contentbase import frozen_media
from contentbase.frozen_media import CHUNK, IMAGE_LIMIT, FrozenMedia, inspect_media

OP = "op_" + "c" * 32


def png_bytes():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0\0\0\0\xff")) + chunk(b"IEND", b"")


def write_sparse_mov(path, size):
    header = struct.pack(">I", 20) + b"ftypqt  " + struct.pack(">I", 0) + b"qt  "
    with path.open("wb") as stream:
        stream.write(header)
        stream.truncate(size)


class MediaStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "example.png"
        self.source.write_bytes(png_bytes())
        self.media = FrozenMedia(self.root / "state")
        self.manifest = self.media.freeze(OP, [inspect_media(self.source, "image")])
        self.folder = self.root / "state" / "publish" / OP / "media"
        self.names = set(os.listdir(self.folder))

    def assert_no_named_snapshot(self):
        self.assertEqual(set(os.listdir(self.folder)), self.names)
        self.assertFalse(any(name.startswith(".snapshot-") for name in os.listdir(self.folder)))

    def test_full_snapshot_is_binary_readable_anonymous_and_private(self):
        with self.media.open_snapshot(OP, self.manifest, 0) as packet:
            self.assertEqual(set(packet), {"stream", "mime", "size", "start", "end", "sha256", "content_length", "range_sha256"})
            stream = packet["stream"]
            self.assertTrue(stream.readable())
            self.assertFalse(stream.writable())
            self.assertIsInstance(stream.name, int)
            descriptor = stream.fileno()
            self.assertEqual(os.fstat(descriptor).st_nlink, 0)
            self.assertEqual(stat.S_IMODE(os.fstat(descriptor).st_mode), 0o600)
            self.assertEqual(packet["content_length"], len(png_bytes()))
            self.assertEqual(packet["range_sha256"], packet["sha256"])
            self.assertEqual(stream.read(CHUNK), png_bytes())
            self.assertEqual(stream.read(CHUNK), b"")
            self.assert_no_named_snapshot()
        self.assertTrue(stream.closed)
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assert_no_named_snapshot()

    def test_range_is_inclusive_and_snapshot_only_has_selected_bytes(self):
        with self.media.open_snapshot(OP, self.manifest, 0, 8, 15) as packet:
            self.assertEqual(packet["stream"].read(CHUNK), png_bytes()[8:16])
            self.assertEqual(os.fstat(packet["stream"].fileno()).st_size, 8)
            self.assertEqual((packet["start"], packet["end"], packet["content_length"]), (8, 15, 8))
            self.assertEqual(packet["sha256"], hashlib.sha256(png_bytes()).hexdigest())
            self.assertEqual(packet["range_sha256"], hashlib.sha256(png_bytes()[8:16]).hexdigest())
            self.assertNotEqual(packet["range_sha256"], packet["sha256"])
            packet["stream"].seek(0)
            self.assertEqual(packet["stream"].read(CHUNK), png_bytes()[8:16])
        self.assert_no_named_snapshot()

    def test_source_descriptor_closed_before_yield_and_snapshot_independent(self):
        source_fds = []
        original = frozen_media._spool_range
        def capture(source, *args):
            source_fds.append(source)
            return original(source, *args)
        with patch("contentbase.frozen_media._spool_range", side_effect=capture):
            with self.media.open_snapshot(OP, self.manifest, 0) as packet:
                with self.assertRaises(OSError):
                    os.fstat(source_fds[0])
                path = Path(self.media.path(OP, self.manifest[0]))
                path.chmod(0o600)
                path.write_bytes(b"changed after complete verified snapshot")
                self.assertEqual(packet["stream"].read(CHUNK), png_bytes())
        self.assert_no_named_snapshot()

    def test_consumer_exception_abort_and_broken_pipe_close_snapshot(self):
        for error in (RuntimeError("consumer abort"), BrokenPipeError("consumer abort")):
            with self.subTest(type=type(error).__name__):
                with self.assertRaises(type(error)):
                    with self.media.open_snapshot(OP, self.manifest, 0) as packet:
                        descriptor = packet["stream"].fileno()
                        raise error
                self.assertTrue(packet["stream"].closed)
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
                self.assert_no_named_snapshot()
        manager = self.media.open_snapshot(OP, self.manifest, 0)
        packet = manager.__enter__()
        descriptor = packet["stream"].fileno()
        self.assertFalse(manager.__exit__(GeneratorExit, GeneratorExit(), None))
        self.assertTrue(packet["stream"].closed)
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assert_no_named_snapshot()

    def test_bad_manifest_is_rejected_before_snapshot_creation(self):
        bad = copy.deepcopy(self.manifest)
        bad[0]["name"] = "changed-name.png"
        with patch("contentbase.frozen_media._anonymous_snapshot") as create:
            with self.assertRaisesRegex(BaseError, "frozen_media_manifest_changed"):
                with self.media.open_snapshot(OP, bad, 0):
                    self.fail("unverified metadata yielded")
            create.assert_not_called()
        self.assert_no_named_snapshot()

    def test_hash_tamper_is_rejected_before_snapshot_creation(self):
        path = Path(self.media.path(OP, self.manifest[0]))
        path.chmod(0o600)
        altered = bytearray(png_bytes())
        altered[-1] ^= 1
        path.write_bytes(altered)
        with patch("contentbase.frozen_media._anonymous_snapshot") as create:
            with self.assertRaises(BaseError):
                with self.media.open_snapshot(OP, self.manifest, 0):
                    self.fail("tampered asset yielded")
            create.assert_not_called()
        self.assert_no_named_snapshot()

    def test_outside_range_change_after_first_hash_still_blocks_yield(self):
        path = Path(self.media.path(OP, self.manifest[0]))
        original = frozen_media._spool_range
        snapshots = []
        create = frozen_media._anonymous_snapshot
        def save(directory):
            descriptor = create(directory)
            snapshots.append(descriptor)
            return descriptor
        def tamper(source, *args):
            path.chmod(0o600)
            changed = bytearray(png_bytes())
            changed[-1] ^= 1
            path.write_bytes(changed)
            return original(source, *args)
        with patch("contentbase.frozen_media._anonymous_snapshot", side_effect=save), patch("contentbase.frozen_media._spool_range", side_effect=tamper):
            with self.assertRaises(BaseError):
                with self.media.open_snapshot(OP, self.manifest, 0, 0, 7):
                    self.fail("correct selected range cannot substitute for complete hash")
        with self.assertRaises(OSError):
            os.fstat(snapshots[0])
        self.assert_no_named_snapshot()

    def test_snapshot_corruption_is_checked_before_yield(self):
        original = frozen_media._spool_range
        descriptors = []
        def corrupt(source, target, *args):
            result = original(source, target, *args)
            descriptors.append(target)
            os.lseek(target, 0, os.SEEK_SET)
            os.write(target, b"X")
            return result
        with patch("contentbase.frozen_media._spool_range", side_effect=corrupt), self.assertRaisesRegex(BaseError, "media_snapshot_integrity_failed"):
            with self.media.open_snapshot(OP, self.manifest, 0):
                self.fail("unverified snapshot yielded")
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])
        self.assert_no_named_snapshot()

    def test_spool_exception_closes_descriptor_and_leaves_no_named_file(self):
        created = []
        original = frozen_media._anonymous_snapshot
        def capture(directory):
            descriptor = original(directory)
            created.append(descriptor)
            return descriptor
        with patch("contentbase.frozen_media._anonymous_snapshot", side_effect=capture), patch("contentbase.frozen_media._spool_range", side_effect=OSError("injected disk failure")), self.assertRaises(BaseError):
            with self.media.open_snapshot(OP, self.manifest, 0):
                self.fail("failed copy yielded")
        with self.assertRaises(OSError):
            os.fstat(created[0])
        self.assert_no_named_snapshot()

    def test_unlink_failure_closes_new_fd_and_cleans_empty_temporary_name(self):
        opened = []
        original_open, original_unlink = os.open, os.unlink
        failed = False
        def capture(name, flags, *args, **kwargs):
            descriptor = original_open(name, flags, *args, **kwargs)
            if isinstance(name, str) and name.startswith(".snapshot-"):
                opened.append(descriptor)
            return descriptor
        def reject_once(name, **kwargs):
            nonlocal failed
            if isinstance(name, str) and name.startswith(".snapshot-") and not failed:
                failed = True
                raise PermissionError("injected unlink failure")
            return original_unlink(name, **kwargs)
        with patch("contentbase.frozen_media.os.open", side_effect=capture), patch("contentbase.frozen_media.os.unlink", side_effect=reject_once), self.assertRaisesRegex(BaseError, "media_snapshot_unavailable"):
            with self.media.open_snapshot(OP, self.manifest, 0):
                self.fail("named snapshot must never yield")
        self.assertTrue(failed)
        with self.assertRaises(OSError):
            os.fstat(opened[0])
        self.assert_no_named_snapshot()

    def test_invalid_ranges_indexes_jobs_and_symlink_are_rejected(self):
        for start, end in ((-1, 2), (8, 2), (0, len(png_bytes())), (True, 3)):
            with self.subTest(start=start, end=end), self.assertRaises(BaseError):
                with self.media.open_snapshot(OP, self.manifest, 0, start, end):
                    self.fail("invalid range yielded")
        for job, index in (("../bad", 0), (OP, -1), (OP, True), (OP, 1)):
            with self.subTest(job=job, index=index), self.assertRaises(BaseError):
                with self.media.open_snapshot(job, self.manifest, index):
                    self.fail("invalid route yielded")
        path = Path(self.media.path(OP, self.manifest[0]))
        path.unlink()
        path.symlink_to(self.source)
        with self.assertRaises(BaseError):
            with self.media.open_snapshot(OP, self.manifest, 0):
                self.fail("symlink asset yielded")
        self.assert_no_named_snapshot()

    def test_legacy_bytes_limit_fails_without_spooling_large_video(self):
        video = self.root / "large.mov"
        write_sparse_mov(video, IMAGE_LIMIT + 1)
        second = "op_" + "d" * 32
        manifest = self.media.freeze(second, [inspect_media(video, "video")])
        with patch.object(self.media, "_file") as open_asset, self.assertRaisesRegex(BaseError, "media_bytes_limit_use_stream"):
            self.media.read(second, manifest, 0)
        open_asset.assert_not_called()
        self.assertEqual(self.media.read(second, manifest, 0, 0, 7)["data"], struct.pack(">I", 20) + b"ftyp")

    def test_large_generated_video_stream_has_bounded_python_allocations(self):
        video = self.root / "generated.mov"
        size = 64 * 1024 * 1024
        write_sparse_mov(video, size)
        second = "op_" + "e" * 32
        manifest = self.media.freeze(second, [inspect_media(video, "video")])
        digest, total = hashlib.sha256(), 0
        tracemalloc.start()
        try:
            with self.media.open_snapshot(second, manifest, 0) as packet:
                while True:
                    block = packet["stream"].read(CHUNK)
                    if not block:
                        break
                    self.assertLessEqual(len(block), CHUNK)
                    digest.update(block)
                    total += len(block)
                self.assertEqual(packet["content_length"], size)
                self.assertEqual(os.fstat(packet["stream"].fileno()).st_size, size)
                self.assertEqual(digest.hexdigest(), packet["range_sha256"])
                self.assertEqual(packet["range_sha256"], packet["sha256"])
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(total, size)
        self.assertLess(peak, 8 * CHUNK)
        self.assertFalse(any(name.startswith(".snapshot-") for name in os.listdir(self.root / "state" / "publish" / second / "media")))


if __name__ == "__main__":
    unittest.main()
