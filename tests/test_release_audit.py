"""Offline regression cases for source release and private-state boundaries.

All samples are constructed synthetic inputs in isolated temporary directories.
No vendor login, external API, upload or real business write is performed.
"""
from __future__ import annotations

import json
import gzip
import hashlib
import io
import tarfile
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import privacy_audit
import export_source
import rollback
from contentbase.config import load_config, private_write
from contentbase.errors import BaseError
from contentbase.runner import Runner
import build_backend


class SdistMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "synthetic-source.tar.gz"

    def archive(self, members):
        with self.path.open("wb") as out, gzip.GzipFile(filename="SYNTHETIC_BUILDER_NAME", mode="wb", fileobj=out, mtime=123456) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for member, data in members:
                    archive.addfile(member, io.BytesIO(data) if member.isfile() else None)

    def member(self, name="source/README.md", data=b"synthetic public source\n"):
        member = tarfile.TarInfo(name)
        member.size = len(data)
        member.uid, member.gid = 12345, 23456
        member.uname, member.gname = "SYNTHETIC_BUILDER", "SYNTHETIC_GROUP"
        member.mtime, member.mode = 123456, 0o600
        member.pax_headers = {"mtime": "123456.123", "comment": "SYNTHETIC_MACHINE_PATH"}
        return member, data

    def test_sdist_neutralizes_all_owner_time_gzip_metadata_and_keeps_bytes(self):
        member, data = self.member()
        directory = tarfile.TarInfo("source")
        directory.type = tarfile.DIRTYPE
        directory.uname, directory.uid = "SYNTHETIC_BUILDER", 12345
        self.archive([(directory, b""), (member, data)])
        build_backend._neutral_sdist(self.path)
        raw = self.path.read_bytes()
        self.assertEqual(raw[4:8], b"\0\0\0\0")
        self.assertFalse(raw[3] & 8)  # gzip FNAME never embeds the build filename.
        with tarfile.open(self.path) as archive:
            for item in archive.getmembers():
                self.assertEqual((item.uid, item.gid, item.uname, item.gname, item.mtime, item.pax_headers), (0, 0, "", "", 0, {}))
                self.assertEqual(item.mode, 0o755 if item.isdir() else 0o644)
            self.assertEqual(archive.extractfile(member.name).read(), data)

    def test_invalid_links_traversal_and_duplicate_members_preserve_original(self):
        for label in ("symlink", "hardlink", "fifo", "traversal", "duplicate"):
            member, data = self.member()
            if label in ("symlink", "hardlink", "fifo"):
                member.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE, "fifo": tarfile.FIFOTYPE}[label]
                member.linkname, member.size = "../outside", 0
            if label == "traversal":
                member.name = "source/../outside.md"
            members = [(member, data)] * (2 if label == "duplicate" else 1)
            self.archive(members)
            before = hashlib.sha256(self.path.read_bytes()).hexdigest()
            with self.subTest(label=label), self.assertRaises(build_backend.BuildAuditError):
                build_backend._neutral_sdist(self.path)
            self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
            self.assertEqual(list(self.root.glob(".sdist-neutral-*")), [])

    def test_corrupt_archive_does_not_leak_file_descriptors_or_temp_files(self):
        self.path.write_bytes(b"not a gzip archive")
        fd_dir = Path("/proc/self/fd") if Path("/proc/self/fd").is_dir() else Path("/dev/fd")
        before = len(list(fd_dir.iterdir()))
        for _ in range(5):
            with self.assertRaises(build_backend.BuildAuditError):
                build_backend._neutral_sdist(self.path)
        self.assertEqual(len(list(fd_dir.iterdir())), before)
        self.assertEqual(self.path.read_bytes(), b"not a gzip archive")
        self.assertEqual(list(self.root.glob(".sdist-neutral-*")), [])

    def test_untrusted_backend_filename_never_escapes_output_directory(self):
        for name in ("../outside.tar.gz", "/outside.tar.gz", "not-source.whl", None):
            with self.subTest(name=name), patch.object(build_backend, "_invoke", return_value=name):
                with self.assertRaisesRegex(build_backend.BuildAuditError, "sdist_filename_invalid"):
                    build_backend.build_sdist(str(self.root))


class ReleaseAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "source"
        self.root.mkdir()
        self.write("README.md", "# Synthetic release fixture\n")
        self.write("contentbase/__init__.py", "VERSION = 'fixture'\n")
        self.manifest(["README.md", "contentbase/__init__.py"])

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def manifest(self, names):
        self.write(privacy_audit.MANIFEST_NAME, json.dumps({
            "version": 1, "files": sorted([privacy_audit.MANIFEST_NAME, *names]),
        }) + "\n")

    def test_multiline_hardcoded_credential_is_detected(self):
        content = '{\n  "' + "api_key" + '":\n    "' + "fixture-secret-value" + '"\n}\n'
        self.write("config.example.json", content)
        self.manifest(["README.md", "contentbase/__init__.py", "config.example.json"])
        self.assertIn("hardcoded_credential", {item["rule"] for item in privacy_audit.audit(self.root)})

    def test_invalid_domain_substring_does_not_exempt_a_password(self):
        content = "password" + ' = "' + "fixture-secret@example.invalid" + '"\n'
        self.write("contentbase/__init__.py", content)
        self.assertIn("hardcoded_credential", {item["rule"] for item in privacy_audit.audit(self.root)})

    def test_export_rejects_unregistered_source_dependency(self):
        self.write("contentbase/__init__.py", "from .required import VALUE\n")
        self.write("contentbase/required.py", "VALUE = 'fixture'\n")
        with self.assertRaises(export_source.ExportError):
            export_source.export(self.root, self.workspace / "public")
        self.assertFalse((self.workspace / "public").exists())

    def test_source_parent_symlink_swap_is_rejected(self):
        outside = self.workspace / "outside"
        outside.mkdir()
        (outside / "__init__.py").write_text("OUTSIDE = True\n", encoding="utf-8")
        source_directory = self.root / "contentbase"
        original_open = os.open
        swapped = False

        def swap_parent_before_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and isinstance(path, (str, os.PathLike)) and Path(path).name == "__init__.py":
                swapped = True
                source_directory.rename(self.root / "original-source")
                source_directory.symlink_to(outside, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(privacy_audit.os, "open", side_effect=swap_parent_before_open):
            try:
                data = privacy_audit.read_source(self.root, "contentbase/__init__.py")
            except privacy_audit.PrivacyError:
                return  # Rejecting the race is valid.
            self.assertEqual(data, b"VERSION = 'fixture'\n")

    def test_export_rejects_a_changed_staged_manifest(self):
        original_read = export_source.read_source

        def changed_manifest(root, name):
            if name == privacy_audit.MANIFEST_NAME:
                return json.dumps({"version": 1, "files": [privacy_audit.MANIFEST_NAME, "contentbase/__init__.py"]}).encode()
            return original_read(root, name)

        with patch.object(export_source, "read_source", side_effect=changed_manifest):
            with self.assertRaises(export_source.ExportError):
                export_source.export(self.root, self.workspace / "public")
        self.assertFalse((self.workspace / "public").exists())

    def test_rollback_rejects_incomplete_checksums(self):
        public = self.workspace / "public"
        export_source.export(self.root, public)
        checksum = public / "MANIFEST.sha256"
        checksum.write_text("".join(line + "\n" for line in checksum.read_text().splitlines() if not line.endswith("  README.md")), encoding="utf-8")
        with self.assertRaises(ValueError):
            rollback.verify(public)

    def test_rollback_rejects_a_symlinked_source_parent(self):
        public = self.workspace / "public"
        export_source.export(self.root, public)
        (public / "contentbase").rename(self.workspace / "outside-source")
        (public / "contentbase").symlink_to(self.workspace / "outside-source", target_is_directory=True)
        with self.assertRaises(ValueError):
            rollback.verify(public)

    def test_rollback_keeps_unregistered_runtime_at_its_original_path(self):
        public = self.workspace / "public"
        export_source.export(self.root, public)
        runtime = public / "runtime" / "outbox.db"
        runtime.parent.mkdir()
        runtime.write_bytes(b"private-state-fixture")
        backup = self.workspace / "backup"
        with self.assertRaises(ValueError):
            rollback.archive(public, backup)
        self.assertEqual(runtime.read_bytes(), b"private-state-fixture")
        self.assertTrue(public.is_dir())
        self.assertFalse(backup.exists())

    def test_verified_source_rollback_preserves_external_data_and_development_files(self):
        public = self.workspace / "public"
        export_source.export(self.root, public)
        for name in (".git/config", "contentbase/__pycache__/fixture.pyc"):
            path = public / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"development-fixture")
        private = self.workspace / "private-state.db"
        private.write_bytes(b"unchanged-private-fixture")
        backup = self.workspace / "backup"
        result = rollback.archive(public, backup)
        self.assertFalse(public.exists())
        self.assertFalse(result["runtime_state_modified"])
        self.assertEqual(private.read_bytes(), b"unchanged-private-fixture")
        self.assertEqual((backup / ".git/config").read_bytes(), b"development-fixture")
        rollback.verify(backup)

    def test_control_characters_in_source_filenames_are_rejected(self):
        for name in ("docs/line\nbreak.md", "contentbase/tab\tname.py", "scripts/control" + chr(127) + ".py"):
            self.assertFalse(privacy_audit.allowed_path(name))

    def test_archive_rejects_post_audit_staging_changes(self):
        original_archive = export_source._archive

        def mutate_staging(staged, destination, names, expected_hashes):
            (staged / "README.md").write_text("Changed after scanning\n", encoding="utf-8")
            return original_archive(staged, destination, names, expected_hashes)

        with patch.object(export_source, "_archive", side_effect=mutate_staging):
            with self.assertRaises(export_source.ExportError):
                export_source.export(self.root, self.workspace / "public")
        self.assertFalse((self.workspace / "public").exists())

    def test_archive_rejects_post_audit_staging_symlinks(self):
        outside = self.workspace / "outside.md"
        outside.write_text("Outside fixture\n", encoding="utf-8")
        original_archive = export_source._archive

        def replace_staging(staged, destination, names, expected_hashes):
            (staged / "README.md").unlink()
            (staged / "README.md").symlink_to(outside)
            return original_archive(staged, destination, names, expected_hashes)

        with patch.object(export_source, "_archive", side_effect=replace_staging):
            with self.assertRaises(export_source.ExportError):
                export_source.export(self.root, self.workspace / "public")
        self.assertFalse((self.workspace / "public").exists())

    def test_private_write_never_follows_a_preexisting_temporary_symlink(self):
        outside = self.workspace / "vendor-state.json"
        outside.write_text("vendor-state-preserved\n", encoding="utf-8")
        target = self.workspace / "private" / "config.json"
        target.parent.mkdir()
        target.with_name(target.name + ".tmp").symlink_to(outside)
        try:
            private_write(target, {"fixture": "new-config"})
        except (BaseError, OSError, ValueError):
            pass  # Rejecting the suspicious temp path is also a valid outcome.
        self.assertEqual(outside.read_text(), "vendor-state-preserved\n")
        self.assertFalse(target.is_symlink())

    def test_invalid_feishu_section_uses_a_structured_error(self):
        config = self.workspace / "config.json"
        config.write_text(json.dumps({"feishu": [], "yxer": {}}), encoding="utf-8")
        with self.assertRaises(BaseError):
            load_config(config)

    def test_non_utf8_vendor_write_response_keeps_unknown_status(self):
        try:
            Runner()([sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([255]))"], write=True)
        except BaseError as error:
            self.assertTrue(error.unknown)
        except UnicodeError:
            self.fail("non-UTF-8 vendor output escaped the structured unknown result")
        else:
            self.fail("invalid vendor output must not be accepted")

    def test_rollback_requires_a_nonempty_checksum_manifest(self):
        self.write("MANIFEST.sha256", "")
        with self.assertRaises(ValueError):
            rollback.verify(self.root)

    def test_rollback_rejects_a_symlinked_checksum_manifest(self):
        checksum = self.workspace / "external-checksum.txt"
        checksum.write_text("", encoding="utf-8")
        (self.root / "MANIFEST.sha256").symlink_to(checksum)
        with self.assertRaises(ValueError):
            rollback.verify(self.root)


class BuildBackendAuditTests(unittest.TestCase):
    """Build guards do not need setuptools installed to test their boundaries."""
    setUp = ReleaseAuditTests.setUp
    write = ReleaseAuditTests.write
    manifest = ReleaseAuditTests.manifest

    def prepare_backend(self):
        import build_backend
        original = Path(__file__).resolve().parents[1]
        added = ["build_backend.py", "scripts/privacy_audit.py", "scripts/export_source.py"]
        for name in added:
            self.write(name, (original / name).read_text(encoding="utf-8"))
        self.manifest(["README.md", "contentbase/__init__.py", *added])
        return build_backend

    def call_hook(self, backend, name):
        if name.startswith("prepare_metadata"):
            return getattr(backend, name)(self.workspace / "metadata")
        if name in {"build_wheel", "build_editable"}:
            return getattr(backend, name)(self.workspace / "wheels")
        if name == "build_sdist":
            return backend.build_sdist(self.workspace / "sdists")
        return getattr(backend, name)()

    def test_all_standard_and_editable_hooks_audit_before_delegating(self):
        backend = self.prepare_backend()
        self.write("contentbase/unregistered.py", "VALUE = 'unregistered'\n")
        hooks = ("get_requires_for_build_wheel", "get_requires_for_build_sdist",
                 "prepare_metadata_for_build_wheel", "build_wheel", "build_sdist",
                 "get_requires_for_build_editable", "prepare_metadata_for_build_editable", "build_editable")
        with patch.object(backend, "_ROOT", self.root), patch.object(backend, "_setuptools") as delegate:
            for hook in hooks:
                with self.subTest(hook=hook), self.assertRaises(backend.BuildAuditError):
                    self.call_hook(backend, hook)
            delegate.assert_not_called()

    def test_public_build_uses_exact_external_copy_and_drops_unlisted_data(self):
        backend = self.prepare_backend()
        self.write("contentbase/web/private.json", '{"fixture": "unlisted"}\n')
        binary = self.root / "contentbase/web/private.png"
        binary.write_bytes(b"\x89PNG\x00FIXTURE")
        self.write("AUTHORS", "Private author fixture\n")
        self.write("old.egg-info/SOURCES.txt", "AUTHORS\ncontentbase/web/private.png\n")
        calls = []

        class Delegate:
            def build_wheel(inner, **arguments):
                clean = Path.cwd()
                calls.append((clean, arguments))
                self.assertNotEqual(clean, self.root)
                self.assertFalse(clean.resolve().is_relative_to(self.root.resolve()))
                for name in ("AUTHORS", "old.egg-info", "contentbase/web/private.json", "contentbase/web/private.png"):
                    self.assertFalse((clean / name).exists(), name)
                self.assertTrue((clean / "build_backend.py").is_file())
                self.assertTrue((clean / "MANIFEST.sha256").is_file())
                return "fixture.whl"

        with patch.object(backend, "_ROOT", self.root), patch.object(backend, "_setuptools", return_value=Delegate()):
            with backend._working_directory(self.workspace):
                self.assertEqual(backend.build_wheel("wheels", metadata_directory="metadata"), "fixture.whl")
        clean, arguments = calls[0]
        self.assertEqual(Path(arguments["wheel_directory"]).resolve(), (self.workspace / "wheels").resolve())
        self.assertEqual(Path(arguments["metadata_directory"]).resolve(), (self.workspace / "metadata").resolve())
        self.assertFalse(clean.exists(), "the disposable build copy must be cleaned")
        self.assertTrue(binary.exists(), "the developer's original files are not deleted")

    def test_build_outputs_and_metadata_cannot_land_in_source(self):
        backend = self.prepare_backend()
        with patch.object(backend, "_ROOT", self.root), patch.object(backend, "_setuptools") as delegate:
            for name in ("build_wheel", "build_sdist", "build_editable", "prepare_metadata_for_build_wheel", "prepare_metadata_for_build_editable"):
                with self.subTest(hook=name), self.assertRaises(backend.BuildAuditError):
                    getattr(backend, name)(self.root / "dist")
            with self.assertRaises(backend.BuildAuditError):
                backend.build_wheel(self.workspace / "wheels", metadata_directory=self.root / "metadata")
            delegate.assert_not_called()

    def test_delegated_hooks_restore_caller_arguments(self):
        backend = self.prepare_backend()
        original_arguments = sys.argv[:]
        class Delegate:
            def get_requires_for_build_wheel(inner, **arguments):
                sys.argv = ["backend-fixture", "egg_info"]
                return []
        with patch.object(backend, "_ROOT", self.root), patch.object(backend, "_setuptools", return_value=Delegate()):
            backend.get_requires_for_build_wheel()
        self.assertEqual(sys.argv, original_arguments)

    def test_editable_keeps_original_path_but_still_requires_external_output(self):
        backend = self.prepare_backend()
        original = self.root
        observed = []
        class Delegate:
            def build_editable(inner, **arguments):
                observed.append((Path.cwd(), arguments))
                return "private-editable-fixture.whl"
        with patch.object(backend, "_ROOT", original), patch.object(backend, "_setuptools", return_value=Delegate()):
            backend.build_editable(self.workspace / "private-wheels")
        self.assertEqual(observed[0][0].resolve(), original.resolve())
        self.assertEqual(Path(observed[0][1]["wheel_directory"]).resolve(), (self.workspace / "private-wheels").resolve())


if __name__ == "__main__":
    unittest.main()
