"""Private, offline review packages retaining the exact frozen media bytes.

No approval or vendor operation is performed. Bytes readers remain compatible;
context-managed readers supply a binary stream plus attested full-file metadata.
Streams are copied, hashed and verified in bounded chunks without re-encoding
or accumulating an entire asset. A legacy bytes reader still owns its buffer.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import ctypes
from datetime import datetime, timezone
import errno
import hashlib
import html
import json
import math
import os
from pathlib import Path
import stat
import sys
import uuid

from .errors import BaseError

MAX_ASSET_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MIME_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "video/mp4": ".mp4", "video/quicktime": ".mov"}
SOURCE_ROOT = Path(__file__).resolve().parents[1]
CSP = "default-src 'none'; img-src 'self' file:; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'none'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'"


def _text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise BaseError("review_metadata_invalid")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise BaseError("review_metadata_invalid") from None
    return value


def _hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise BaseError("review_digest_invalid")
    return value


def _normalize(review):
    if not isinstance(review, dict):
        raise BaseError("review_metadata_invalid")
    result = {"review_schema": 1, "approved": False, "published": False}
    for field, maximum in {"operation_id": 256, "title": 500, "body": 200000, "platform": 100, "account_id": 256}.items():
        result[field] = _text(review.get(field), maximum)
    if not result["operation_id"] or not result["account_id"] or not result["platform"]:
        raise BaseError("review_metadata_invalid")
    for field in ("confirmation_digest", "media_review_digest"):
        result[field] = _hash(review.get(field))
    result["kind"], result["channel"] = review.get("kind"), review.get("channel")
    if result["kind"] not in {"imageText", "video"} or result["channel"] not in {"cloud", "local"}:
        raise BaseError("review_metadata_invalid")
    expiry = review.get("expires_at")
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0:
        raise BaseError("review_metadata_invalid")
    try:
        datetime.fromtimestamp(expiry, timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise BaseError("review_metadata_invalid") from None
    result["expires_at"] = expiry
    media = review.get("media")
    if not isinstance(media, list) or not 1 <= len(media) <= 21:
        raise BaseError("review_media_required")
    result["media"] = []
    indices = set()
    for position, original in enumerate(media):
        if not isinstance(original, dict):
            raise BaseError("review_media_metadata_invalid")
        index, size = original.get("index"), original.get("size")
        if type(index) is not int or not 0 <= index < 21 or index in indices:
            raise BaseError("review_media_index_invalid")
        if type(size) is not int or not 0 < size <= MAX_ASSET_BYTES:
            raise BaseError("review_media_size_invalid")
        indices.add(index)
        name = _text(original.get("name"), 255)
        if not name or name in {".", ".."} or "/" in name or "\\" in name or any(ord(c) < 32 for c in name):
            raise BaseError("review_media_name_invalid")
        usage, mime = original.get("usage"), original.get("mime")
        if usage not in {"image", "cover", "video"} or mime not in MIME_EXTENSIONS:
            raise BaseError("review_media_type_invalid")
        if (usage == "video") != mime.startswith("video/"):
            raise BaseError("review_media_type_invalid")
        result["media"].append({"index": index, "name": name, "usage": usage, "size": size,
                                "sha256": _hash(original.get("sha256")), "mime": mime,
                                "file": f"media/{position:02d}-{usage}{MIME_EXTENSIONS[mime]}"})
    usages = [item["usage"] for item in result["media"]]
    if result["kind"] == "imageText" and any(usage != "image" for usage in usages):
        raise BaseError("review_media_type_invalid")
    if result["kind"] == "video" and sorted(usages) != ["cover", "video"]:
        raise BaseError("review_video_cover_required")
    return result


def _mime(data):
    header = data[:32]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/quicktime" if header[8:12] == b"qt  " else "video/mp4"
    raise BaseError("review_media_type_invalid")


def _html(review):
    escape = lambda value: html.escape(str(value), quote=True)
    attributes = [("操作", review["operation_id"]), ("账号", review["account_id"]), ("平台", review["platform"]),
                  ("通道", review["channel"]), ("类型", review["kind"]),
                  ("冻结确认摘要", review["confirmation_digest"]), ("素材审核摘要", review["media_review_digest"]),
                  ("过期时间 UTC", datetime.fromtimestamp(review["expires_at"], timezone.utc).isoformat()),
                  ("过期时间 epoch", review["expires_at"])]
    rows = "".join(f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in attributes)
    figures = []
    for position, item in enumerate(review["media"]):
        tag = (f'<video controls preload="metadata" src="{item["file"]}"></video>' if item["usage"] == "video"
               else f'<img src="{item["file"]}" alt="{escape(item["name"])}">')
        figures.append(f'<figure><figcaption>素材 {position + 1} · 冻结索引 {item["index"]} · {escape(item["usage"])} · {escape(item["name"])}<br>MIME {escape(item["mime"])} · {item["size"]} bytes<br>源 SHA-256 <code>{item["sha256"]}</code></figcaption>{tag}</figure>')
    return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\">"
            f'<meta http-equiv="Content-Security-Policy" content="{escape(CSP)}">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>冻结素材审核包</title>'
            '<style>body{max-width:1000px;margin:32px auto;padding:0 20px;font-family:system-ui,sans-serif;color:#111;background:#fff}h1,pre,dd,figcaption{overflow-wrap:anywhere}pre{white-space:pre-wrap}dd{margin:0 0 12px}dt{font-weight:700}figure{margin:20px 0;padding:12px;border:1px solid #bbb;box-shadow:1px 1px 1px #ddd}img,video{display:block;max-width:100%;max-height:80vh;margin:12px auto}code{word-break:break-all}.notice{border:2px solid #222;padding:12px}</style></head><body>'
            '<p class="notice">仅审核：本包不代表已批准或已发布，不包含自动投递、确认按钮或网络调用。正文及素材保持本次冻结版本；正式确认仍须回到本机底座，由本人完成。</p>'
            f'<h1>{escape(review["title"])}</h1><dl>{rows}</dl><h2>完整正文</h2><pre>{escape(review["body"])}</pre>'
            '<h2>冻结素材（原始顺序、原始字节）</h2>' + "".join(figures) + '</body></html>').encode("utf-8")


def _write(directory, name, data, mode):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        view = memoryview(data)
        for offset in range(0, len(view), CHUNK_BYTES):
            chunk = view[offset:offset + CHUNK_BYTES]
            while chunk:
                written = os.write(fd, chunk)
                if written <= 0:
                    raise BaseError("review_package_write_failed")
                chunk = chunk[written:]
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)


class _BytesReader:
    """Compatibility adapter with no second full-asset buffer."""
    def __init__(self, data):
        self.data, self.offset = memoryview(data), 0

    def read(self, amount):
        result = self.data[self.offset:self.offset + amount]
        self.offset += len(result)
        return result


def _copy_packet(directory, item, packet):
    if not isinstance(packet, dict):
        raise BaseError("review_media_unavailable")
    if (type(packet.get("size")) is not int or packet["size"] != item["size"]
            or packet.get("sha256") != item["sha256"] or packet.get("mime") != item["mime"]):
        raise BaseError("review_media_changed")
    streamed = "stream" in packet
    if streamed:
        if "data" in packet or not callable(getattr(packet["stream"], "read", None)):
            raise BaseError("review_media_unavailable")
        for key, expected in (("content_length", item["size"]), ("start", 0), ("end", item["size"] - 1)):
            if type(packet.get(key)) is not int or packet[key] != expected:
                raise BaseError("review_media_changed")
        source = packet["stream"]
    else:
        if not isinstance(packet.get("data"), bytes):
            raise BaseError("review_media_unavailable")
        if len(packet["data"]) != item["size"]:
            raise BaseError("review_media_changed")
        source = _BytesReader(packet["data"])
    if "range_sha256" in packet and packet["range_sha256"] != item["sha256"]:
        raise BaseError("review_media_changed")
    name = item["file"].split("/", 1)[1]
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        digest, total, header = hashlib.sha256(), 0, b""
        while True:
            block = source.read(CHUNK_BYTES)
            if not isinstance(block, (bytes, bytearray, memoryview)) or len(block) > CHUNK_BYTES:
                raise BaseError("review_media_stream_invalid")
            if not block:
                break
            if total + len(block) > item["size"]:
                raise BaseError("review_media_changed")
            digest.update(block)
            if len(header) < 64:
                header += bytes(block[:64 - len(header)])
            view = memoryview(block)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise BaseError("review_package_write_failed")
                view = view[written:]
            total += len(block)
        if total != item["size"] or digest.hexdigest() != item["sha256"] or _mime(header) != item["mime"]:
            raise BaseError("review_media_changed")
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_media(directory, item, media_reader):
    complete = False
    try:
        resource = media_reader(item["index"])
        if isinstance(resource, dict) and "stream" in resource:
            raise BaseError("review_media_context_required")
        manager = nullcontext(resource) if isinstance(resource, dict) else resource
        if not callable(getattr(manager, "__enter__", None)) or not callable(getattr(manager, "__exit__", None)):
            raise BaseError("review_media_unavailable")
        with manager as packet:
            _copy_packet(directory, item, packet)
            complete = True
    except BaseError:
        raise
    except Exception:
        raise BaseError("review_media_unavailable") from None
    if not complete:
        # A reader context must not suppress an integrity failure and turn it
        # into a successful export with a partial asset.
        raise BaseError("review_media_unavailable")


def _verify_file(directory, name, digest, size, mode):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size or stat.S_IMODE(info.st_mode) != mode:
            raise BaseError("review_package_verification_failed")
        hashed = hashlib.sha256()
        while chunk := os.read(fd, CHUNK_BYTES):
            hashed.update(chunk)
        after = os.fstat(fd)
        if (hashed.hexdigest() != digest
                or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise BaseError("review_package_verification_failed")
    finally:
        os.close(fd)


def _rename_exclusive(parent, source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    function, flag = ((getattr(libc, "renameatx_np", None), 4) if sys.platform == "darwin"
                      else (getattr(libc, "renameat2", None), 1) if sys.platform.startswith("linux") else (None, 0))
    if function is None:
        raise BaseError("review_atomic_delivery_unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent, os.fsencode(source), parent, os.fsencode(destination), flag) != 0:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise BaseError("review_destination_exists")
        raise BaseError("review_atomic_delivery_failed")


def _entries(directory):
    """Enumerate an independent open-file description of this anchored inode.

    Reusing a directory FD opened before relative file creation can expose a
    stale enumeration cursor on Linux. openat('.') avoids sharing that cursor
    without resolving any untrusted pathname or accepting a partial listing.
    """
    fresh = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        original, current = os.fstat(directory), os.fstat(fresh)
        if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
            raise BaseError("review_package_verification_failed")
        return os.listdir(fresh)
    finally:
        os.close(fresh)


def _clear_directory(directory):
    for name in _entries(directory):
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            nested = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                _clear_directory(nested)
            finally:
                os.close(nested)
            os.rmdir(name, dir_fd=directory)
        else:
            os.unlink(name, dir_fd=directory)


def _destination_path(value):
    destination = Path(value).expanduser().absolute()
    if not destination.name or ".." in destination.parts or any(ord(c) < 32 for c in str(destination)):
        raise BaseError("review_destination_invalid")
    if destination.resolve().is_relative_to(SOURCE_ROOT.resolve()):
        raise BaseError("review_destination_must_be_outside_source")
    return destination


def _directory(path, stack):
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stack.callback(os.close, directory)
    for part in path.parts[1:]:
        directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        stack.callback(os.close, directory)
    return directory


def _result(destination, manifest):
    return {"directory": str(destination), "index_path": str(destination / "index.html"),
            "manifest_path": str(destination / "review.json"), "manifest_digest": hashlib.sha256(manifest).hexdigest()}


def _serialized_review(frozen):
    return (json.dumps(frozen, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def verify_review_package(review, destination):
    """Reopen and verify the exact expected private package without rewriting it."""
    frozen = _normalize(review)
    destination = _destination_path(destination)
    manifest, page = _serialized_review(frozen), _html(frozen)
    expected = {item["file"]: (item["sha256"], item["size"], 0o400) for item in frozen["media"]}
    for name, data in (("review.json", manifest), ("index.html", page)):
        expected[name] = (hashlib.sha256(data).hexdigest(), len(data), 0o600)
    checksum = "".join(f"{expected[name][0]}  {name}\n" for name in sorted(expected)).encode("ascii")
    expected["SHA256SUMS"] = (hashlib.sha256(checksum).hexdigest(), len(checksum), 0o600)
    with ExitStack() as stack:
        try:
            directory = _directory(destination, stack)
            media = os.open("media", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, media)
            if stat.S_IMODE(os.fstat(directory).st_mode) != 0o700 or stat.S_IMODE(os.fstat(media).st_mode) != 0o700:
                raise BaseError("review_package_verification_failed")
            if set(_entries(directory)) != {"media", "review.json", "index.html", "SHA256SUMS"} or set(_entries(media)) != {item["file"].split("/")[1] for item in frozen["media"]}:
                raise BaseError("review_package_verification_failed")
            for name, parameters in expected.items():
                _verify_file(media if name.startswith("media/") else directory, name.split("/", 1)[-1], *parameters)
            return _result(destination, manifest)
        except OSError:
            raise BaseError("review_package_verification_failed") from None


def export_review(review, media_reader, destination):
    """Export and reopen-verify a new private package; never approve or publish."""
    frozen = _normalize(review)
    destination = _destination_path(destination)
    staged_name = ".review-stage-" + uuid.uuid4().hex
    delivered = False
    stage_created = False
    stage = None
    with ExitStack() as stack:
        try:
            parent = _directory(destination.parent, stack)
            try:
                os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BaseError("review_destination_exists")
            os.mkdir(staged_name, 0o700, dir_fd=parent)
            stage_created = True
            stage = os.open(staged_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, stage)
            os.fchmod(stage, 0o700)
            os.mkdir("media", 0o700, dir_fd=stage)
            assets = os.open("media", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage)
            stack.callback(os.close, assets)
        except OSError:
            if stage_created:
                if stage is not None:
                    _clear_directory(stage)
                os.rmdir(staged_name, dir_fd=parent)
            raise BaseError("review_destination_parent_invalid") from None
        try:
            expected = {}
            for item in frozen["media"]:
                _copy_media(assets, item, media_reader)
                expected[item["file"]] = (item["sha256"], item["size"], 0o400)
            manifest = _serialized_review(frozen)
            page = _html(frozen)
            for name, data in (("review.json", manifest), ("index.html", page)):
                _write(stage, name, data, 0o600)
                expected[name] = (hashlib.sha256(data).hexdigest(), len(data), 0o600)
            checksum = "".join(f"{expected[name][0]}  {name}\n" for name in sorted(expected)).encode("ascii")
            _write(stage, "SHA256SUMS", checksum, 0o600)
            expected["SHA256SUMS"] = (hashlib.sha256(checksum).hexdigest(), len(checksum), 0o600)
            for name, parameters in expected.items():
                _verify_file(assets if name.startswith("media/") else stage, name.split("/", 1)[-1], *parameters)
            if set(_entries(stage)) != {"media", "review.json", "index.html", "SHA256SUMS"} or set(_entries(assets)) != {item["file"].split("/")[1] for item in frozen["media"]}:
                raise BaseError("review_package_verification_failed")
            os.fsync(assets)
            os.fsync(stage)
            _rename_exclusive(parent, staged_name, destination.name)
            delivered = True
            os.fsync(parent)
            return verify_review_package(frozen, destination)
        except OSError:
            raise BaseError("review_package_io_failed") from None
        finally:
            if not delivered:
                _clear_directory(stage)
                os.rmdir(staged_name, dir_fd=parent)
