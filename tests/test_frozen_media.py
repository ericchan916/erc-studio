"""Only synthetic valid media headers/PNG pixels; no vendor upload or publication."""
import copy
import hashlib
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib
from contentbase.errors import BaseError
from contentbase.frozen_media import FrozenMedia, IMAGE_LIMIT, VIDEO_LIMIT, inspect_media

OP = "op_" + "a" * 32
OTHER = "op_" + "b" * 32


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff")) + chunk(b"IEND", b""))


def mov():
    # A structurally valid QuickTime file-type header and a harmless free box.
    ftyp = b"qt  " + struct.pack(">I", 0) + b"qt  "
    return struct.pack(">I", len(ftyp) + 8) + b"ftyp" + ftyp + struct.pack(">I", 12) + b"free" + b"\0" * 4


class FrozenMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source.png"
        self.source.write_bytes(png())
        self.source.chmod(0o640)
        self.state = self.root / "state"
        self.frozen = FrozenMedia(self.state)

    def freeze_png(self, op=OP):
        return self.frozen.freeze(op, [inspect_media(self.source, "image")])

    def test_inspect_valid_png_and_mov_headers(self):
        info = inspect_media(self.source, "image")
        self.assertEqual(info["mime"], "image/png")
        self.assertEqual(info["sha256"], hashlib.sha256(png()).hexdigest())
        self.assertEqual(info["size"], len(png()))
        self.assertTrue(Path(info["path"]).is_absolute())
        self.assertEqual(info["name"], "source.png")
        self.assertEqual(set(info["source_identity"]), {"device", "inode", "size", "mtime_ns", "ctime_ns"})
        video = self.root / "example.mov"
        video.write_bytes(mov())
        self.assertEqual(inspect_media(video, "video")["mime"], "video/quicktime")
        self.assertEqual(inspect_media(self.source, "cover")["usage"], "cover")

    def test_header_suffix_and_role_mismatch_are_rejected(self):
        wrong = self.root / "wrong.jpg"
        wrong.write_bytes(png())
        with self.assertRaises(BaseError):
            inspect_media(wrong, "image")
        with self.assertRaises(BaseError):
            inspect_media(self.source, "video")
        wrong.write_bytes(b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(BaseError):
            inspect_media(wrong, "image")

    def test_source_symlink_directory_and_fifo_are_rejected_without_blocking(self):
        link = self.root / "link.png"
        link.symlink_to(self.source)
        for path in (link, self.root):
            with self.subTest(path=path.name), self.assertRaises(BaseError):
                inspect_media(path, "image")
        if hasattr(os, "mkfifo"):
            fifo = self.root / "pipe.png"
            os.mkfifo(fifo)
            with self.assertRaises(BaseError):
                inspect_media(fifo, "image")

    def test_strict_size_limits_use_stat_before_streaming(self):
        self.assertEqual(IMAGE_LIMIT, 20 * 1024 * 1024)
        self.assertEqual(VIDEO_LIMIT, 512 * 1024 * 1024)
        for filename, content, usage, maximum in (("big.png", png(), "image", IMAGE_LIMIT), ("big.mov", mov(), "video", VIDEO_LIMIT)):
            path = self.root / filename
            with path.open("wb") as stream:
                stream.write(content)
                stream.truncate(maximum + 1)
            with self.subTest(usage=usage), self.assertRaisesRegex(BaseError, "media_size_limit"):
                inspect_media(path, usage)
        with patch("contentbase.frozen_media.IMAGE_LIMIT", len(png())):
            self.assertEqual(inspect_media(self.source, "image")["size"], len(png()))
        with patch("contentbase.frozen_media.IMAGE_LIMIT", len(png()) - 1), self.assertRaises(BaseError):
            inspect_media(self.source, "image")

    def test_freeze_keeps_source_and_uses_private_readonly_copies(self):
        before = self.source.stat()
        manifest = self.freeze_png()
        target = Path(self.frozen.path(OP, manifest[0]))
        self.assertEqual(target.read_bytes(), png())
        self.assertTrue(self.source.exists())
        self.assertEqual(self.source.read_bytes(), png())
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o400)
        for directory in (self.state / "publish", target.parent.parent, target.parent):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((target.parent / ".manifest.json").stat().st_mode), 0o400)
        self.assertNotIn("path", manifest[0])
        self.assertNotIn("source_identity", manifest[0])
        self.assertEqual(Path(manifest[0]["filename"]).name, manifest[0]["filename"])
        self.assertTrue(self.frozen.verify(OP, manifest))

    def test_source_replacement_after_freeze_does_not_change_frozen_bytes(self):
        manifest = self.freeze_png()
        replacement = self.root / "replacement.png"
        replacement.write_bytes(png() + b"new source content")
        replacement.replace(self.source)
        self.assertEqual(self.frozen.read(OP, manifest, 0)["data"], png())
        self.source.write_bytes(b"changed again")
        self.assertEqual(self.frozen.read(OP, manifest, 0)["data"], png())

    def test_changed_source_between_inspect_and_copy_is_rejected(self):
        info = inspect_media(self.source, "image")
        self.source.write_bytes(png() + b"changed")
        with self.assertRaisesRegex(BaseError, "media_source_changed"):
            self.frozen.freeze(OP, [info])

    def test_same_bytes_replaced_inode_between_inspect_and_copy_is_rejected(self):
        info = inspect_media(self.source, "image")
        replacement = self.root / "same.png"
        replacement.write_bytes(png())
        replacement.replace(self.source)
        with self.assertRaisesRegex(BaseError, "media_source_changed"):
            self.frozen.freeze(OP, [info])

    def test_source_content_write_during_copy_is_rejected(self):
        info = inspect_media(self.source, "image")
        real_write = os.write
        changed = False
        def race(descriptor, data):
            nonlocal changed
            written = real_write(descriptor, data)
            if not changed:
                changed = True
                altered = bytearray(png())
                altered[-1] ^= 1
                self.source.write_bytes(altered)
            return written
        from contentbase import frozen_media
        actual_identity = frozen_media._identity
        def same_tick_identity(value):
            # Real Linux tmpfs reproduced this: same inode/size/timestamps despite
            # changed bytes. Make that condition deterministic on other hosts too.
            current = actual_identity(value)
            current["mtime_ns"] = info["source_identity"]["mtime_ns"]
            current["ctime_ns"] = info["source_identity"]["ctime_ns"]
            return current
        with patch("contentbase.frozen_media._identity", side_effect=same_tick_identity), patch("contentbase.frozen_media.os.write", side_effect=race), self.assertRaisesRegex(BaseError, "media_source_changed"):
            self.frozen.freeze(OP, [info])
        self.assertTrue(changed)
        self.assertTrue(self.source.exists())

    def test_source_path_replacement_during_copy_is_rejected(self):
        info = inspect_media(self.source, "image")
        real_write = os.write
        replacement = self.root / "same.png"
        replacement.write_bytes(png())
        changed = False
        def race(descriptor, data):
            nonlocal changed
            written = real_write(descriptor, data)
            if not changed:
                changed = True
                replacement.replace(self.source)
            return written
        with patch("contentbase.frozen_media.os.write", side_effect=race), self.assertRaises(BaseError):
            self.frozen.freeze(OP, [info])
        self.assertTrue(changed)

    def test_hash_tamper_rejected_even_with_original_mtime(self):
        manifest = self.freeze_png()
        target = Path(self.frozen.path(OP, manifest[0]))
        old = target.stat()
        target.chmod(0o600)
        changed = bytearray(target.read_bytes())
        changed[-1] ^= 1
        target.write_bytes(changed)
        os.utime(target, ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaisesRegex(BaseError, "frozen_media_integrity_failed"):
            self.frozen.verify(OP, manifest)
        with self.assertRaises(BaseError):
            self.frozen.read(OP, manifest, 0)

    def test_manifest_metadata_changes_are_rejected(self):
        manifest = self.freeze_png()
        for key, value in (("name", "renamed.png"), ("usage", "cover"), ("mime", "image/jpeg"), ("size", manifest[0]["size"] + 1)):
            altered = copy.deepcopy(manifest)
            altered[0][key] = value
            with self.subTest(key=key), self.assertRaises(BaseError):
                self.frozen.verify(OP, altered)
            with self.subTest(path_key=key), self.assertRaises(BaseError):
                self.frozen.path(OP, altered[0])

    def test_manifest_filename_and_job_traversal_are_rejected(self):
        manifest = self.freeze_png()
        for name in ("../source.png", "/tmp/source.png", "a\\b.png", ".", ".."):
            altered = copy.deepcopy(manifest)
            altered[0]["filename"] = name
            with self.subTest(filename=name), self.assertRaises(BaseError):
                self.frozen.verify(OP, altered)
        for job in ("../job", "op_" + "0" * 31, "op_" + "G" * 32, OP + "/x", "other"):
            with self.subTest(job=job), self.assertRaises(BaseError):
                self.frozen.verify(job, manifest)

    def test_frozen_symlink_is_rejected_without_reading_target(self):
        manifest = self.freeze_png()
        target = Path(self.frozen.path(OP, manifest[0]))
        target.unlink()
        target.symlink_to(self.source)
        with self.assertRaises(BaseError):
            self.frozen.verify(OP, manifest)
        with self.assertRaises(BaseError):
            self.frozen.read(OP, manifest, 0)

    def test_publish_and_media_directory_symlinks_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        other_state = self.root / "other-state"
        other_state.mkdir()
        (other_state / "publish").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BaseError):
            FrozenMedia(other_state)
        self.assertFalse(any(outside.iterdir()))
        job = self.state / "publish" / OP
        job.mkdir()
        (job / "media").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BaseError):
            self.freeze_png()
        self.assertFalse(any(outside.iterdir()))

    def test_full_and_inclusive_ranges_return_complete_file_hash(self):
        manifest = self.freeze_png()
        full = self.frozen.read(OP, manifest, 0)
        self.assertEqual(full["data"], png())
        self.assertEqual((full["start"], full["end"], full["size"]), (0, len(png()) - 1, len(png())))
        for start, end in ((8, 15), (4, None), (None, 7)):
            part = self.frozen.read(OP, manifest, 0, start, end)
            left, right = (0 if start is None else start), (len(png()) - 1 if end is None else end)
            self.assertEqual(part["data"], png()[left:right + 1])
            self.assertEqual(part["sha256"], hashlib.sha256(png()).hexdigest())
            self.assertEqual(part["mime"], "image/png")

    def test_invalid_ranges_and_indexes_are_rejected(self):
        manifest = self.freeze_png()
        for start, end in ((-1, 2), (2, 1), (0, len(png())), (len(png()), None), (True, 4), (0, "4")):
            with self.subTest(start=start, end=end), self.assertRaisesRegex(BaseError, "media_range_not_satisfiable"):
                self.frozen.read(OP, manifest, 0, start, end)
        for index in (-1, 1, True, "0"):
            with self.subTest(index=index), self.assertRaises(BaseError):
                self.frozen.read(OP, manifest, index)

    def test_range_rechecks_bytes_after_initial_hash(self):
        manifest = self.freeze_png()
        target = Path(self.frozen.path(OP, manifest[0]))
        from contentbase import frozen_media
        original = frozen_media._stream
        passes = 0
        def mutate_after_hash(descriptor, limit, **kwargs):
            nonlocal passes
            result = original(descriptor, limit, **kwargs)
            # First stream is the metadata manifest; second is the media proof.
            passes += 1
            if passes == 2:
                target.chmod(0o600)
                changed = bytearray(target.read_bytes())
                changed[-1] ^= 1
                target.write_bytes(changed)
            return result
        with patch("contentbase.frozen_media._stream", side_effect=mutate_after_hash), self.assertRaises(BaseError):
            self.frozen.read(OP, manifest, 0, 0, 7)
        self.assertGreaterEqual(passes, 3)

    def test_valid_mov_can_be_frozen_and_range_read(self):
        video = self.root / "example.mov"
        video.write_bytes(mov())
        manifest = self.frozen.freeze(OP, [inspect_media(video, "video")])
        result = self.frozen.read(OP, manifest, 0, 4, 11)
        self.assertEqual(result["data"], b"ftypqt  ")
        self.assertEqual(result["mime"], "video/quicktime")

    def test_missing_manifest_and_legacy_info_never_rebuild_sources(self):
        manifest = self.freeze_png()
        target = Path(self.frozen.path(OP, manifest[0]))
        (target.parent / ".manifest.json").unlink()
        with self.assertRaisesRegex(BaseError, "frozen_media_manifest_required"):
            self.frozen.verify(OP, manifest)
        info = inspect_media(self.source, "image")
        info.pop("source_identity")
        with self.assertRaisesRegex(BaseError, "media_source_identity_required"):
            self.frozen.freeze(OTHER, [info])
        self.assertTrue(self.source.exists())

    def test_legacy_empty_manifest_is_explicit_even_without_media_directory(self):
        with self.assertRaisesRegex(BaseError, "frozen_media_manifest_required"):
            self.frozen.verify(OP, None)
        with self.assertRaisesRegex(BaseError, "frozen_media_manifest_required"):
            self.frozen.verify(OP, [])
        self.assertFalse((self.state / "publish" / OP).exists())

    def test_same_job_is_not_overwritten_or_recaptured(self):
        manifest = self.freeze_png()
        self.source.write_bytes(png() + b"new source")
        with self.assertRaisesRegex(BaseError, "frozen_media_already_exists"):
            self.freeze_png()
        self.assertEqual(self.frozen.read(OP, manifest, 0)["data"], png())


if __name__ == "__main__":
    unittest.main()
