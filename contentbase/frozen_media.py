"""Descriptor-bound inspection and private immutable copies; never delete source media."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import struct
from .errors import BaseError

CHUNK = 1024 * 1024
IMAGE_LIMIT = 20 * 1024 * 1024
VIDEO_LIMIT = 512 * 1024 * 1024
JOB = re.compile(r"op_[a-f0-9]{32}")
HASH = re.compile(r"[a-f0-9]{64}")
MANIFEST = ".manifest.json"
ENTRY_KEYS = {"index", "filename", "name", "usage", "size", "sha256", "mime"}


def _flags(directory=False):
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise BaseError("media_platform_unsupported")
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_DIRECTORY if directory else 0)


def _absolute(path):
    try:
        raw = os.fspath(path)
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ValueError
        return Path(os.path.abspath(os.path.expanduser(raw)))
    except (TypeError, ValueError):
        raise BaseError("media_path_invalid") from None


def _identity(value):
    return {"device": value.st_dev, "inode": value.st_ino, "size": value.st_size,
            "mtime_ns": value.st_mtime_ns, "ctime_ns": value.st_ctime_ns}


def _same_entry(parent, name, descriptor):
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        opened = os.fstat(descriptor)
    except OSError:
        raise BaseError("media_path_changed") from None
    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino) or stat.S_ISLNK(current.st_mode):
        raise BaseError("media_path_changed")


@contextmanager
def _source(path):
    path = _absolute(path)
    parent = descriptor = None
    try:
        parent_path = path.parent.resolve(strict=True)
        parent = os.open(parent_path, _flags(True))
        descriptor = os.open(path.name, _flags(), dir_fd=parent)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise BaseError("media_source_not_regular")
        _same_entry(parent, path.name, descriptor)
        # The lexical original path must still refer to the captured parent/leaf.
        if _identity(os.stat(path, follow_symlinks=False)) != _identity(opened):
            raise BaseError("media_source_changed")
        yield descriptor, parent, path
        _same_entry(parent, path.name, descriptor)
        if _identity(os.stat(path, follow_symlinks=False)) != _identity(os.fstat(descriptor)):
            raise BaseError("media_source_changed")
    except (OSError, RuntimeError):
        raise BaseError("media_source_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def _limit(usage):
    if usage not in ("image", "cover", "video"):
        raise BaseError("media_usage_invalid")
    return VIDEO_LIMIT if usage == "video" else IMAGE_LIMIT


def _kind(header, suffix, usage, size):
    if suffix not in ((".mp4", ".mov") if usage == "video" else (".png", ".jpg", ".jpeg", ".webp", ".gif")):
        raise BaseError("media_suffix_invalid")
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        valid = len(header) >= 33 and header[8:16] == b"\x00\x00\x00\rIHDR" and all(struct.unpack(">II", header[16:24]))
        detected, suffixes = "image/png", {".png"}
    elif header.startswith(b"\xff\xd8\xff"):
        valid, detected, suffixes = len(header) >= 4, "image/jpeg", {".jpg", ".jpeg"}
    elif header.startswith((b"GIF87a", b"GIF89a")):
        valid = len(header) >= 13 and all(struct.unpack("<HH", header[6:10]))
        detected, suffixes = "image/gif", {".gif"}
    elif header.startswith(b"RIFF") and len(header) >= 16 and header[8:12] == b"WEBP":
        valid = struct.unpack("<I", header[4:8])[0] + 8 == size and header[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
        detected, suffixes = "image/webp", {".webp"}
    elif len(header) >= 20 and header[4:8] == b"ftyp":
        box_size = struct.unpack(">I", header[:4])[0]
        brand = header[8:12]
        valid = 16 <= box_size <= size and box_size % 4 == 0
        if brand == b"qt  ":
            detected, suffixes = "video/quicktime", {".mov"}
        else:
            valid = valid and (brand.startswith((b"iso", b"mp4", b"avc")) or brand in (b"M4V ", b"MSNV", b"dash", b"cmfc", b"cmfs"))
            detected, suffixes = "video/mp4", {".mp4"}
    else:
        raise BaseError("media_header_invalid")
    if not valid or suffix not in suffixes or (usage == "video") != detected.startswith("video/"):
        raise BaseError("media_header_suffix_mismatch")
    return detected


def _stream(descriptor, limit, *, destination=None, start=None, end=None):
    before = _identity(os.fstat(descriptor))
    if not 0 < before["size"] <= limit:
        raise BaseError("media_size_limit")
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest, count, captured, header = hashlib.sha256(), 0, [], b""
    while True:
        block = os.read(descriptor, CHUNK)
        if not block:
            break
        if count + len(block) > limit:
            raise BaseError("media_size_limit")
        if len(header) < 64:
            header += block[:64 - len(header)]
        digest.update(block)
        if destination is not None:
            view = memoryview(block)
            while view:
                written = os.write(destination, view)
                if written <= 0:
                    raise BaseError("media_copy_failed")
                view = view[written:]
        if start is not None:
            left, right = max(start - count, 0), min(end + 1 - count, len(block))
            if left < right:
                captured.append(block[left:right])
        count += len(block)
    if before != _identity(os.fstat(descriptor)) or count != before["size"]:
        raise BaseError("media_source_changed")
    return {"sha256": digest.hexdigest(), "size": count, "header": header,
            "identity": before, "data": b"".join(captured)}



def _anonymous_snapshot(directory):
    """Create/unlink before writing: only an owned descriptor survives."""
    name = ".snapshot-" + secrets.token_hex(16)
    descriptor = None
    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(name, flags, 0o600, dir_fd=directory)
        os.unlink(name, dir_fd=directory)
        os.fchmod(descriptor, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_nlink != 0:
            raise BaseError("media_snapshot_unavailable")
        return descriptor
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        raise BaseError("media_snapshot_unavailable") from None


def _spool_range(source, target, limit, start, end):
    before = _identity(os.fstat(source))
    if not 0 < before["size"] <= limit:
        raise BaseError("media_size_limit")
    os.lseek(source, 0, os.SEEK_SET)
    complete, selected, total, written_total = hashlib.sha256(), hashlib.sha256(), 0, 0
    while True:
        block = os.read(source, CHUNK)
        if not block:
            break
        if total + len(block) > limit:
            raise BaseError("media_size_limit")
        complete.update(block)
        left, right = max(start - total, 0), min(end + 1 - total, len(block))
        if left < right:
            part = memoryview(block)[left:right]
            selected.update(part)
            written_total += len(part)
            while part:
                count = os.write(target, part)
                if count <= 0:
                    raise BaseError("media_snapshot_write_failed")
                part = part[count:]
        total += len(block)
    if before != _identity(os.fstat(source)) or total != before["size"] or written_total != end - start + 1:
        raise BaseError("media_source_changed")
    os.fsync(target)
    return {"sha256": complete.hexdigest(), "size": total,
            "range_sha256": selected.hexdigest(), "content_length": written_total}

def inspect_media(path, usage):
    limit = _limit(usage)
    with _source(path) as (descriptor, parent, absolute):
        if any(ord(c) < 32 or c == "\\" for c in absolute.name):
            raise BaseError("media_name_invalid")
        try:
            absolute.name.encode("utf-8")
        except UnicodeError:
            raise BaseError("media_name_invalid") from None
        suffix = absolute.suffix.lower()
        result = _stream(descriptor, limit)
        mime = _kind(result["header"], suffix, usage, result["size"])
        return {"path": str(absolute), "name": absolute.name, "usage": usage,
                "sha256": result["sha256"], "size": result["size"], "mime": mime,
                "suffix": suffix, "source_identity": result["identity"]}


@contextmanager
def _directory(path, create=False):
    descriptor = None
    try:
        absolute = _absolute(path)
        # Resolve parent aliases (e.g. OS temporary directories), not this leaf.
        parent = absolute.parent.resolve(strict=not create)
        absolute = parent / absolute.name
        if create:
            absolute.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(absolute, _flags(True))
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise BaseError("frozen_media_directory_invalid")
        yield descriptor, absolute
    except (OSError, RuntimeError):
        raise BaseError("frozen_media_directory_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def _child(parent, name, *, create=False, exclusive=False):
    descriptor = None
    try:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                if exclusive:
                    raise BaseError("frozen_media_already_exists") from None
        descriptor = os.open(name, _flags(True), dir_fd=parent)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise BaseError("frozen_media_directory_invalid")
        os.fchmod(descriptor, 0o700)
        _same_entry(parent, name, descriptor)
        yield descriptor
        _same_entry(parent, name, descriptor)
    except OSError:
        raise BaseError("frozen_media_directory_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _atomic_file(directory, name, data):
    temporary = ".tmp-" + secrets.token_hex(16)
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise BaseError("frozen_media_write_failed")
            view = view[written:]
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except OSError:
        raise BaseError("frozen_media_write_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


class FrozenMedia:
    def __init__(self, state_dir):
        with _directory(state_dir, create=True) as (descriptor, absolute):
            self.state_dir, self.root = absolute, absolute / "publish"
            self.root_identity = (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino)
            with _child(descriptor, "publish", create=True):
                pass

    @staticmethod
    def _job(op_id):
        if not isinstance(op_id, str) or not JOB.fullmatch(op_id):
            raise BaseError("frozen_media_job_invalid")

    @contextmanager
    def _media(self, op_id, *, create=False):
        self._job(op_id)
        with _directory(self.state_dir) as (state_fd, absolute):
            if (os.fstat(state_fd).st_dev, os.fstat(state_fd).st_ino) != self.root_identity:
                raise BaseError("frozen_media_root_changed")
            with _child(state_fd, "publish") as root_fd:
                with _child(root_fd, op_id, create=create) as job_fd:
                    with _child(job_fd, "media", create=create, exclusive=create) as media_fd:
                        yield media_fd

    @staticmethod
    def _validate_manifest(manifest):
        if not isinstance(manifest, list) or not manifest:
            raise BaseError("frozen_media_manifest_required")
        names = set()
        for index, entry in enumerate(manifest):
            if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS or type(entry["index"]) is not int or entry["index"] != index:
                raise BaseError("frozen_media_manifest_invalid")
            name, filename, digest = entry["name"], entry["filename"], entry["sha256"]
            if not isinstance(name, str) or not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\x00")) or any(ord(c) < 32 for c in name):
                raise BaseError("frozen_media_manifest_invalid")
            if not isinstance(filename, str) or Path(filename).name != filename or any(c in filename for c in ("/", "\\", "\x00")) or not isinstance(digest, str) or not HASH.fullmatch(digest):
                raise BaseError("frozen_media_manifest_invalid")
            suffix = Path(filename).suffix.lower()
            if filename != "%04d-%s%s" % (index, digest[:16], suffix) or filename in names:
                raise BaseError("frozen_media_manifest_invalid")
            limit = _limit(entry["usage"])
            if type(entry["size"]) is not int or not 0 < entry["size"] <= limit or not isinstance(entry["mime"], str):
                raise BaseError("frozen_media_manifest_invalid")
            names.add(filename)
        return manifest

    @staticmethod
    def _stored(directory):
        descriptor = None
        try:
            descriptor = os.open(MANIFEST, _flags(), dir_fd=directory)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= CHUNK:
                raise BaseError("frozen_media_manifest_invalid")
            packet = _stream(descriptor, CHUNK, start=0, end=info.st_size - 1)
            value = json.loads(packet["data"].decode("utf-8"))
            return FrozenMedia._validate_manifest(value)
        except (OSError, ValueError, UnicodeError):
            raise BaseError("frozen_media_manifest_required") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @classmethod
    def _bound(cls, directory, manifest):
        cls._validate_manifest(manifest)
        if cls._stored(directory) != manifest:
            raise BaseError("frozen_media_manifest_changed")

    @staticmethod
    @contextmanager
    def _file(directory, entry):
        descriptor = None
        try:
            descriptor = os.open(entry["filename"], _flags(), dir_fd=directory)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise BaseError("frozen_media_not_regular")
            _same_entry(directory, entry["filename"], descriptor)
            result = _stream(descriptor, _limit(entry["usage"]))
            mime = _kind(result["header"], Path(entry["filename"]).suffix.lower(), entry["usage"], result["size"])
            if (result["sha256"], result["size"], mime) != (entry["sha256"], entry["size"], entry["mime"]):
                raise BaseError("frozen_media_integrity_failed")
            yield descriptor
            _same_entry(directory, entry["filename"], descriptor)
        except OSError:
            raise BaseError("frozen_media_file_unavailable") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def freeze(self, op_id, infos):
        if not isinstance(infos, list) or not infos:
            raise BaseError("media_info_required")
        manifest = []
        with self._media(op_id, create=True) as directory:
            for index, info in enumerate(infos):
                if not isinstance(info, dict) or not {"path", "name", "usage", "sha256", "size", "mime", "suffix", "source_identity"} <= info.keys():
                    raise BaseError("media_source_identity_required")
                temporary, output = ".tmp-" + secrets.token_hex(16), None
                try:
                    with _source(info["path"]) as (source, parent, original):
                        if info["source_identity"] != _identity(os.fstat(source)):
                            raise BaseError("media_source_changed")
                        if original.name != info["name"] or original.suffix.lower() != info["suffix"]:
                            raise BaseError("media_source_changed")
                        output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
                        copied = _stream(source, _limit(info["usage"]), destination=output)
                        mime = _kind(copied["header"], info["suffix"], info["usage"], copied["size"])
                        if (copied["sha256"], copied["size"], mime) != (info["sha256"], info["size"], info["mime"]):
                            raise BaseError("media_source_changed")
                        os.fchmod(output, 0o400)
                        os.fsync(output)
                        # Some filesystems keep size/mtime/ctime unchanged for same-tick
                        # writes. Re-read the same captured source fd after copying;
                        # timestamps alone cannot prove the input stayed unchanged.
                        confirmed = _stream(source, _limit(info["usage"]))
                        if (confirmed["sha256"], confirmed["size"], confirmed["identity"]) != (info["sha256"], info["size"], info["source_identity"]):
                            raise BaseError("media_source_changed")
                    filename = "%04d-%s%s" % (index, info["sha256"][:16], info["suffix"])
                    os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                    manifest.append({"index": index, "filename": filename, "name": info["name"], "usage": info["usage"], "size": info["size"], "sha256": info["sha256"], "mime": info["mime"]})
                except OSError:
                    raise BaseError("media_copy_failed") from None
                finally:
                    if output is not None:
                        os.close(output)
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError:
                        pass
            self._validate_manifest(manifest)
            _atomic_file(directory, MANIFEST, json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        self.verify(op_id, manifest)
        return manifest

    def verify(self, op_id, manifest):
        self._job(op_id)
        self._validate_manifest(manifest)
        with self._media(op_id) as directory:
            self._bound(directory, manifest)
            for entry in manifest:
                with self._file(directory, entry):
                    pass
        return True

    def path(self, op_id, entry):
        if not isinstance(entry, dict) or type(entry.get("index")) is not int:
            raise BaseError("frozen_media_manifest_invalid")
        with self._media(op_id) as directory:
            manifest = self._stored(directory)
            index = entry["index"]
            if not 0 <= index < len(manifest) or entry != manifest[index]:
                raise BaseError("frozen_media_manifest_changed")
            with self._file(directory, entry):
                pass
        return str(self.root / op_id / "media" / entry["filename"])

    @contextmanager
    def open_snapshot(self, op_id, manifest, index, start=None, end=None):
        """Yield a verified anonymous range stream; consumers must read bounded chunks."""
        self._job(op_id)
        self._validate_manifest(manifest)
        if type(index) is not int or not 0 <= index < len(manifest):
            raise BaseError("frozen_media_index_invalid")
        entry = manifest[index]
        size = entry["size"]
        start, end = (0 if start is None else start), (size - 1 if end is None else end)
        if type(start) is not int or type(end) is not int or not 0 <= start <= end < size:
            raise BaseError("media_range_not_satisfiable")
        content_length = end - start + 1
        snapshot, stream, consumer_started = None, None, False
        try:
            with self._media(op_id) as directory:
                self._bound(directory, manifest)
                # _file proves the complete asset before the second captured pass.
                with self._file(directory, entry) as source:
                    snapshot = _anonymous_snapshot(directory)
                    spooled = _spool_range(source, snapshot, _limit(entry["usage"]), start, end)
                    if (spooled["sha256"], spooled["size"]) != (entry["sha256"], size):
                        raise BaseError("frozen_media_integrity_failed")
                    checked = _stream(snapshot, content_length)
                    if (checked["sha256"], checked["size"]) != (spooled["range_sha256"], content_length):
                        raise BaseError("media_snapshot_integrity_failed")
                self._bound(directory, manifest)
            # No named path or source descriptor survives into the consumer context.
            os.lseek(snapshot, 0, os.SEEK_SET)
            stream = os.fdopen(snapshot, "rb", buffering=0)
            snapshot = None
            consumer_started = True
            yield {"stream": stream, "mime": entry["mime"], "size": size,
                   "start": start, "end": end, "sha256": entry["sha256"],
                   "content_length": content_length, "range_sha256": spooled["range_sha256"]}
        except OSError:
            if consumer_started:
                raise
            raise BaseError("media_snapshot_unavailable") from None
        finally:
            if stream is not None:
                stream.close()
            if snapshot is not None:
                os.close(snapshot)

    def read(self, op_id, manifest, index, start=None, end=None):
        if type(index) is not int or not isinstance(manifest, list) or not 0 <= index < len(manifest):
            raise BaseError("frozen_media_index_invalid")
        with self._media(op_id) as directory:
            self._bound(directory, manifest)
            entry = manifest[index]
            size = entry["size"]
            start, end = (0 if start is None else start), (size - 1 if end is None else end)
            if type(start) is not int or type(end) is not int or not 0 <= start <= end < size:
                raise BaseError("media_range_not_satisfiable")
            if end - start + 1 > IMAGE_LIMIT:
                raise BaseError("media_bytes_limit_use_stream")
            with self._file(directory, entry) as descriptor:
                # A second streaming hash includes exactly the returned range bytes;
                # metadata-only caching cannot attest the body delivered to the user.
                result = _stream(descriptor, _limit(entry["usage"]), start=start, end=end)
                if result["sha256"] != entry["sha256"] or result["size"] != size:
                    raise BaseError("frozen_media_integrity_failed")
                return {"data": result["data"], "mime": entry["mime"], "size": size,
                        "start": start, "end": end, "sha256": entry["sha256"]}
