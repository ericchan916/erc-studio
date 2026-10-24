"""Offline fixtures exercise real argv/payload contracts; no vendor calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contentbase.errors import BaseError
from contentbase.feishu import Feishu
from contentbase.yxer import Yxer


CONFIG = {"feishu": {"cli": "lark-cli", "profile": "fixture-profile", "base_token": "BASEEXAMPLE", "table_id": "tblEXAMPLE", "fields": {"title": "标题", "body": "正文", "stage": "阶段"}}, "yxer": {"cli": "yxer"}}
SCHEMA = [{"name": "标题", "type": "text"}, {"name": "正文", "type": "text"}, {"name": "阶段", "type": "select", "multiple": False, "options": [{"name": "待审核"}, {"name": "已审核"}]}, {"name": "关联", "type": "link"}]
RESOURCE = {"key": "resource-example", "size": 120, "width": 1200, "height": 1600, "name": "example.png"}


def ok(data):
    return {"ok": True, "identity": "user", "data": copy.deepcopy(data)}


class FeishuFixture:
    def __init__(self):
        self.calls = []
        self.identity = {"open_id": "ou_example", "tenant_key": "tenant-example"}
        self.fields = {"标题": "示例", "正文": "原文", "阶段": ["待审核"], "关联": []}
        self.schema = copy.deepcopy(SCHEMA)
        self.write_failure = False
        self.readback_wrong = False
        self.wrote = False
        self.rich_text = False
        self.duplicate_page = False
        self.has_more_invalid = False
        self.ignored_fields = False

    def __call__(self, argv, *, timeout=60, write=False):
        self.calls.append((list(argv), write))
        assert argv[:3] == ["lark-cli", "--profile", "fixture-profile"]
        assert argv[-4:] == ["--as", "user", "--format", "json"]
        if argv[3:5] == ["api", "GET"]:
            return ok(self.identity)
        action = argv[4]
        if action == "+base-get":
            return ok({"name": "示例内容库"})
        if action == "+table-list":
            return ok({"tables": [{"table_id": "tblEXAMPLE", "name": "内容"}]})
        if action == "+table-get":
            return ok({"table": {"id": "tblEXAMPLE", "name": "内容", "primary_field": "FIELD_EXAMPLE"}, "fields": self.schema, "views": []})
        if action == "+field-list":
            return ok({"fields": self.schema})
        if action == "+record-list":
            offset = int(argv[argv.index("--offset") + 1])
            rid = "recFIRST" if offset == 0 or self.duplicate_page else "recSECOND"
            return ok({"fields": list(self.fields), "data": [list(self.fields.values())], "record_id_list": [rid], "has_more": "false" if self.has_more_invalid else offset == 0})
        if action == "+record-get":
            rid = argv[argv.index("--record-id") + 1]
            fields = copy.deepcopy(self.fields)
            if self.wrote and self.readback_wrong:
                fields["正文"] = "错误读回"
            if self.wrote and self.rich_text:
                fields["正文"] = [{"type": "text", "text": fields["正文"]}]
            return ok({"fields": list(fields), "data": [list(fields.values())], "record_id_list": [rid]})
        if action == "+record-upsert":
            if "--dry-run" in argv:
                assert not write
                return ok({"dry_run": True})
            assert write
            if self.write_failure:
                raise BaseError("transport_failed", unknown=True)
            self.fields.update(json.loads(argv[argv.index("--json") + 1]))
            self.wrote = True
            return ok({"ignored_fields": ["正文"]} if self.ignored_fields else {})
        raise AssertionError(action)


class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.fake = FeishuFixture()
        self.adapter = Feishu(CONFIG, self.fake)

    def test_binding_is_only_existing_target_reads(self):
        bound = self.adapter.binding()
        self.assertEqual(bound["table"]["id"], "tblEXAMPLE")
        self.assertEqual([c[0][4] for c in self.fake.calls], ["+base-get", "+table-get", "+field-list"])
        self.assertFalse(any(write for argv, write in self.fake.calls))

    def test_binding_missing_field_is_not_added(self):
        self.fake.schema = [f for f in self.fake.schema if f["name"] != "正文"]
        with self.assertRaisesRegex(BaseError, "feishu_field_mapping_missing"):
            self.adapter.binding()
        self.assertFalse(any("+field-create" in argv for argv, write in self.fake.calls))

    def test_identity_uses_user_api_and_returns_no_token(self):
        self.fake.identity["token"] = "YOUR_SYNTHETIC_CREDENTIAL"
        self.assertEqual(self.adapter.identity(), {"open_id": "ou_example", "tenant_key": "tenant-example"})
        self.assertIn("/open-apis/authen/v1/user_info", self.fake.calls[0][0])

    def test_identity_rejects_invalid_identity(self):
        self.fake.identity["open_id"] = "bot-identity"
        with self.assertRaisesRegex(BaseError, "feishu_identity_unverified"):
            self.adapter.identity()

    def test_records_follow_all_pages(self):
        rows = self.adapter.records()
        self.assertEqual([r["record_id"] for r in rows], ["recFIRST", "recSECOND"])
        self.assertEqual(rows[0]["fields"]["正文"], "原文")

    def test_records_reject_duplicate_pages(self):
        self.fake.duplicate_page = True
        with self.assertRaisesRegex(BaseError, "feishu_pagination_invalid"):
            self.adapter.records()

    def test_records_require_boolean_has_more(self):
        self.fake.has_more_invalid = True
        with self.assertRaisesRegex(BaseError, "feishu_pagination_invalid"):
            self.adapter.records()

    def test_plan_is_read_and_real_dryrun_then_apply_readback(self):
        plan = self.adapter.plan_update("recEXAMPLE", {"正文": "新稿", "阶段": ["已审核"]})
        self.assertTrue({"binding", "identity", "before_hash", "schema_hash"} <= plan.keys())
        self.assertFalse(any(write for argv, write in self.fake.calls))
        self.assertIn("--dry-run", self.fake.calls[-1][0])
        after = self.adapter.apply_update(plan)
        self.assertEqual(after["fields"]["正文"], "新稿")
        self.assertEqual(sum(write for argv, write in self.fake.calls), 1)
        written = [argv for argv, write in self.fake.calls if write][0]
        self.assertEqual(json.loads(written[written.index("--json") + 1]), {"正文": "新稿", "阶段": ["已审核"]})

    def test_rich_text_readback_matches_exact_submitted_text(self):
        plan = self.adapter.plan_update("recEXAMPLE", {"正文": "新稿\n第二段"})
        self.fake.rich_text = True
        result = self.adapter.apply_update(plan)
        self.assertEqual(result["fields"]["正文"][0]["text"], "新稿\n第二段")

    def test_identity_change_prevents_write(self):
        plan = self.adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
        self.fake.identity["open_id"] = "ou_other"
        with self.assertRaisesRegex(BaseError, "feishu_identity_changed"):
            self.adapter.apply_update(plan)
        self.assertFalse(self.fake.wrote)

    def test_record_change_prevents_write(self):
        plan = self.adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
        self.fake.fields["标题"] = "外部修改"
        with self.assertRaisesRegex(BaseError, "feishu_record_changed"):
            self.adapter.apply_update(plan)
        self.assertFalse(self.fake.wrote)

    def test_schema_change_prevents_write(self):
        plan = self.adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
        self.fake.schema.append({"name": "新字段", "type": "text"})
        with self.assertRaisesRegex(BaseError, "feishu_schema_changed"):
            self.adapter.apply_update(plan)
        self.assertFalse(self.fake.wrote)

    def test_changed_plan_prevents_write(self):
        plan = self.adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
        plan["fields"]["正文"] = "修改稿"
        with self.assertRaisesRegex(BaseError, "feishu_plan_changed"):
            self.adapter.apply_update(plan)
        self.assertFalse(self.fake.wrote)

    def test_text_select_link_validation(self):
        for fields in ({"正文": []}, {"阶段": "已审核"}, {"阶段": ["任意值"]}, {"阶段": ["待审核", "已审核"]}, {"关联": [{"id": "invented"}]}, {"不存在": "值"}):
            with self.subTest(fields=fields), self.assertRaises(BaseError):
                self.adapter.plan_update("recEXAMPLE", fields)
        self.adapter.plan_update("recEXAMPLE", {"关联": [{"id": "recRELATED"}]})

    def test_verify_update_proves_current_target_without_replay(self):
        plan = self.adapter.plan_update("recEXAMPLE", {"正文": "新稿", "阶段": ["已审核"], "关联": [{"id": "recRELATED"}]})
        self.fake.fields.update({"正文": "新稿", "阶段": [{"name": "已审核"}], "关联": [{"id": "recRELATED", "name": "标签"}]})
        self.fake.wrote = True
        self.fake.rich_text = True
        self.fake.calls.clear()
        current = self.adapter.verify_update(plan)
        self.assertEqual(current["record_id"], "recEXAMPLE")
        self.assertEqual(len(self.fake.calls), 3)
        self.assertFalse(any(write or "--dry-run" in argv for argv, write in self.fake.calls))
        self.assertFalse(any("+record-upsert" in argv for argv, write in self.fake.calls))

    def test_verify_update_wrong_fields_remain_unknown(self):
        plan = self.adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
        self.fake.calls.clear()
        with self.assertRaises(BaseError) as error:
            self.adapter.verify_update(plan)
        self.assertTrue(error.exception.unknown)
        self.assertFalse(any(write or "--dry-run" in argv for argv, write in self.fake.calls))

    def test_verify_update_checks_identity_binding_fields_and_schema(self):
        for failure in ("identity", "binding", "fields", "schema"):
            with self.subTest(failure=failure):
                fake = FeishuFixture()
                adapter = Feishu(CONFIG, fake)
                plan = adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
                fake.fields["正文"] = "新稿"
                if failure == "identity":
                    fake.identity["open_id"] = "ou_other"
                elif failure == "binding":
                    plan["binding"]["table_id"] = "tblOTHER"
                elif failure == "fields":
                    plan["fields"]["正文"] = "伪造稿"
                else:
                    fake.schema.append({"name": "新字段", "type": "text"})
                fake.calls.clear()
                with self.assertRaises(BaseError) as error:
                    adapter.verify_update(plan)
                self.assertTrue(error.exception.unknown)
                self.assertFalse(any(write or "--dry-run" in argv for argv, write in fake.calls))

    def test_timeout_and_wrong_readback_are_unknown(self):
        for failure in ("write_failure", "readback_wrong", "ignored_fields"):
            with self.subTest(failure=failure):
                fake = FeishuFixture()
                adapter = Feishu(CONFIG, fake)
                plan = adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
                setattr(fake, failure, True)
                with self.assertRaises(BaseError) as error:
                    adapter.apply_update(plan)
                self.assertTrue(error.exception.unknown)
                self.assertEqual(sum(write for argv, write in fake.calls), 1)


class YxerFixture:
    def __init__(self):
        self.calls = []
        self.platform = "小红书"
        self.account = {"id": "account-example", "platformName": self.platform, "platformAccountName": "示例账号", "status": 1, "isLock": False, "isFreeze": False, "isOperate": True, "accessToken": "YOUR_SYNTHETIC_CREDENTIAL"}
        self.music = {"yixiaoerId": "music-example", "yixiaoerName": "示例音乐", "provider": "native"}
        self.category = {"yixiaoerId": "root-example", "yixiaoerName": "示例分区", "children": [{"yixiaoerId": "leaf-example", "yixiaoerName": "示例子分区"}]}
        self.channel = "cloud"
        self.receipt = {"taskSetId": "task-example"}
        self.publish_failure = False
        self.schema = {"businessFields": {"title": {"type": "string", "maxLength": 20, "required": True}, "description": {"type": "string", "maxLength": 5000}, "formType": {"type": "string", "default": "task"}, "images": {"type": "array", "maxItems": 9}, "video": {"type": "object"}, "cover": {"type": "object"}, "music": {"type": "object"}, "category": {"type": "array"}, "tags": {"type": "array", "default": []}, "visibleType": {"type": "integer", "default": 1, "enum": [1, 2]}}}

    def __call__(self, argv, *, timeout=60, write=False):
        self.calls.append((list(argv), write))
        assert argv[0] == "yxer" and argv[-1] == "--json"
        args = argv[1:-1]
        if args[0] == "doctor":
            return ok({"apiKey": "YOUR_SYNTHETIC_CREDENTIAL", "publishChannelReadiness": {"cloud": {"configured": True, "clientId": "YOUR_SYNTHETIC_CREDENTIAL"}, "local": {"configured": False}}})
        if args[:2] == ["accounts", "list"]:
            return ok([self.account])
        if args[0] == "prepare":
            return ok({"platform": args[1]})
        if args[:2] == ["schema", "get"]:
            return ok(self.schema)
        if args[:2] == ["query", "music"]:
            return ok({"dataList": [self.music]})
        if args[:2] == ["query", "categories"]:
            return ok({"dataList": [self.category]})
        if args[0] == "upload":
            resource = copy.deepcopy(RESOURCE)
            if args[args.index("--usage") + 1] == "video":
                resource["duration"] = 15
            return ok(resource)
        if args[0] == "validate":
            return ok({"valid": True})
        if args[0] == "publish":
            if "--dry-run" in args:
                assert not write
                return ok({"meta": {"effectivePublishChannel": self.channel, "requestHash": "request-example"}, "apiKey": "YOUR_SYNTHETIC_CREDENTIAL"})
            assert write
            if self.publish_failure:
                raise BaseError("cli_transport_unknown", unknown=True)
            return ok(self.receipt)
        if args[:2] == ["query", "details"]:
            return ok({"id": args[2], "token": "YOUR_SYNTHETIC_CREDENTIAL", "tasks": [{"platformName": self.account["platformName"], "publishId": "work-example", "openUrl": "https://example.invalid/work-example", "cookie": "YOUR_SYNTHETIC_CREDENTIAL", "errorMessage": "token=YOUR_SYNTHETIC_CREDENTIAL"}]})
        raise AssertionError(args)


class YxerTests(unittest.TestCase):
    def setUp(self):
        self.fake = YxerFixture()
        self.adapter = Yxer(CONFIG, self.fake)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "payload.json"

    def image_payload(self, **changes):
        args = {"platform": self.fake.account["platformName"], "kind": "imageText", "channel": "cloud", "account_id": "account-example", "title": "示例标题", "body": "原文保持不变", "resources": [{"usage": "image", "resource": RESOURCE}], "fields": {}}
        args.update(changes)
        return self.adapter.prepare_payload(**args)

    def preflight(self, payload):
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.adapter.preflight(payload, self.path)

    def test_doctor_and_accounts_do_not_expose_credentials(self):
        self.assertEqual(self.adapter.doctor(), {"channels": {"cloud": {"configured": True}, "local": {"configured": False}}})
        self.assertNotIn("accessToken", self.adapter.accounts()[0])

    def test_image_payload_keeps_complete_resources_and_defaults(self):
        payload = self.image_payload()
        form = payload["publishArgs"]["accountForms"][0]
        self.assertEqual(form["images"][0], RESOURCE)
        self.assertEqual(form["images"], form["contentPublishForm"]["images"])
        self.assertEqual(form["coverKey"], RESOURCE["key"])
        self.assertEqual(form["contentPublishForm"]["visibleType"], 1)
        self.assertEqual(form["contentPublishForm"]["description"], "原文保持不变")

    def test_image_limit_and_text_length_are_not_truncated(self):
        for values in ({"title": "很" * 21}, {"resources": [{"usage": "image", "resource": RESOURCE}] * 10}):
            with self.subTest(values=values), self.assertRaises(BaseError):
                self.image_payload(**values)

    def test_upload_checks_magic_and_preserves_resource(self):
        path = Path(self.temp.name) / "example.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 12)
        self.assertEqual(self.adapter.upload(path, "小红书", "image"), RESOURCE)
        path.write_bytes(b"not-an-image")
        with self.assertRaisesRegex(BaseError, "yxer_upload_media_invalid"):
            self.adapter.upload(path, "小红书", "image")
        self.assertEqual(sum("upload" in argv for argv, write in self.fake.calls), 1)

    def test_resource_metadata_is_required(self):
        incomplete = {"key": "fake"}
        with self.assertRaisesRegex(BaseError, "yxer_resource_metadata_missing"):
            self.image_payload(resources=[{"usage": "image", "resource": incomplete}])

    def test_douyin_music_requires_real_query_receipt(self):
        self.fake.account["platformName"] = "抖音"
        with self.assertRaisesRegex(BaseError, "yxer_music_required"):
            self.image_payload()
        with self.assertRaisesRegex(BaseError, "yxer_choice_receipt_required"):
            self.image_payload(fields={"music": self.fake.music})
        values = self.adapter.choices("account-example", "imageText", "music", "示例")
        payload = self.image_payload(fields={"music": values[0]})
        self.assertEqual(payload["publishArgs"]["accountForms"][0]["contentPublishForm"]["music"], self.fake.music)
        changed = copy.deepcopy(values[0])
        changed["provider"] = "forged"
        with self.assertRaisesRegex(BaseError, "yxer_choice_receipt_required"):
            self.image_payload(fields={"music": changed})

    def test_default_music_does_not_bypass_query_receipt(self):
        self.fake.account["platformName"] = "抖音"
        self.fake.schema["businessFields"]["music"]["default"] = self.fake.music
        with self.assertRaisesRegex(BaseError, "yxer_choice_receipt_required"):
            self.image_payload()

    def test_choice_receipt_is_account_bound_and_expires(self):
        self.fake.account["platformName"] = "抖音"
        values = self.adapter.choices("account-example", "imageText", "music")
        with patch("contentbase.yxer.time.monotonic", return_value=10**12), self.assertRaisesRegex(BaseError, "yxer_choice_receipt_required"):
            self.image_payload(fields={"music": values[0]})

    def test_video_requires_cover_and_bilibili_category_and_tags(self):
        self.fake.account["platformName"] = "哔哩哔哩"
        video = {**RESOURCE, "duration": 10}
        kwargs = {"platform": "哔哩哔哩", "kind": "video", "resources": [{"usage": "video", "resource": video}, {"usage": "cover", "resource": RESOURCE}]}
        with self.assertRaisesRegex(BaseError, "yxer_bilibili_fields_required"):
            self.image_payload(**kwargs)
        categories = self.adapter.choices("account-example", "video", "category")
        payload = self.image_payload(**kwargs, fields={"category": categories[0], "tags": ["示例"]})
        af = payload["publishArgs"]["accountForms"][0]
        self.assertEqual(af["video"], af["contentPublishForm"]["video"])
        self.assertEqual(af["cover"], af["contentPublishForm"]["cover"])
        with self.assertRaisesRegex(BaseError, "yxer_video_cover_required"):
            self.image_payload(**{**kwargs, "resources": [{"usage": "video", "resource": video}]})

    def test_preflight_is_explicit_channel_and_never_publishes(self):
        payload = self.image_payload()
        result = self.preflight(payload)
        self.assertEqual(result["channel"], "cloud")
        self.assertNotIn("apiKey", result["dry_run"])
        self.assertFalse(any(write for argv, write in self.fake.calls))
        dry = [argv for argv, write in self.fake.calls if "--dry-run" in argv][0]
        self.assertIn("--publish-channel", dry)
        self.assertNotIn("--auto-fallback-local", dry)

    def test_channel_mismatch_prevents_submit(self):
        payload = self.image_payload()
        self.fake.channel = "local"
        with self.assertRaisesRegex(BaseError, "yxer_channel_mismatch"):
            self.preflight(payload)
        with self.assertRaisesRegex(BaseError, "yxer_preflight_required"):
            self.adapter.submit(payload, self.path)

    def test_revalidate_rebuilds_receipts_in_new_process_without_upload(self):
        self.fake.account["platformName"] = "抖音"
        values = self.adapter.choices("account-example", "imageText", "music", "原查询词")
        payload = self.image_payload(fields={"music": values[0]})
        self.preflight(payload)
        frozen = self.path.read_bytes()
        new_process = Yxer(CONFIG, self.fake)
        start = len(self.fake.calls)
        receipt = new_process.revalidate(payload, self.path, {"music_query": "原查询词", "music_choice": 0})
        self.assertEqual(self.path.read_bytes(), frozen)
        self.assertEqual(receipt["channel"], "cloud")
        calls = self.fake.calls[start:]
        self.assertFalse(any(write for argv, write in calls))
        self.assertFalse(any("upload" in argv for argv, write in calls))
        self.assertTrue(any("schema" in argv for argv, write in calls))
        query = next(argv for argv, write in calls if "music" in argv)
        self.assertEqual(query[query.index("--query") + 1], "原查询词")
        self.assertEqual(new_process.submit(payload, self.path)["taskSetId"], "task-example")

    def test_revalidate_rejects_changed_native_dynamic_selection(self):
        self.fake.account["platformName"] = "抖音"
        values = self.adapter.choices("account-example", "imageText", "music")
        payload = self.image_payload(fields={"music": values[0]})
        self.preflight(payload)
        self.fake.music["provider"] = "vendor-changed"
        with self.assertRaisesRegex(BaseError, "yxer_dynamic_selection_changed"):
            self.adapter.revalidate(payload, self.path)
        with self.assertRaisesRegex(BaseError, "yxer_preflight_required"):
            self.adapter.submit(payload, self.path)
        self.assertFalse(any(write for argv, write in self.fake.calls))

    def test_revalidate_rebuilds_bilibili_category_receipt(self):
        self.fake.account["platformName"] = "哔哩哔哩"
        values = self.adapter.choices("account-example", "video", "category")
        resources = [{"usage": "video", "resource": {**RESOURCE, "duration": 10}}, {"usage": "cover", "resource": RESOURCE}]
        payload = self.image_payload(kind="video", resources=resources, fields={"category": values[0], "tags": ["示例"]})
        self.preflight(payload)
        new_process = Yxer(CONFIG, self.fake)
        new_process.revalidate(payload, self.path, {"category_choice": 0, "tags": ["示例"]})
        self.assertEqual(new_process.submit(payload, self.path)["taskSetId"], "task-example")

    def test_revalidate_never_refreshes_an_attempted_submission(self):
        payload = self.image_payload()
        self.preflight(payload)
        self.adapter.submit(payload, self.path)
        count = len(self.fake.calls)
        with self.assertRaises(BaseError) as error:
            self.adapter.revalidate(payload, self.path)
        self.assertTrue(error.exception.unknown)
        self.assertEqual(len(self.fake.calls), count)

    def test_revalidate_checks_frozen_file_before_vendor_queries(self):
        payload = self.image_payload()
        self.preflight(payload)
        self.path.write_text("{}", encoding="utf-8")
        count = len(self.fake.calls)
        with self.assertRaisesRegex(BaseError, "yxer_payload_file_changed"):
            Yxer(CONFIG, self.fake).revalidate(payload, self.path)
        self.assertEqual(len(self.fake.calls), count)

    def test_submit_returns_acceptance_not_public_visibility(self):
        payload = self.image_payload()
        self.preflight(payload)
        result = self.adapter.submit(payload, self.path)
        self.assertEqual(result, {"taskSetId": "task-example", "accepted": True, "publicVisible": None, "vendor_receipt": {"taskSetId": "task-example"}})
        with self.assertRaisesRegex(BaseError, "yxer_submission_already_attempted"):
            self.adapter.submit(payload, self.path)
        self.assertEqual(sum(write for argv, write in self.fake.calls), 1)

    def test_submit_preserves_redacted_native_receipt(self):
        payload = self.image_payload()
        self.preflight(payload)
        self.fake.receipt = {"taskSetId": "task-example", "meta": {"requestHash": "request-example", "apiKey": "YOUR_SYNTHETIC_CREDENTIAL"}, "taskSet": {"id": "task-example", "stageStatus": "accepted", "cookie": "YOUR_SYNTHETIC_CREDENTIAL"}}
        result = self.adapter.submit(payload, self.path)
        self.assertEqual(result["vendor_receipt"]["meta"], {"requestHash": "request-example"})
        self.assertEqual(result["vendor_receipt"]["taskSet"], {"id": "task-example", "stageStatus": "accepted"})
        self.assertNotIn("YOUR_SYNTHETIC_CREDENTIAL", json.dumps(result))
        self.assertIsNone(result["publicVisible"])

    def test_missing_task_id_still_attaches_redacted_native_receipt(self):
        payload = self.image_payload()
        self.preflight(payload)
        self.fake.receipt = {"meta": {"requestHash": "request-example", "token": "YOUR_SYNTHETIC_CREDENTIAL"}, "accepted": True}
        with self.assertRaises(BaseError) as error:
            self.adapter.submit(payload, self.path)
        self.assertTrue(error.exception.unknown)
        self.assertEqual(error.exception.vendor_receipt, {"meta": {"requestHash": "request-example"}, "accepted": True})
        self.assertNotIn("YOUR_SYNTHETIC_CREDENTIAL", json.dumps(error.exception.vendor_receipt))

    def test_submit_rechecks_account(self):
        payload = self.image_payload()
        self.preflight(payload)
        self.fake.account["status"] = 2
        with self.assertRaisesRegex(BaseError, "yxer_account_unavailable"):
            self.adapter.submit(payload, self.path)
        self.assertFalse(any(write for argv, write in self.fake.calls))

    def test_file_change_prevents_submit(self):
        payload = self.image_payload()
        self.preflight(payload)
        self.path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(BaseError, "yxer_payload_file_changed"):
            self.adapter.submit(payload, self.path)
        self.assertFalse(any(write for argv, write in self.fake.calls))

    def test_forged_dynamic_payload_is_blocked_even_with_valid_file(self):
        self.fake.account["platformName"] = "抖音"
        values = self.adapter.choices("account-example", "imageText", "music")
        payload = self.image_payload(fields={"music": values[0]})
        payload["publishArgs"]["accountForms"][0]["contentPublishForm"]["music"]["yixiaoerId"] = "forged"
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(BaseError, "yxer_choice_receipt_required"):
            self.adapter.preflight(payload, self.path)

    def test_missing_receipt_and_timeout_do_not_retry(self):
        for failure in ("missing", "timeout"):
            with self.subTest(failure=failure):
                fake = YxerFixture()
                adapter = Yxer(CONFIG, fake)
                payload = adapter.prepare_payload("小红书", "imageText", "cloud", "account-example", "示例", "原文", [{"usage": "image", "resource": RESOURCE}], {})
                self.path.write_text(json.dumps(payload), encoding="utf-8")
                adapter.preflight(payload, self.path)
                if failure == "missing":
                    fake.receipt = {}
                else:
                    fake.publish_failure = True
                with self.assertRaises(BaseError) as error:
                    adapter.submit(payload, self.path)
                self.assertTrue(error.exception.unknown)
                with self.assertRaises(BaseError):
                    adapter.submit(payload, self.path)
                self.assertEqual(sum(write for argv, write in fake.calls), 1)

    def test_details_redacts_credentials_and_preserves_unknown_visibility(self):
        details = self.adapter.details("task-example")
        text = json.dumps(details)
        self.assertNotIn("YOUR_SYNTHETIC_CREDENTIAL", text)
        self.assertEqual(details["tasks"][0]["publishId"], "work-example")
        self.assertIsNone(details["publicVisible"])


if __name__ == "__main__":
    unittest.main()
