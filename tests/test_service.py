import copy
import json
import os
import tempfile
import threading
import struct
import zlib
import unittest
from pathlib import Path
from contentbase.errors import BaseError
from contentbase.service import Service
from contentbase.store import Store, digest


CONFIG = {"feishu": {"cli": "lark-cli", "profile": "example-profile", "base_token": "BASE_EXAMPLE", "table_id": "tbl_EXAMPLE",
                       "fields": {"title": "标题", "body": "正文", "stage": "阶段", "published_url": "作品链接", "metrics": "复盘指标", "review": "复盘结论"}}, "yxer": {"cli": "yxer"}}


def synthetic_png():
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\x30\x60\x90")) + chunk(b"IEND", b"")


class FakeFeishu:
    def __init__(self):
        self.user = {"open_id": "ou_EXAMPLE", "tenant_key": "TENANT_EXAMPLE"}
        self.row = {"record_id": "rec_EXAMPLE", "id": "rec_EXAMPLE", "fields": {"标题": "示例标题", "正文": "示例正文", "阶段": ["待审核"]}}
        self.writes = 0
        self.failure = False
        self.entered, self.release = None, None

    def identity(self):
        return copy.deepcopy(self.user)

    def binding(self):
        return {"verified": True}

    def record(self, rid):
        return copy.deepcopy(self.row)

    def records(self):
        return [self.record("rec_EXAMPLE")]

    def plan_update(self, rid, fields):
        return {"record_id": rid, "fields": fields, "identity": self.identity(), "before": self.record(rid),
                "before_hash": digest(self.row), "binding": {key: CONFIG["feishu"][key] for key in ("profile", "base_token", "table_id")}}

    def apply_update(self, plan):
        if self.user != plan["identity"] or digest(self.row) != plan["before_hash"]:
            raise BaseError("source_changed")
        if self.entered:
            self.entered.set()
            self.release.wait(5)
        self.writes += 1
        self.row["fields"].update(plan["fields"])
        if self.failure:
            raise BaseError("write_unverified", unknown=True)
        return self.record(plan["record_id"])

    def verify_update(self, plan):
        if any(self.row["fields"].get(key) != value for key, value in plan["fields"].items()):
            raise BaseError("write_unverified", unknown=True)
        return self.record(plan["record_id"])


class FakeYxer:
    def __init__(self):
        self.uploads = self.submits = self.preflights = 0
        self.unknown = False
        self.revalidate_hook = None
        self.provider = {"version": 1, "provider_hash": "PROVIDER_EXAMPLE", "local_target_hash": "CLIENT_EXAMPLE", "accounts_hash": "ACCOUNTS_EXAMPLE", "credential_present": True, "limitations": []}

    def provider_binding(self, account_id=None):
        return copy.deepcopy(self.provider)

    def validate_fields(self, *args):
        return None

    def doctor(self):
        return {"channels": {"cloud": {"configured": True}}}

    def accounts(self):
        return [{"id": "ACCOUNT_EXAMPLE", "platformName": "小红书", "platformAccountName": "示例账号", "status": 1}]

    def contract(self, platform, kind):
        return {"schema": {"businessFields": {}}}

    def choices(self, *args):
        return [{"yixiaoerId": "MUSIC_EXAMPLE", "yixiaoerName": "示例音乐"}]

    def upload(self, path, platform, usage):
        self.uploads += 1
        return {"key": "RESOURCE_EXAMPLE", "size": 8, "width": 1, "height": 1}

    def prepare_payload(self, platform, kind, channel, account, title, body, resources, fields):
        return {"action": "publish", "publishType": kind, "platforms": [platform], "publishChannel": channel,
                "publishArgs": {"accountForms": [{"platformAccountId": account, "contentPublishForm": {"title": title, "description": body, **fields}, "images": resources}]}}

    def preflight(self, payload, path):
        self.preflights += 1
        return {"channel": payload["publishChannel"], "dry_run": True}

    def revalidate(self, payload, path, fields):
        self.preflight(payload, path)
        if self.revalidate_hook:
            self.revalidate_hook()

    def submit(self, payload, path):
        self.submits += 1
        if self.unknown:
            raise BaseError("publish_unknown", unknown=True)
        return {"taskSetId": "TASK_EXAMPLE", "accepted": True, "publicVisible": None, "vendor_receipt": {"id": "TASK_EXAMPLE"}}

    def details(self, task):
        return {"id": task, "tasks": [], "publicVisible": None}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.feishu, self.yxer = FakeFeishu(), FakeYxer()
        self.service = Service(CONFIG, self.root / "state", self.feishu, self.yxer)
        self.image = self.root / "image.png"
        self.image.write_bytes(synthetic_png())

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, key="draft-example-1", body="新的正文"):
        return self.service.draft("rec_EXAMPLE", "新标题", body, key)

    def publish(self, key="publish-example-1"):
        return self.service.publish_prepare("rec_EXAMPLE", "ACCOUNT_EXAMPLE", "小红书", "imageText", "cloud", [(str(self.image), "image")], {}, key)

    def test_draft_is_plan_then_human_approval_then_readback(self):
        op = self.plan()
        self.assertEqual(self.feishu.writes, 0)
        self.assertEqual(op["confirmation_digest"], op["data"]["confirmation"])
        with self.assertRaises(BaseError):
            self.service.apply(op["id"])
        with self.assertRaises(BaseError):
            self.service.approve(op["id"], op["confirmation_digest"], False)
        self.service.approve(op["id"], op["confirmation_digest"], True)
        result = self.service.apply(op["id"])
        self.assertEqual(result["state"], "applied")
        self.assertEqual(self.service.apply(op["id"])["state"], "applied")
        self.assertEqual(self.feishu.writes, 1)

    def test_input_key_conflict_and_approval_change(self):
        op = self.plan()
        self.assertEqual(self.plan()["id"], op["id"])
        with self.assertRaises(BaseError):
            self.plan(body="改变正文")
        with self.assertRaises(BaseError):
            self.service.approve(op["id"], "wrong-hash", True)

    def test_unknown_write_stops_replay_and_can_readonly_reconcile(self):
        op = self.plan()
        self.service.approve(op["id"], op["confirmation_digest"], True)
        self.feishu.failure = True
        with self.assertRaises(BaseError):
            self.service.apply(op["id"])
        self.assertEqual(self.service.store.get(op["id"])["state"], "unknown")
        with self.assertRaises(BaseError):
            self.service.apply(op["id"])
        result = self.service.verify_write(op["id"])
        self.assertEqual(result["state"], "applied")
        self.assertEqual(self.feishu.writes, 1)

    def test_simultaneous_plans_for_same_record_are_serialized(self):
        a, b = self.plan(), self.plan("draft-example-2", "另一正文")
        for op in (a, b):
            self.service.approve(op["id"], op["confirmation_digest"], True)
        self.feishu.entered, self.feishu.release = threading.Event(), threading.Event()
        thread = threading.Thread(target=self.service.apply, args=(a["id"],))
        thread.start()
        self.assertTrue(self.feishu.entered.wait(2))
        # A second Service does not mark the live operation as unknown.
        other = Service(CONFIG, self.root / "state", self.feishu, self.yxer)
        self.assertEqual(other.store.get(a["id"])["state"], "applying")
        with self.assertRaisesRegex(BaseError, "resource_operation_in_progress"):
            other.apply(b["id"])
        self.feishu.release.set()
        thread.join(5)
        self.assertEqual(self.feishu.writes, 1)

    def test_publish_is_real_prepare_but_no_submit_until_exact_confirmation(self):
        op = self.publish()
        self.assertEqual((self.yxer.uploads, self.yxer.submits), (1, 0))
        with self.assertRaises(BaseError):
            self.service.publish_submit(op["id"], op["confirmation_digest"], False)
        with self.assertRaises(BaseError):
            self.service.publish_submit(op["id"], "wrong-hash", True)
        result = self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.assertEqual(result["state"], "submitted")
        self.assertIsNone(result["data"]["receipt"]["publicVisible"])
        self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.assertEqual(self.yxer.submits, 1)
        verified = self.service.publish_verify(op["id"])
        self.assertIsNone(verified["data"]["verification"]["public_visible"])

    def test_source_edit_during_remote_revalidation_blocks_publish(self):
        op = self.publish()
        self.yxer.revalidate_hook = lambda: self.feishu.row["fields"].update({"正文": "后来的版本"})
        with self.assertRaises(BaseError):
            self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.assertEqual(self.yxer.submits, 0)

    def test_new_key_cannot_bypass_unknown_even_when_config_changes(self):
        op = self.publish()
        self.yxer.unknown = True
        with self.assertRaises(BaseError):
            self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        other_config = copy.deepcopy(CONFIG)
        other_config["feishu"]["stages"] = {"review": "新显示名称"}
        other = Service(other_config, self.root / "state", self.feishu, self.yxer)
        with self.assertRaisesRegex(BaseError, "prior_publish_requires_verification"):
            other.publish_prepare("rec_EXAMPLE", "ACCOUNT_EXAMPLE", "小红书", "imageText", "cloud", [(str(self.image), "image")], {}, "publish-new-key")
        self.assertEqual((self.yxer.uploads, self.yxer.submits), (1, 1))

    def test_new_key_cannot_repeat_submitted_source(self):
        op = self.publish()
        self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.feishu.row["fields"]["阶段"] = ["已发布"]
        with self.assertRaisesRegex(BaseError, "same_source_already_submitted"):
            self.publish("publish-repeat-key")
        self.assertEqual(self.yxer.submits, 1)

    def test_expired_preflight_does_not_submit(self):
        op = self.publish()
        with self.service.store.connect() as db:
            data = op["data"]
            data["prepared_at"] = 0
            db.execute("UPDATE operations SET data=? WHERE id=?", (json.dumps(data), op["id"]))
        with self.assertRaisesRegex(BaseError, "publish_preflight_expired"):
            self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.assertEqual(self.yxer.submits, 0)

    def test_unknown_write_new_operation_is_blocked(self):
        op = self.plan()
        self.service.approve(op["id"], op["confirmation_digest"], True)
        self.feishu.failure = True
        with self.assertRaises(BaseError):
            self.service.apply(op["id"])
        second = self.plan("another-draft-key", "新的目标")
        self.service.approve(second["id"], second["confirmation_digest"], True)
        with self.assertRaisesRegex(BaseError, "prior_write_requires_verification"):
            self.service.apply(second["id"])
        self.assertEqual(self.feishu.writes, 1)

    def test_snapshot_isolated_when_identity_changes(self):
        op = self.plan()
        self.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
        with self.assertRaisesRegex(BaseError, "workspace_operator_changed"):
            self.service.inspect()
        with self.assertRaisesRegex(BaseError, "workspace_operator_changed"):
            self.service.inspect(op["id"])

    def test_private_receipt_recovers_query_without_resubmit(self):
        op = self.publish()
        self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        with self.service.store.connect() as db:
            data = self.service.store.get(op["id"])["data"]
            data.pop("receipt")
            db.execute("UPDATE operations SET state='unknown',data=? WHERE id=?", (json.dumps(data), op["id"]))
        self.assertEqual(self.service.publish_verify(op["id"])["data"]["receipt"]["taskSetId"], "TASK_EXAMPLE")
        self.assertEqual(self.yxer.submits, 1)

    def test_web_media_ids_no_host_paths(self):
        with self.assertRaises(BaseError):
            self.service.media_paths([str(self.image)])
        media = self.service.register_media("example.png", b"example-bytes", "image")
        self.assertEqual(len(self.service.media_paths([media["id"]])), 1)
        path, _ = self.service.media_paths([media["id"]])[0]
        Path(path).write_bytes(b"changed")
        with self.assertRaisesRegex(BaseError, "media_changed"):
            self.service.media_paths([media["id"]])

    def test_metric_provenance_and_https_public_url_required(self):
        with self.assertRaises(BaseError):
            self.service.review("rec_EXAMPLE", {"views": 0}, "结论", "review-example")
        with self.assertRaises(BaseError):
            self.service.published("rec_EXAMPLE", "javascript:alert(1)", "public-example")
        metrics = {"platform": "示例平台", "work_id": "WORK_EXAMPLE", "observed_at": "2026-01-01T00:00:00Z", "values": {"views": None}}
        result = self.service.review("rec_EXAMPLE", metrics, "待进一步观察", "review-example")
        self.assertIn('"views": null', result["data"]["plan"]["fields"]["复盘指标"])


class StoreTests(unittest.TestCase):
    def test_dead_owner_recovers_unknown_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.db"
            store = Store(path)
            op, _ = store.create("publish", "example-key", {}, {}, "submitting")
            with store.connect() as db:
                db.execute("UPDATE operations SET run_pid=? WHERE id=?", (2147483647, op["id"]))
            self.assertEqual(Store(path).get(op["id"])["state"], "unknown")

    def test_source_state_is_rejected(self):
        with self.assertRaisesRegex(BaseError, "runtime_state_must_be_outside_source"):
            Service(CONFIG, Path(__file__).resolve().parents[1] / "private", FakeFeishu(), FakeYxer())


if __name__ == "__main__":
    unittest.main()
