"""Evidence-driven regressions for identity, idempotency and long preflights."""
import copy
import json
import math
from pathlib import Path
from unittest.mock import patch
from contentbase.errors import BaseError
from contentbase.service import Service, text
import test_service as fixture
from test_service import CONFIG, FakeFeishu, FakeYxer
import unittest


class CoreAuditTests(unittest.TestCase):
    setUp = fixture.ServiceTests.setUp
    tearDown = fixture.ServiceTests.tearDown
    plan = fixture.ServiceTests.plan
    publish = fixture.ServiceTests.publish
    def test_applied_operation_does_not_reveal_another_identity_material(self):
        op = self.plan()
        self.service.approve(op["id"], op["confirmation_digest"], True)
        self.service.apply(op["id"])
        self.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
        with self.assertRaises(BaseError):
            self.service.apply(op["id"])

    def test_workspace_cannot_silently_share_yxer_credentials_with_new_feishu_user(self):
        self.service.account_snapshot()
        self.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
        with self.assertRaisesRegex(BaseError, "workspace_operator_changed"):
            self.service.account_snapshot()

    def test_received_vendor_receipt_and_failed_local_save_remain_unknown(self):
        op = self.publish()
        from contentbase.config import private_write as real_write
        def failing_receipt(path, value):
            if Path(path).name == "receipt.json":
                raise BaseError("private_write_failed")
            return real_write(path, value)
        with patch("contentbase.service.private_write", side_effect=failing_receipt):
            with self.assertRaises(BaseError) as caught:
                self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.assertTrue(caught.exception.unknown)
        stored = self.service.store.get(op["id"])
        self.assertEqual(stored["state"], "unknown")
        self.assertEqual(stored["data"]["receipt"]["taskSetId"], "TASK_EXAMPLE")
        self.assertEqual(self.yxer.submits, 1)

    def test_completed_external_write_and_failed_database_finalization_are_unknown(self):
        op = self.plan()
        self.service.approve(op["id"], op["confirmation_digest"], True)
        real_transition = self.service.store.transition
        def fail_finalization(oid, expected, target, data=None):
            if target == "applied":
                raise BaseError("state_database_error")
            return real_transition(oid, expected, target, data)
        with patch.object(self.service.store, "transition", side_effect=fail_finalization):
            with self.assertRaises(BaseError) as caught:
                self.service.apply(op["id"])
        self.assertTrue(caught.exception.unknown)
        self.assertEqual(self.service.store.get(op["id"])["state"], "unknown")
        self.assertEqual(self.feishu.writes, 1)
        self.service.verify_write(op["id"])
        self.assertEqual(self.feishu.writes, 1)

    def test_completed_publish_and_failed_database_finalization_are_unknown(self):
        op = self.publish()
        real_transition = self.service.store.transition
        def fail_finalization(oid, expected, target, data=None):
            if target == "submitted":
                raise BaseError("state_database_error")
            return real_transition(oid, expected, target, data)
        with patch.object(self.service.store, "transition", side_effect=fail_finalization):
            with self.assertRaises(BaseError) as caught:
                self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.assertTrue(caught.exception.unknown)
        self.assertEqual(self.service.store.get(op["id"])["state"], "unknown")
        self.service.publish_verify(op["id"])
        self.assertEqual(self.yxer.submits, 1)

    def test_request_key_bad_type_fails_before_dryrun(self):
        with self.assertRaisesRegex(BaseError, "idempotency_key_required"):
            self.service.draft("rec_EXAMPLE", "标题", "正文", {})
        self.assertEqual(self.service.store.all_operations(), [])

    def test_prepared_approval_requires_the_frozen_operator(self):
        op = self.plan()
        self.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
        with self.assertRaises(BaseError):
            self.service.approve(op["id"], op["confirmation_digest"], True)
        self.assertEqual(self.service.store.get(op["id"])["state"], "prepared")

    def test_publish_verify_does_not_reveal_other_operator_material(self):
        op = self.publish()
        self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.feishu.user["open_id"] = "ou_OTHER_EXAMPLE"
        with self.assertRaises(BaseError):
            self.service.publish_verify(op["id"])

    def test_submitted_retry_still_checks_exact_confirmation(self):
        op = self.publish()
        self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        with self.assertRaises(BaseError):
            self.service.publish_submit(op["id"], "wrong-digest", True)

    def test_same_draft_request_after_apply_returns_original_acknowledgement(self):
        op = self.plan()
        self.service.approve(op["id"], op["confirmation_digest"], True)
        self.service.apply(op["id"])
        repeated = self.plan()
        self.assertEqual((repeated["id"], repeated["state"]), (op["id"], "applied"))
        self.assertEqual(self.feishu.writes, 1)

    def test_rich_text_native_cells_are_preserved_without_repr_or_dropped_runs(self):
        self.assertEqual(text({"text": "正文\n下一段"}), "正文\n下一段")
        self.assertEqual(text([{"text": "正文"}, {"text": "\n下一段"}]), "正文\n下一段")
        with self.assertRaises(BaseError):
            text([{"attachment": "EXAMPLE"}])

    def test_draft_based_on_stale_source_does_not_produce_a_write_plan(self):
        source = self.service.source_record("rec_EXAMPLE")
        self.feishu.row["fields"]["正文"] = "另一位写入者的新原文"
        with self.assertRaisesRegex(BaseError, "feishu_source_changed"):
            self.service.draft("rec_EXAMPLE", "标题", "旧草稿", "source-audit-key", source["source_fingerprint"])
        self.assertEqual(self.service.store.all_operations(), [])

    def test_same_source_bound_request_after_apply_reuses_original_ack(self):
        source = self.service.source_record("rec_EXAMPLE")
        op = self.service.draft("rec_EXAMPLE", "标题", "新稿", "source-audit-key", source["source_fingerprint"])
        self.service.approve(op["id"], op["confirmation_digest"], True)
        self.service.apply(op["id"])
        repeated = self.service.draft("rec_EXAMPLE", "标题", "新稿", "source-audit-key", source["source_fingerprint"])
        self.assertEqual((repeated["id"], repeated["state"]), (op["id"], "applied"))
        self.assertEqual(self.feishu.writes, 1)

    def test_publish_prepare_retry_after_stage_change_keeps_original_job(self):
        op = self.publish()
        self.feishu.row["fields"]["阶段"] = ["已审核"]
        repeated = self.publish()
        self.assertEqual(repeated["id"], op["id"])
        self.assertEqual(self.yxer.uploads, 1)

    def test_expiry_during_remote_revalidation_stops_submit(self):
        op = self.publish()
        real_clock = __import__("time").time
        times = [op["data"]["prepared_at"] + 1700]
        self.yxer.revalidate_hook = lambda: times.append(op["data"]["prepared_at"] + 1900)
        with patch("contentbase.service.time.time", side_effect=lambda: times[-1]):
            with self.assertRaises(BaseError):
                self.service.publish_submit(op["id"], op["confirmation_digest"], True, op["data"]["media_review_digest"])
        self.assertEqual(self.yxer.submits, 0)

    def test_inspect_filters_before_pagination(self):
        owned = self.plan()
        for i in range(201):
            other = copy.deepcopy(owned["data"])
            other["plan"]["identity"] = {"open_id": "ou_OTHER_EXAMPLE", "tenant_key": "TENANT_EXAMPLE"}
            self.service.store.create("draft", "other-key-%04d" % i, {"i": i}, other)
        self.assertEqual([op["id"] for op in self.service.inspect()], [owned["id"]])

    def test_metric_timestamp_and_identity_are_not_empty_slots(self):
        for metrics in (
            {"platform": None, "work_id": "WORK_EXAMPLE", "observed_at": "2026-01-01T00:00:00Z", "values": {}},
            {"platform": "平台", "work_id": "", "observed_at": "2026-01-01T00:00:00Z", "values": {}},
            {"platform": "平台", "work_id": "WORK_EXAMPLE", "observed_at": "yesterday", "values": {}},
            {"platform": "平台", "work_id": "WORK_EXAMPLE", "observed_at": "2026-01-01", "values": {}},
            {"platform": "平台", "work_id": "WORK_EXAMPLE", "observed_at": "2026-01-01T00:00:00Z", "values": {"views": math.nan}},
        ):
            with self.subTest(metrics=metrics):
                with self.assertRaises(BaseError):
                    self.service.review("rec_EXAMPLE", metrics, "观察结论", "review-audit-key")

    def test_state_directory_never_targets_a_home_or_root_directory(self):
        for root in (Path("/"), Path.home()):
            with self.subTest(root=str(root)):
                with patch("contentbase.service.Store") as store:
                    with self.assertRaises(BaseError):
                        Service(CONFIG, root, self.feishu, self.yxer)
                    store.assert_not_called()
