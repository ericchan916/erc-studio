"""PEP 517 public builds from an audited, exact-manifest source snapshot.

PEP 660 editable wheels are private local-installation artifacts: they refer to
this checkout and must never be distributed as public release wheels. Public
source exports exclude editable wheels, egg-info and other local build state.
"""
from contextlib import contextmanager
import importlib
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import tarfile
import threading
import types
import uuid

_ROOT = Path(__file__).resolve().parent
_HOOK_LOCK = threading.RLock()


class BuildAuditError(RuntimeError):
    """Content-free build errors, with scrubbed privacy findings when present."""


def _release_tools(root):
    # A unique namespace prevents a preinstalled 'scripts' package or a previous
    # build of another checkout from supplying the release-gate implementation.
    namespace = "_contentbase_build_release_" + uuid.uuid4().hex
    package = types.ModuleType(namespace)
    package.__path__ = [str(root / "scripts")]
    sys.modules[namespace] = package
    try:
        privacy = importlib.import_module(namespace + ".privacy_audit")
        exporter = importlib.import_module(namespace + ".export_source")
        return privacy, exporter
    finally:
        for name in list(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                sys.modules.pop(name, None)


def _inspect_source():
    root = Path(_ROOT)
    privacy, exporter = _release_tools(root)
    denylist = os.environ.get("CONTENTBASE_BUILD_DENYLIST") or None
    findings = privacy.audit(root, denylist)
    if findings:
        raise BuildAuditError(json.dumps({"error": "source_privacy_audit_failed", "findings": findings}, ensure_ascii=False))
    return root, exporter, denylist


def _output_path(root, value):
    try:
        path = Path(value).expanduser().absolute()
        if path.resolve().is_relative_to(root.resolve()):
            raise BuildAuditError("build_output_must_be_outside_source")
        return str(path)
    except (TypeError, ValueError):
        raise BuildAuditError("invalid_build_output_path") from None


def _temporary_parent(root):
    for candidate in (tempfile.gettempdir(), "/tmp", "/var/tmp"):
        path = Path(candidate)
        if path.is_dir() and not path.resolve().is_relative_to(root.resolve()):
            return path
    raise BuildAuditError("external_build_temp_required")


@contextmanager
def _working_directory(path):
    previous = Path.cwd()
    arguments = sys.argv[:]
    os.chdir(path)
    try:
        yield
    finally:
        sys.argv = arguments
        os.chdir(previous)


def _setuptools():
    return importlib.import_module("setuptools.build_meta")


def _invoke(hook, config_settings=None, *, editable=False, **directories):
    with _HOOK_LOCK:
        root, exporter, denylist = _inspect_source()
        arguments = {key: _output_path(root, value) if value is not None else None
                     for key, value in directories.items()}
        arguments["config_settings"] = config_settings
        if editable:
            # Keeping the original source path is essential to PEP 660. This is
            # a private installation, not the public artifact-building path.
            with _working_directory(root):
                return getattr(_setuptools(), hook)(**arguments)
        with tempfile.TemporaryDirectory(prefix="contentbase-build-", dir=_temporary_parent(root)) as temporary:
            clean = Path(temporary) / "source"
            try:
                exporter.export(root, clean, denylist)
            except exporter.ExportError as error:
                raise BuildAuditError(json.dumps({"error": str(error), "findings": error.findings}, ensure_ascii=False)) from None
            with _working_directory(clean):
                return getattr(_setuptools(), hook)(**arguments)


def _neutral_sdist(path):
    """Keep every member byte, but never distribute the builder's tar identity."""
    path = Path(path)
    descriptor = None
    temporary = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        original_stat = os.fstat(descriptor)
        if not stat.S_ISREG(original_stat.st_mode):
            raise BuildAuditError("sdist_not_regular")
        expected, seen = {}, set()
        output_fd, temporary = tempfile.mkstemp(prefix=".sdist-neutral-", dir=path.parent)
        with os.fdopen(descriptor, "rb") as original:
            descriptor = None
            with os.fdopen(output_fd, "wb") as output, tarfile.open(fileobj=original, mode="r:gz") as incoming:
                with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
                    with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as outgoing:
                        for member in incoming:
                            name = PurePosixPath(member.name)
                            if (name.is_absolute() or ".." in name.parts or member.name in seen
                                    or not name.parts or not (member.isdir() or member.isfile())):
                                raise BuildAuditError("sdist_member_invalid")
                            seen.add(member.name)
                            clean = tarfile.TarInfo(member.name)
                            clean.type = tarfile.DIRTYPE if member.isdir() else tarfile.REGTYPE
                            clean.mode = 0o755 if member.isdir() else 0o644
                            clean.uid = clean.gid = clean.mtime = 0
                            clean.uname = clean.gname = ""
                            clean.size = member.size if member.isfile() else 0
                            if member.isfile():
                                stream = incoming.extractfile(member)
                                if stream is None:
                                    raise BuildAuditError("sdist_member_unreadable")
                                data = stream.read()
                                if len(data) != member.size:
                                    raise BuildAuditError("sdist_member_changed")
                                expected[member.name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "directory": False}
                                # Source files are already exact-allowlisted and small.
                                import io
                                outgoing.addfile(clean, io.BytesIO(data))
                            else:
                                expected[member.name] = {"directory": True}
                                outgoing.addfile(clean)
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(original.fileno())
            if (original_stat.st_dev, original_stat.st_ino, original_stat.st_size, original_stat.st_mtime_ns, original_stat.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise BuildAuditError("sdist_changed_during_audit")
        with tarfile.open(temporary, "r:gz") as reopened:
            if {m.name for m in reopened.getmembers()} != set(expected):
                raise BuildAuditError("sdist_members_changed")
            for member in reopened.getmembers():
                allowed_pax = {"path"} if len(member.name.encode("utf-8")) > 100 else set()
                if (member.uid or member.gid or member.mtime or member.uname or member.gname
                        or member.linkname or set(member.pax_headers) - allowed_pax
                        or member.mode != (0o755 if member.isdir() else 0o644)):
                    raise BuildAuditError("sdist_non_neutral_metadata")
                wanted = expected[member.name]
                if member.isdir() != wanted["directory"]:
                    raise BuildAuditError("sdist_member_changed")
                if member.isfile():
                    data = reopened.extractfile(member).read()
                    if len(data) != wanted["size"] or hashlib.sha256(data).hexdigest() != wanted["sha256"]:
                        raise BuildAuditError("sdist_member_changed")
        current = os.stat(path, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (original_stat.st_dev, original_stat.st_ino, original_stat.st_size, original_stat.st_mtime_ns, original_stat.st_ctime_ns) or not stat.S_ISREG(current.st_mode):
            raise BuildAuditError("sdist_path_changed")
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        temporary = None
    except (OSError, tarfile.TarError, EOFError):
        raise BuildAuditError("sdist_metadata_audit_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            os.unlink(temporary)


def get_requires_for_build_wheel(config_settings=None):
    return _invoke("get_requires_for_build_wheel", config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return _invoke("get_requires_for_build_sdist", config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _invoke("prepare_metadata_for_build_wheel", config_settings, metadata_directory=metadata_directory)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _invoke("build_wheel", config_settings, wheel_directory=wheel_directory, metadata_directory=metadata_directory)


def build_sdist(sdist_directory, config_settings=None):
    name = _invoke("build_sdist", config_settings, sdist_directory=sdist_directory)
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".tar.gz"):
        raise BuildAuditError("sdist_filename_invalid")
    _neutral_sdist(Path(sdist_directory).expanduser().absolute() / name)
    return name


def get_requires_for_build_editable(config_settings=None):
    return _invoke("get_requires_for_build_editable", config_settings, editable=True)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return _invoke("prepare_metadata_for_build_editable", config_settings, editable=True, metadata_directory=metadata_directory)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _invoke("build_editable", config_settings, editable=True, wheel_directory=wheel_directory, metadata_directory=metadata_directory)
