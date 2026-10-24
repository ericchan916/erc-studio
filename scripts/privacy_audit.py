#!/usr/bin/env python3
"""Fail-closed privacy checks for the explicit public source manifest.

Private identifiers belong in an external JSON array (or {"values": [...]})
passed with --denylist. Findings never include matched content.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Iterable

MANIFEST_NAME = "PUBLIC_EXPORT_MANIFEST.json"
TOP_LEVEL = frozenset({
    ".gitignore", "pyproject.toml", "config.example.json", "README.md",
    "AGENTS.md", "LICENSE", "MANIFEST.in", "build_backend.py", MANIFEST_NAME,
})
IGNORED_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__",
    "node_modules", "private", "runtime", "build", "dist", ".tox",
})
MAX_SOURCE_BYTES = 10 * 1024 * 1024
PUBLIC_FEISHU_HOSTS = frozenset({
    "open", "www", "docs", "help", "support", "developer", "developers",
    "applink", "passport", "accounts", "static", "login",
})
HOME_PATH = re.compile(r"/" + r"Users/([^/\s\"'<>`]+)(?:/|\b)|[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s\"'<>`]+)")
FEISHU_ID = re.compile(r"(?<![A-Za-z0-9_])(?:ou_|oc_|om_|cli_)[A-Za-z0-9]{12,}(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])(?:rec|tbl|fld)[A-Za-z0-9]{12,}(?![A-Za-z0-9_])")
TENANT_HOST = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9-]*)\.(?:feishu\.cn|larksuite\.com|larkoffice\.com)\b", re.I)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")
API_SECRET = re.compile(r"(?<![A-Za-z0-9])(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AKID[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{24,}|xox[baprs]-[A-Za-z0-9-]{18,}|AIza[A-Za-z0-9_-]{30,})(?![A-Za-z0-9])")
BEARER_SECRET = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=\-]{24,})", re.I)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9_]*_)?(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|app[_-]?secret|password|passwd)"
    r"\s*[\"']?\s*[:=]\s*[\"']([^\"'\r\n]+)[\"']", re.I,
)
BINARY_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PrivacyError(ValueError):
    """A stable, content-free validation error code."""


def allowed_path(value: str) -> bool:
    """Only documented source roles may be included, never runtime files."""
    if not isinstance(value, str) or not value or "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in IGNORED_DIRECTORIES for part in parts):
        return False
    if len(parts) == 1:
        return value in TOP_LEVEL
    if parts[0] == "workbench":
        return (path.suffix in {".py", ".cjs", ".mjs", ".js", ".json", ".html", ".css", ".svg", ".md"}
                or path.name == "LICENSE" or path.name.endswith(".LICENSE"))
    if parts[0] == ".github":
        return value == ".github/pull_request_template.md"
    if parts[0] == "agent-runtime":
        return value == "agent-runtime/workflow-registry.json"
    if parts[0] == "contentbase":
        return path.suffix == ".py" or (
            len(parts) == 3 and parts[1] == "web" and path.suffix in {".html", ".js", ".css"}
        )
    if parts[0] == "docs":
        return path.suffix == ".md"
    if parts[0] in {"tests", "scripts"} and len(parts) == 2:
        return path.suffix == (".md" if parts[0] == "docs" else ".py")
    return value == "skills/content-company-base/SKILL.md"


def manifest_files(root: Path | str) -> list[str]:
    root = Path(root)
    try:
        payload = json.loads(read_source(root, MANIFEST_NAME).decode("utf-8"))
    except PrivacyError as error:
        code = {"source_missing": "manifest_missing", "symlink": "manifest_symlink"}.get(str(error), "manifest_unreadable")
        raise PrivacyError(code) from None
    except (UnicodeError, json.JSONDecodeError):
        raise PrivacyError("manifest_unreadable") from None
    if (not isinstance(payload, dict) or type(payload.get("version")) is not int
            or payload["version"] != 1 or not isinstance(payload.get("files"), list)):
        raise PrivacyError("manifest_invalid_schema")
    files = payload["files"]
    if any(not allowed_path(value) for value in files):
        raise PrivacyError("manifest_disallowed_path")
    if len(files) != len(set(files)):
        raise PrivacyError("manifest_duplicate_path")
    if not files:
        raise PrivacyError("manifest_empty")
    if MANIFEST_NAME not in files:
        raise PrivacyError("manifest_self_required")
    return sorted(files)


def _deny_values(root: Path, denylist: Path | str | None) -> list[str]:
    if denylist is None:
        return []
    path = Path(denylist)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise PrivacyError("denylist_must_be_external")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PrivacyError("denylist_unreadable") from None
    values = payload.get("values") if isinstance(payload, dict) else payload
    if (not isinstance(values, list)
            or any(not isinstance(value, str) or not value for value in values)):
        raise PrivacyError("denylist_invalid_schema")
    return sorted(set(values), key=lambda value: (-len(value), value))


def _placeholder(value: str, kind: str = "generic") -> bool:
    """Complete typed slots only; a substring never exempts a credential."""
    upper, lower = value.upper(), value.lower()
    if re.fullmatch(r"(?:YOUR_[A-Z0-9_]+|YOUR-[A-Z0-9-]+|SK-YOUR_[A-Z0-9_]+)", upper):
        return True
    if upper in {"TENANT", "USER", "USERNAME", "TOKEN", "PASSWORD", "API_KEY", "HOST", "TARGET", "ACCOUNT", "SAMPLE"}:
        return True
    if lower in {"example", "example-user", "example_user", "example-token", "example_token", "changeme", "change_me", "..."}:
        return True
    if re.fullmatch(r"(?:\$\{[A-Z_][A-Z0-9_]*\}|<[A-Z_][A-Z0-9_]*>|\{\{\s*[A-Z_][A-Z0-9_]*\s*\}\})", upper):
        return True
    if kind == "identifier":
        # Exact public ECharts renderer class, not a Feishu record identifier.
        # Keep upstream bytes intact; this does not exempt arbitrary rec* values.
        if value == "rectCoordBuildBreakAxis":
            return True
        return bool(re.fullmatch(r"(?:ou_|oc_|om_|cli_|rec|tbl|fld)example[A-Za-z0-9_-]*", lower))
    return False


def _matches(text: str, values: list[str]):
    for value in values:
        offset = text.find(value)
        while offset >= 0:
            yield offset, "private_denylist"
            offset = text.find(value, offset + 1)
    for match in HOME_PATH.finditer(text):
        if not _placeholder(match.group(1) or match.group(2), "user"):
            yield match.start(), "private_home_path"
    for match in FEISHU_ID.finditer(text):
        if not _placeholder(match.group(), "identifier"):
            yield match.start(), "private_feishu_identifier"
    for match in TENANT_HOST.finditer(text):
        if match.group(1).lower() not in PUBLIC_FEISHU_HOSTS and not _placeholder(match.group(1), "host"):
            yield match.start(), "private_feishu_tenant"
    for match in PRIVATE_KEY.finditer(text):
        yield match.start(), "private_key"
    for match in API_SECRET.finditer(text):
        if not _placeholder(match.group(), "credential"):
            yield match.start(), "api_secret"
    for match in BEARER_SECRET.finditer(text):
        if not _placeholder(match.group(1), "credential"):
            yield match.start(), "bearer_secret"
    for match in CREDENTIAL_ASSIGNMENT.finditer(text):
        if not _placeholder(match.group(1), "credential"):
            yield match.start(), "hardcoded_credential"


def _rules(text: str, values: list[str]) -> set[str]:
    return {rule for _, rule in _matches(text, values)}


def _safe_file(relative: str, values: list[str]) -> str:
    if _rules(relative, values):
        digest = hashlib.sha256(relative.encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"<private-path:{digest}>"
    return relative


def _finding(relative: str, line: int, rule: str, values: list[str]) -> dict[str, object]:
    return {"file": _safe_file(relative, values), "line": line, "rule": rule}


def _candidate_files(root: Path) -> tuple[set[str], list[str]]:
    files = {name for name in TOP_LEVEL if (root / name).exists() or (root / name).is_symlink()}
    links: list[str] = []
    for prefix in ("contentbase", "docs", "tests", "scripts", "skills/content-company-base", "workbench", ".github", "agent-runtime"):
        directory = root / prefix
        if directory.is_symlink():
            links.append(prefix)
            continue
        if not directory.is_dir():
            continue
        for current, directories, names in os.walk(directory, followlinks=False):
            for name in list(directories):
                child = Path(current) / name
                if name in IGNORED_DIRECTORIES:
                    directories.remove(name)
                elif child.is_symlink():
                    links.append(child.relative_to(root).as_posix())
                    directories.remove(name)
            for name in names:
                relative = (Path(current) / name).relative_to(root).as_posix()
                if allowed_path(relative):
                    files.add(relative)
    return files, links


def _read_regular(root: Path, relative: str) -> bytes:
    """Anchor every in-tree component to an open directory descriptor."""
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts) or "\\" in relative or "\x00" in relative:
        raise PrivacyError("disallowed_source_path")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise PrivacyError("secure_source_read_unavailable")
    try:
        with ExitStack() as stack:
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, directory)
            for component in parts[:-1]:
                directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                stack.callback(os.close, directory)
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
            stack.callback(os.close, descriptor)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise PrivacyError("nonregular_source")
            if info.st_size > MAX_SOURCE_BYTES:
                raise PrivacyError("oversized_source")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                data = stream.read(MAX_SOURCE_BYTES + 1)
            if len(data) > MAX_SOURCE_BYTES:
                raise PrivacyError("oversized_source")
            after = os.fstat(descriptor)
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise PrivacyError("source_changed_during_read")
            return data
    except FileNotFoundError:
        raise PrivacyError("source_missing") from None
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise PrivacyError("symlink") from None
        raise PrivacyError("source_unreadable") from None


def read_source(root: Path, relative: str) -> bytes:
    if not allowed_path(relative):
        raise PrivacyError("disallowed_source_path")
    return _read_regular(Path(root), relative)


def read_release_checksum(root: Path | str) -> bytes:
    return _read_regular(Path(root), "MANIFEST.sha256")


def audit(root: Path | str, denylist: Path | str | None = None,
          files: Iterable[str] | None = None) -> list[dict[str, object]]:
    """Audit manifest plus unregistered public candidates, or an explicit set.

    Findings contain only a relative/scrubbed file name, line number and rule.
    Passing files audits exactly that set (useful for a staged export).
    """
    root = Path(root)
    try:
        values = _deny_values(root, denylist)
    except PrivacyError as error:
        return [{"file": "<denylist>", "line": 0, "rule": str(error)}]
    findings: list[dict[str, object]] = []
    if files is None:
        try:
            selected = set(manifest_files(root))
        except PrivacyError as error:
            findings.append({"file": "<manifest>", "line": 0, "rule": str(error)})
            selected = set()
        candidates, links = _candidate_files(root)
        for relative in sorted(candidates - selected):
            findings.append(_finding(relative, 0, "unregistered_source", values))
        selected.update(candidates)
        for relative in links:
            findings.append(_finding(relative, 0, "symlink", values))
    else:
        selected = set(files)
    for relative in sorted(selected, key=str):
        if not allowed_path(relative):
            findings.append({"file": "<manifest-entry>", "line": 0, "rule": "disallowed_source_path"})
            continue
        for rule in sorted(_rules(relative, values)):
            findings.append(_finding(relative, 0, rule, values))
        try:
            data = read_source(root, relative)
        except PrivacyError as error:
            findings.append(_finding(relative, 0, str(error), values))
            continue
        try:
            content = data.decode("utf-8")
        except UnicodeError:
            findings.append(_finding(relative, 0, "binary_source", values))
            continue
        if BINARY_CONTROL.search(content):
            findings.append(_finding(relative, 0, "binary_source", values))
            continue
        for offset, rule in _matches(content, values):
            findings.append(_finding(relative, content.count("\n", 0, offset) + 1, rule, values))
    unique = {(item["file"], item["line"], item["rule"]): item for item in findings}
    return [unique[key] for key in sorted(unique)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--denylist", type=Path, help="External private JSON; never put it in the source repository")
    args = parser.parse_args(argv)
    findings = audit(args.root, args.denylist)
    print(json.dumps({"passed": not findings, "findings": findings}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
