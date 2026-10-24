#!/usr/bin/env python3
"""Create a clean, deterministic public source directory and tar.gz archive.

Only exact paths listed in PUBLIC_EXPORT_MANIFEST.json are copied. The source
repository and its Git history are untouched. Run privacy_audit first when
reviewing all public candidate files, including unregistered ones.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile

try:
    from .privacy_audit import PrivacyError, audit, manifest_files, read_source, read_release_checksum
except ImportError:
    from privacy_audit import PrivacyError, audit, manifest_files, read_source, read_release_checksum


class ExportError(ValueError):
    def __init__(self, code: str, findings: list[dict[str, object]] | None = None):
        super().__init__(code)
        self.findings = findings or []


def _archive(staged: Path, destination: Path, names: list[str], expected_hashes: dict[str, str]) -> None:
    def checked_bytes(name):
        try:
            data = read_release_checksum(staged) if name == "MANIFEST.sha256" else read_source(staged, name)
        except PrivacyError:
            raise ExportError("staged_file_unreadable") from None
        if hashlib.sha256(data).hexdigest() != expected_hashes[name]:
            raise ExportError("staged_content_changed")
        return data
    with destination.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name in sorted(names):
                    data = checked_bytes(name)
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(data))
    with tarfile.open(destination, "r:gz") as archive:
        members = archive.getmembers()
        if sorted(member.name for member in members) != sorted(names):
            raise ExportError("archive_members_mismatch")
        for member in members:
            if (not member.isfile() or member.uid or member.gid or member.mtime
                    or member.uname or member.gname):
                raise ExportError("archive_metadata_mismatch")
            extracted = archive.extractfile(member)
            if extracted is None or extracted.read() != checked_bytes(member.name):
                raise ExportError("archive_content_mismatch")


def export(root: Path | str, destination: Path | str,
           denylist: Path | str | None = None) -> dict[str, object]:
    root = Path(root).absolute()
    destination = Path(destination).absolute()
    archive_path = destination.with_name(destination.name + ".tar.gz")
    if not root.is_dir() or root.is_symlink():
        raise ExportError("source_root_invalid")
    try:
        destination.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise ExportError("destination_must_be_outside_source")
    if destination.exists() or destination.is_symlink() or archive_path.exists() or archive_path.is_symlink():
        raise ExportError("destination_already_exists")
    try:
        names = manifest_files(root)
    except PrivacyError as error:
        raise ExportError(str(error)) from None
    findings = audit(root, denylist)
    if findings:
        raise ExportError("privacy_audit_failed", findings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".public-export-", dir=destination.parent) as workspace:
        workspace = Path(workspace)
        staged = workspace / "source"
        staged.mkdir(mode=0o755)
        hashes: list[str] = []
        expected_hashes: dict[str, str] = {}
        for name in names:
            try:
                data = read_source(root, name)
            except PrivacyError as error:
                raise ExportError(str(error)) from None
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
            expected_hashes[name] = hashlib.sha256(data).hexdigest()
            hashes.append(f"{expected_hashes[name]}  {name}\n")
        try:
            if manifest_files(staged) != names:
                raise ExportError("staged_manifest_changed")
        except PrivacyError as error:
            raise ExportError(str(error)) from None
        staged_findings = audit(staged, denylist, files=names)
        if staged_findings:
            raise ExportError("staged_privacy_audit_failed", staged_findings)
        checksum = staged / "MANIFEST.sha256"
        checksum.write_text("".join(hashes), encoding="utf-8")
        checksum.chmod(0o644)
        packed = workspace / "source.tar.gz"
        expected_hashes["MANIFEST.sha256"] = hashlib.sha256(checksum_content(hashes)).hexdigest()
        _archive(staged, packed, names + ["MANIFEST.sha256"], expected_hashes)
        archive_hash = hashlib.sha256(packed.read_bytes()).hexdigest()
        if destination.exists() or archive_path.exists():
            raise ExportError("destination_already_exists")
        os.rename(staged, destination)
        try:
            # link creates the archive exclusively instead of overwriting it.
            os.link(packed, archive_path)
        except OSError:
            shutil.rmtree(destination)
            raise ExportError("archive_write_failed") from None
    return {
        "file_count": len(names), "destination": str(destination),
        "archive_path": str(archive_path), "archive_sha256": archive_hash,
        "manifest_sha256": hashlib.sha256(checksum_content(hashes)).hexdigest(),
    }


def checksum_content(hashes: list[str]) -> bytes:
    return "".join(hashes).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--denylist", type=Path, help="External JSON of private identifiers")
    args = parser.parse_args(argv)
    try:
        result = export(args.root, args.destination, args.denylist)
    except ExportError as error:
        print(json.dumps({"error": str(error), "findings": error.findings}, ensure_ascii=False, indent=2))
        return 1
    except OSError:
        print(json.dumps({"error": "filesystem_error", "findings": []}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
