#!/usr/bin/env python3
"""Archive a verified source-only checkout without relocating private state.

This is a rollback to the pre-creation state, not an external transaction undo.
Use --rehearse to verify archive/restore on an isolated copy first.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

try:
    from .privacy_audit import PrivacyError, manifest_files, read_release_checksum, read_source
except ImportError:
    from privacy_audit import PrivacyError, manifest_files, read_release_checksum, read_source

DEVELOPMENT_DIRECTORIES = {".git", "__pycache__", ".venv", "venv", "node_modules", "build", "dist", ".tox"}


def _verify_tree(root, expected):
    """Reject unregistered/private data instead of moving its active location."""
    for current, directories, names in os.walk(root, followlinks=False):
        for name in list(directories):
            path = Path(current) / name
            if path.is_symlink():
                raise ValueError("rollback_symlink_rejected")
            if name in DEVELOPMENT_DIRECTORIES or name.endswith(".egg-info"):
                directories.remove(name)
        for name in names:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ValueError("rollback_symlink_rejected")
            if relative not in expected and relative not in {"MANIFEST.sha256", ".git", ".DS_Store"}:
                raise ValueError("unregistered_or_private_data_present")


def verify(root):
    root = Path(root).absolute()
    try:
        expected_names = manifest_files(root)
        checksum = read_release_checksum(root).decode("utf-8")
    except (PrivacyError, UnicodeError):
        raise ValueError("trusted_export_manifest_required") from None
    lines = checksum.splitlines()
    if not lines:
        raise ValueError("nonempty_export_checksum_required")
    hashes = {}
    for line in lines:
        match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
        if not match:
            raise ValueError("invalid_checksum_entry")
        expected, name = match.groups()
        if name in hashes or name not in expected_names:
            raise ValueError("invalid_checksum_path")
        hashes[name] = expected
    if set(hashes) != set(expected_names):
        raise ValueError("incomplete_export_checksum")
    for name in expected_names:
        try:
            actual = hashlib.sha256(read_source(root, name)).hexdigest()
        except PrivacyError:
            raise ValueError("invalid_checksum_path") from None
        if actual != hashes[name]:
            raise ValueError("source_changed")
    _verify_tree(root, set(expected_names))


def archive(root, destination):
    root, destination = Path(root).absolute(), Path(destination).absolute()
    verify(root)
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(root.resolve()):
        raise ValueError("new_external_backup_required")
    destination.parent.mkdir(parents=True, exist_ok=True)
    root.rename(destination)
    return {"archived": True, "runtime_state_modified": False, "external_business_undone": False}


def rehearse(root):
    root = Path(root).absolute()
    verify(root)
    with tempfile.TemporaryDirectory(prefix="contentbase-rollback-") as directory:
        temp = Path(directory)
        candidate, backup = temp / "candidate", temp / "backup"
        candidate.mkdir()
        for name in manifest_files(root):
            path = candidate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(read_source(root, name))
        (candidate / "MANIFEST.sha256").write_bytes(read_release_checksum(root))
        result = archive(candidate, backup)
        absent = not candidate.exists()
        # Check this checkout, not another globally installed copy of the package.
        baseline = subprocess.run([sys.executable, "-I", "-c", "from pathlib import Path; import sys; absent=not Path(sys.argv[1]).exists(); print('source_absent' if absent else 'source_present'); raise SystemExit(1 if absent else 0)", str(candidate)], cwd=temp, capture_output=True, text=True)
        backup.rename(candidate)
        restored_command = [sys.executable, "-I", "-c", "import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=['contentbase','--help']; runpy.run_module('contentbase',run_name='__main__')", str(candidate)]
        modified = subprocess.run(restored_command, cwd=candidate, capture_output=True, text=True)
        verify(candidate)
        if not absent or baseline.returncode != 1 or modified.returncode != 0:
            raise ValueError("rollback_rehearsal_failed")
        return {**result, "baseline_exit": baseline.returncode, "restored_exit": modified.returncode,
                "baseline_stdout": baseline.stdout, "baseline_stderr": baseline.stderr,
                "restored_stdout": modified.stdout, "restored_stderr": modified.stderr}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--rehearse", action="store_true")
    p.add_argument("--archive-to", type=Path)
    args = p.parse_args()
    try:
        if args.rehearse:
            result = rehearse(args.source)
        elif args.archive_to:
            result = archive(args.source, args.archive_to)
        else:
            raise ValueError("rehearse_or_archive_destination_required")
    except (ValueError, OSError):
        print(json.dumps({"ok": False, "error": "rollback_stopped_without_overwriting"}))
        return 1
    print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
