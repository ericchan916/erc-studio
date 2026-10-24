"""Isolated process-generation, migration and private-file regression fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from contentbase.config import private_write
from contentbase.errors import BaseError
from contentbase.store import Store
import contentbase.store as store_module


class StoreAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.path = self.workspace / "state" / "outbox.db"

    def legacy_database(self, *, pid_column=True, state=None, pid=None, scoped=False):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as database:
            columns = ", run_pid INTEGER" if pid_column else ""
            database.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, kind TEXT NOT NULL, request_key TEXT NOT NULL, input_hash TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL" + columns + ", UNIQUE(kind,request_key))")
            if state:
                data = {"binding_hash": "fixture-binding", "plan": {"identity": {"open_id": "ou_FIXTURE", "tenant_key": "TENANT_FIXTURE"}}} if scoped else {}
                values = ("op_LEGACY_FIXTURE", "draft", "legacy-fixture-key", "fixture-hash", state, json.dumps(data), 1, 1)
                if pid_column:
                    database.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?)", (*values, pid))
                else:
                    database.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)", values)
            database.commit()

    def test_legacy_active_row_with_unrelated_live_pid_becomes_unknown(self):
        # This old schema has no evidence tying the recycled PID to this operation.
        self.legacy_database(state="applying", pid=os.getpid())
        recovered = Store(self.path)
        self.assertEqual(recovered.get("op_LEGACY_FIXTURE")["state"], "unknown")

    def test_actually_live_current_operation_is_not_recovered(self):
        store = Store(self.path)
        operation, _ = store.create("draft", "live-fixture-key", {}, {}, state="applying")
        self.assertEqual(Store(self.path).get(operation["id"])["state"], "applying")

    def zombie(self, mode):
        program = (
            "import os,sys; from contentbase.store import Store; "
            "store=Store(sys.argv[1]); mode=sys.argv[2]; "
            "store.create('draft','zombie-worker-key',{}, {},state='submitting') if mode=='operation' else None; "
            "lock=store.resource_lock('fixture-resource') if mode=='lock' else None; "
            "lock.__enter__() if lock else None; os._exit(0)"
        )
        child = subprocess.Popen([sys.executable, "-c", program, str(self.path), mode],
                                 cwd=Path(__file__).resolve().parents[1],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: child.wait(timeout=5))
        for _ in range(200):
            if Path("/proc").is_dir():
                try:
                    state = (Path("/proc") / str(child.pid) / "stat").read_text().rsplit(")", 1)[1].split()[0]
                except (OSError, IndexError):
                    state = ""
                zombie = state == "Z"
            else:
                result = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(child.pid)], capture_output=True, text=True)
                zombie = any(state.startswith("Z") for state in result.stdout.split())
            if zombie:
                self.assertTrue(self.path.exists(), "the child must have initialized its fixture database")
                return child.pid
            time.sleep(0.01)
        self.fail("fixture subprocess did not enter the observable zombie state")

    def test_zombie_worker_is_recovered_even_when_kill_zero_succeeds(self):
        pid = self.zombie("operation")
        os.kill(pid, 0)
        recovered = Store(self.path).lookup("draft", "zombie-worker-key")
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["state"], "unknown")

    def test_zombie_resource_lock_does_not_block_forever(self):
        self.zombie("lock")
        with Store(self.path).resource_lock("fixture-resource"):
            pass

    def test_concurrent_old_schema_initializers_do_not_race_alter_table(self):
        self.legacy_database(pid_column=False)
        barrier = threading.Barrier(2)
        original_connect = sqlite3.connect

        class SnapshotCursor:
            def __init__(self, rows):
                self.rows = rows
            def __iter__(self):
                barrier.wait(timeout=2)
                return iter(self.rows)
            def fetchall(self):
                barrier.wait(timeout=2)
                return self.rows

        class Connection(sqlite3.Connection):
            def execute(self, statement, *args, **kwargs):
                cursor = super().execute(statement, *args, **kwargs)
                if statement.upper().startswith("PRAGMA TABLE_INFO(OPERATIONS)") and not self.in_transaction:
                    return SnapshotCursor(list(cursor))
                return cursor

        def connect(*args, **kwargs):
            kwargs["factory"] = Connection
            return original_connect(*args, **kwargs)

        with patch.object(store_module.sqlite3, "connect", side_effect=connect):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(Store, self.path) for _ in range(2)]
                errors = []
                for future in futures:
                    try:
                        future.result(timeout=5)
                    except Exception as error:
                        errors.append(type(error).__name__ + ":" + str(error))
        self.assertEqual(errors, [], "schema initialization must be serialized or idempotent")

    def test_database_symlink_replacement_is_rejected_on_later_connections(self):
        store = Store(self.path)
        vendor = Store(self.workspace / "vendor" / "outbox.db")
        self.path.unlink()
        self.path.symlink_to(vendor.path)
        with self.assertRaises(BaseError):
            store.create("draft", "misdirected-fixture-key", {}, {})
        self.assertEqual(vendor.all_operations(), [])

    def test_database_hardlink_to_vendor_state_is_rejected(self):
        vendor = Store(self.workspace / "vendor" / "outbox.db")
        self.path.parent.mkdir()
        os.link(vendor.path, self.path)
        with self.assertRaises(BaseError):
            Store(self.path).create("draft", "hardlink-fixture-key", {}, {})
        with closing(sqlite3.connect("file:" + str(vendor.path) + "?mode=ro", uri=True)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 0)

    def test_journal_symlink_never_overwrites_an_external_file(self):
        store = Store(self.path)
        outside = self.workspace / "outside-journal.txt"
        outside.write_bytes(b"outside-journal-preserved")
        self.path.with_name(self.path.name + "-journal").symlink_to(outside)
        try:
            store.create("draft", "journal-fixture-key", {}, {})
        except (BaseError, sqlite3.Error, OSError):
            pass
        self.assertEqual(outside.read_bytes(), b"outside-journal-preserved")

    def test_database_swap_at_sqlite_open_is_rejected_before_vendor_writes(self):
        store = Store(self.path)
        vendor = Store(self.workspace / "vendor" / "outbox.db")
        original_connect = sqlite3.connect
        swapped = False

        def replace_before_sqlite_open(path, *args, **kwargs):
            nonlocal swapped
            if not swapped and isinstance(path, (str, os.PathLike)) and Path(path) == self.path:
                swapped = True
                self.path.unlink()
                self.path.symlink_to(vendor.path)
            return original_connect(path, *args, **kwargs)

        with patch.object(store_module.sqlite3, "connect", side_effect=replace_before_sqlite_open):
            with self.assertRaises(BaseError):
                store.create("draft", "raced-fixture-key", {}, {})
        self.assertEqual(vendor.all_operations(), [])

    def test_existing_public_mode_database_is_private_before_journal_writes(self):
        self.legacy_database(state="prepared", pid=None, scoped=True)
        self.path.chmod(0o644)
        observed = []
        original_connect = sqlite3.connect
        journal = self.path.with_name(self.path.name + "-journal")

        class Connection(sqlite3.Connection):
            def execute(self, statement, *args, **kwargs):
                result = super().execute(statement, *args, **kwargs)
                if journal.exists():
                    observed.append(journal.stat().st_mode & 0o777)
                return result

        def connect(*args, **kwargs):
            kwargs["factory"] = Connection
            return original_connect(*args, **kwargs)

        with patch.object(store_module.sqlite3, "connect", side_effect=connect):
            Store(self.path)
        self.assertTrue(observed, "journal visibility must actually be observed")
        self.assertTrue(all(mode == 0o600 for mode in observed), observed)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_database_fails_with_a_structured_error_and_preserves_bytes(self):
        self.path.parent.mkdir()
        content = b"not-a-sqlite-fixture"
        self.path.write_bytes(content)
        try:
            with self.assertRaises(BaseError):
                Store(self.path)
        finally:
            self.assertEqual(self.path.read_bytes(), content)

    def test_private_write_concurrent_same_path_is_atomic_private_and_cleans_temps(self):
        target = self.workspace / "config" / "config.json"
        def write(index):
            private_write(target, {"writer": index, "text": str(index) * 1000})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(32)))
        result = json.loads(target.read_text())
        self.assertEqual(result["text"], str(result["writer"]) * 1000)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(target.parent.glob("*.tmp")), [])
        self.assertEqual(list(target.parent.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
