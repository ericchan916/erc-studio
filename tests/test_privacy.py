from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout

# The scripts also work before installing the package.
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from privacy_audit import MANIFEST_NAME, allowed_path, audit, main as audit_main
from export_source import ExportError, export


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "candidate"
        self.root.mkdir()
        self.write("README.md", "# Content Company Base\nNo personal runtime data.\n")
        self.write("contentbase/__init__.py", 'VERSION = "0.1.0"\n')
        self.set_manifest(["README.md", "contentbase/__init__.py", MANIFEST_NAME])

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")
        return path

    def set_manifest(self, names):
        self.write(MANIFEST_NAME, json.dumps({"version": 1, "files": names}, indent=2) + "\n")

    def test_generic_placeholders_and_official_domains_pass(self):
        self.write("README.md", "\n".join([
            "/Users/" + "YOUR_USER" + "/project",
            "C:" + chr(92) + "Users" + chr(92) + "YOUR_USER" + chr(92) + "project",
            "https://TENANT.feishu.cn/base/YOUR_BASE_TOKEN",
            "https://example.feishu.cn https://open.feishu.cn",
            'api_key: "YOUR_API_KEY"', 'password = "YOUR_PASSWORD"',
            'client_secret = "${CLIENT_SECRET}"',
            "https://content.example.invalid ou_YOUR_MEMBER cli_YOUR_PROFILE",
            "cli_exampleprofile123456 tblexampletable123456",
        ]))
        self.assertEqual(audit(self.root), [])

    def test_builtin_sensitive_markers_and_redacted_reports(self):
        markers = [
            ("private_home_path", "/Users/" + "privatefixture" + "/runtime"),
            ("private_home_path", "C:" + chr(92) + "Users" + chr(92) + "privatefixture"),
            ("private_home_path", "C:" + chr(92) * 2 + "Users" + chr(92) * 2 + "privatefixture"),
            ("private_feishu_identifier", "ou_" + "a" * 24),
            ("private_feishu_identifier", "oc_" + "b" * 24),
            ("private_feishu_identifier", "om_" + "c" * 24),
            ("private_feishu_identifier", "cli_" + "d" * 24),
            ("private_feishu_identifier", "rec" + "e" * 16),
            ("private_feishu_identifier", "tbl" + "f" * 16),
            ("private_feishu_identifier", "fld" + "g" * 16),
            ("private_feishu_tenant", "https://" + "privatefixture" + ".feishu.cn"),
            ("api_secret", "sk-" + "K" * 30),
            ("private_key", "-----BEGIN " + "PRIVATE KEY-----"),
            ("hardcoded_credential", "password" + ' = "' + "fixture-secret" + '"'),
            ("hardcoded_credential", "YXER_API_KEY" + ' = "' + "fixture-secret" + '"'),
        ]
        for rule, marker in markers:
            with self.subTest(rule=rule):
                self.write("README.md", "# Fixture\n" + marker + "\n")
                findings = audit(self.root)
                self.assertIn(rule, {entry["rule"] for entry in findings})
                self.assertTrue(any(entry["line"] == 2 for entry in findings))
                self.assertNotIn(marker, json.dumps(findings))
                self.assertTrue(all(set(entry) == {"file", "line", "rule"} for entry in findings))

    def test_denylist_is_external_and_checks_filename(self):
        marker = "Private" + "Identity" + "Fixture"
        denylist = self.workspace / "private-denylist.json"
        denylist.write_text(json.dumps({"values": [marker]}), encoding="utf-8")
        self.write("docs/" + marker + ".md", "# General docs\n")
        findings = audit(self.root, denylist)
        self.assertIn("private_denylist", {entry["rule"] for entry in findings})
        self.assertNotIn(marker, json.dumps(findings))
        inside = self.write("private-denylist.json", json.dumps([marker]))
        self.assertEqual(audit(self.root, inside)[0]["rule"], "denylist_must_be_external")

    def test_unregistered_public_candidate_is_checked(self):
        self.write("docs/unregistered.md", "sk-" + "Z" * 30)
        self.assertIn("api_secret", {entry["rule"] for entry in audit(self.root)})
        with self.assertRaises(ExportError):
            export(self.root, self.workspace / "published")
        self.assertFalse((self.workspace / "published").exists())

    def test_binary_source_is_rejected(self):
        for value in (b"\x89PNG\xff", b"hello\x00world"):
            self.write("contentbase/__init__.py", value)
            self.assertIn("binary_source", {entry["rule"] for entry in audit(self.root)})
            with self.assertRaises(ExportError):
                export(self.root, self.workspace / "published")

    def test_path_allowlist_and_traversal(self):
        for name in ("../README.md", "/README.md", "docs/../README.md", "docs//a.md",
                     "C:" + chr(92) + "Users" + chr(92) + "fixture" + chr(92) + "a.py", ".env", "runtime/config.json", ".git/config",
                     "contentbase/runtime/a.py", "contentbase/image.png", "docs/private.db"):
            with self.subTest(name=name):
                self.assertFalse(allowed_path(name))
                self.set_manifest([name])
                with self.assertRaises(ExportError):
                    export(self.root, self.workspace / "published")
                self.assertFalse((self.workspace / "published").exists())

    def test_symlinks_and_symlink_parent_are_rejected(self):
        private_file = self.workspace / "fixture.md"
        private_file.write_text("outside source", encoding="utf-8")
        (self.root / "README.md").unlink()
        (self.root / "README.md").symlink_to(private_file)
        self.assertIn("symlink", {entry["rule"] for entry in audit(self.root)})
        with self.assertRaises(ExportError):
            export(self.root, self.workspace / "published")
        (self.root / "README.md").unlink()
        self.write("README.md", "# Normal\n")
        outside = self.workspace / "outside"
        outside.mkdir()
        (outside / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "contentbase/linked").symlink_to(outside, target_is_directory=True)
        self.set_manifest(["README.md", "contentbase/linked/a.py", MANIFEST_NAME])
        self.assertIn("symlink", {entry["rule"] for entry in audit(self.root)})
        with self.assertRaises(ExportError):
            export(self.root, self.workspace / "published")

    def test_clean_deterministic_export_excludes_history_and_runtime(self):
        if shutil.which("git"):
            subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        else:
            # An explicitly synthetic history fixture for minimal offline images;
            # the source-export gate must exclude it just like real Git metadata.
            self.write(".git/HEAD", "ref: refs/heads/synthetic-fixture\n")
            self.write(".git/config", "[core]\nrepositoryformatversion = 0\n")
            self.write(".git/logs/HEAD", "synthetic fixture history; not a real commit\n")
        self.write(".git/private-fixture", "internal history")
        self.write("runtime/config.json", "local runtime")
        self.write(".env", "local credentials")
        first = export(self.root, self.workspace / "published-one")
        second = export(self.root, self.workspace / "published-two")
        self.assertEqual(first["file_count"], 3)
        self.assertEqual(first["archive_sha256"], second["archive_sha256"])
        self.assertEqual(Path(first["archive_path"]).read_bytes(), Path(second["archive_path"]).read_bytes())
        published = Path(first["destination"])
        self.assertFalse((published / ".git").exists())
        self.assertFalse((published / "runtime").exists())
        self.assertFalse((published / ".env").exists())
        self.assertEqual(audit(published), [])
        for line in (published / "MANIFEST.sha256").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((published / name).read_bytes()).hexdigest())
        with tarfile.open(first["archive_path"], "r:gz") as archive:
            for member in archive.getmembers():
                self.assertEqual((member.uid, member.gid, member.mtime, member.uname, member.gname), (0, 0, 0, "", ""))
                self.assertTrue(member.isfile())
        with self.assertRaises(ExportError):
            export(self.root, published)
        with self.assertRaises(ExportError):
            export(self.root, self.root / "exported")

    def test_invalid_denylist_and_manifest_are_content_free(self):
        private = self.workspace / "denylist.json"
        private.write_text("invalid", encoding="utf-8")
        self.assertEqual(audit(self.root, private)[0]["rule"], "denylist_unreadable")
        self.set_manifest(["README.md", "README.md"])
        self.assertIn("manifest_duplicate_path", {entry["rule"] for entry in audit(self.root)})
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            status = audit_main(["--root", str(self.root)])
        self.assertEqual(status, 1)
        self.assertNotIn(str(self.workspace), buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
