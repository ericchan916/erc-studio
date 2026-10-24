"""Independent protocol regressions based on installed CLI help and read-only probes."""
import copy
import json
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from contentbase.errors import BaseError
from contentbase.feishu import Feishu
from contentbase.runner import Runner
from contentbase.yxer import Yxer
import test_adapters as adapters_fixture


class RunnerAudit(unittest.TestCase):
    def test_native_stderr_validation_is_actionable_and_pre_execution(self):
        packet = {"ok": False, "error": {"type": "validation", "subtype": "missing_required_flag", "message": "EXAMPLE_PRIVATE_MESSAGE"}}
        result = subprocess.CompletedProcess(["EXAMPLE_CLI"], 2, "", json.dumps(packet))
        with patch("contentbase.runner.subprocess.run", return_value=result), self.assertRaises(BaseError) as caught:
            Runner()(["EXAMPLE_CLI"], write=True)
        self.assertEqual(caught.exception.code, "cli_validation")
        self.assertFalse(caught.exception.unknown)
        self.assertNotIn("EXAMPLE_PRIVATE_MESSAGE", str(caught.exception))

    def test_native_stderr_authorization_has_distinct_category(self):
        packet = {"ok": False, "error": {"type": "authorization", "message": "YOUR_SYNTHETIC_CREDENTIAL", "missing_scopes": ["base:record:read"]}}
        result = subprocess.CompletedProcess(["EXAMPLE_CLI"], 1, "", json.dumps(packet))
        with patch("contentbase.runner.subprocess.run", return_value=result), self.assertRaises(BaseError) as caught:
            Runner()(["EXAMPLE_CLI"], write=True)
        self.assertEqual(caught.exception.code, "cli_auth")
        self.assertFalse(caught.exception.unknown)
        self.assertNotIn("YOUR_SYNTHETIC_CREDENTIAL", str(caught.exception))

    def test_valid_stderr_success_must_not_mask_invalid_stdout(self):
        result = subprocess.CompletedProcess(["EXAMPLE_CLI"], 0, "broken stdout", json.dumps({"ok": True, "data": {"accepted": True}}))
        with patch("contentbase.runner.subprocess.run", return_value=result), self.assertRaises(BaseError) as caught:
            Runner()(["EXAMPLE_CLI"], write=True)
        self.assertTrue(caught.exception.unknown)

    def test_rate_limit_and_provider_failure_are_actionable_but_write_unknown(self):
        for error, expected in [({"type": "rate_limit", "code": "EXAMPLE_RATE_LIMIT"}, "cli_rate_limit"), ({"type": "api_error", "code": "EXAMPLE_PROVIDER_UNAVAILABLE", "category": "network_error"}, "cli_provider_unavailable")]:
            with self.subTest(expected=expected):
                result = subprocess.CompletedProcess(["EXAMPLE_CLI"], 1, json.dumps({"ok": False, "error": error}), "")
                with patch("contentbase.runner.subprocess.run", return_value=result), self.assertRaises(BaseError) as caught:
                    Runner()(["EXAMPLE_CLI"], write=True)
                self.assertEqual(caught.exception.code, expected)
                self.assertTrue(caught.exception.unknown)


class SchemaAudit(unittest.TestCase):
    def test_known_table_after_default_first_50_binds_without_unrelated_listing(self):
        base = adapters_fixture.FeishuFixture()
        def run(argv, **kwargs):
            if "+table-list" in argv:
                return adapters_fixture.ok({"tables": [{"id": "tblOTHER%02d" % n} for n in range(50)], "total": 51})
            return base(argv, **kwargs)
        result = Feishu(adapters_fixture.CONFIG, run).binding()
        self.assertEqual(result["table"]["id"], "tblEXAMPLE")
        self.assertFalse(any("+table-list" in argv for argv, write in base.calls))
        self.assertFalse(any(write for argv, write in base.calls))

    def test_direct_table_wrong_or_conflicting_identity_never_reads_schema(self):
        for table in ({"id": "tblOTHER"}, {"id": "tblOTHER", "table_id": "tblEXAMPLE"}):
            base = adapters_fixture.FeishuFixture()
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                if "+table-get" in argv:
                    return adapters_fixture.ok({"table": table})
                return base(argv, **kwargs)
            with self.subTest(table=table), self.assertRaises(BaseError):
                Feishu(adapters_fixture.CONFIG, run).binding()
            self.assertFalse(any("+field-list" in argv for argv in calls))

    def test_direct_table_bad_packet_is_structured_and_does_not_fallback_to_list(self):
        for packet in ({}, {"table": None}, {"table": []}):
            base = adapters_fixture.FeishuFixture()
            def run(argv, **kwargs):
                if "+table-get" in argv:
                    return adapters_fixture.ok(packet)
                return base(argv, **kwargs)
            with self.subTest(packet=packet), self.assertRaisesRegex(BaseError, "feishu_tables_invalid"):
                Feishu(adapters_fixture.CONFIG, run).binding()
            self.assertFalse(any("+table-list" in argv for argv, write in base.calls))
    def test_native_bilibili_tag_item_limit_is_enforced(self):
        # Native 3.2.7 schema: tags maxItems=10; each string maxLength=20.
        spec = {"type": "array", "required": True, "maxItems": 10, "items": {"type": "string", "maxLength": 20}}
        with self.assertRaises(BaseError):
            Yxer._field_value(["标" * 21], spec)

    def test_native_object_array_does_not_accept_string_items(self):
        # Native shopping_cart is an array of object, not an array of string.
        with self.assertRaises(BaseError):
            Yxer._field_value(["EXAMPLE_NOT_AN_OBJECT"], {"type": "array", "items": {"type": "object"}})

    def test_nested_select_multiple_contract_is_honored(self):
        schema = [{"name": "标签", "type": "select", "property": {"multiple": True, "options": [{"name": "A"}, {"name": "B"}]}}]
        Feishu._validate_fields(schema, {"标签": ["A", "B"]})

    def test_schema_reads_past_default_first_page(self):
        fields = [{"name": "字段%03d" % i, "type": "text"} for i in range(201)]
        config = {"feishu": {"cli": "EXAMPLE_CLI", "profile": "EXAMPLE_PROFILE", "base_token": "EXAMPLE_BASE", "table_id": "tblEXAMPLE", "fields": {"body": "字段200"}}}
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            offset = int(argv[argv.index("--offset") + 1]) if "--offset" in argv else 0
            limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 100
            return {"ok": True, "identity": "user", "data": {"fields": fields[offset:offset + limit], "has_more": offset + limit < len(fields)}}
        actual = Feishu(config, run)._schema()
        self.assertEqual(len(actual), 201)
        self.assertTrue(any("--offset" in argv for argv in calls[1:]))


class ProviderFixture:
    def __init__(self):
        self.context = {"apiUrl": "https://example.invalid/api", "apiKeyPresent": True, "localPublishClientId": "EXAMPLE_LOCAL_CLIENT", "publishChannelReadiness": {"cloud": {"configured": True}, "local": {"configured": True, "clientId": "EXAMPLE_LOCAL_CLIENT"}}}
        self.rows = [{"id": "EXAMPLE_ACCOUNT", "platformName": "抖音", "platformAccountName": "示例账号", "status": 1, "isLock": False, "isFreeze": False, "isOperate": True, "authChannel": "EXAMPLE_CHANNEL", "platformType": 0, "principal": None, "operators": [{"id": "EXAMPLE_MEMBER", "userId": "EXAMPLE_USER", "name": "示例成员"}]}]

    def __call__(self, argv, **kwargs):
        if argv[1:2] == ["doctor"]:
            return {"ok": True, "data": copy.deepcopy(self.context)}
        if argv[1:3] == ["accounts", "list"]:
            return {"ok": True, "data": copy.deepcopy(self.rows)}
        raise AssertionError("unexpected fixture command")


class ProviderAudit(unittest.TestCase):
    def test_local_target_change_invalidates_provider_binding_without_exposing_id(self):
        fixture = ProviderFixture()
        adapter = Yxer({"yxer": {"cli": "yxer"}}, fixture)
        first = adapter.provider_binding()
        fixture.context["localPublishClientId"] = "EXAMPLE_NEW_LOCAL_CLIENT"
        fixture.context["publishChannelReadiness"]["local"]["clientId"] = "EXAMPLE_NEW_LOCAL_CLIENT"
        second = adapter.provider_binding()
        self.assertNotEqual(first, second)
        self.assertNotIn("EXAMPLE_LOCAL_CLIENT", json.dumps(first))
        self.assertTrue(first["limitations"])

    def test_account_operator_change_invalidates_binding_but_metrics_do_not(self):
        fixture = ProviderFixture()
        adapter = Yxer({"yxer": {"cli": "yxer"}}, fixture)
        first = adapter.provider_binding()
        fixture.rows[0]["fansTotal"] = 123
        fixture.rows[0]["operators"][0]["name"] = "显示名变更"
        self.assertEqual(first, adapter.provider_binding())
        fixture.rows[0]["operators"][0]["userId"] = "EXAMPLE_OTHER_USER"
        self.assertNotEqual(first, adapter.provider_binding())

    def test_unrelated_account_does_not_change_target_binding(self):
        fixture = ProviderFixture()
        adapter = Yxer({"yxer": {"cli": "yxer"}}, fixture)
        target = adapter.provider_binding("EXAMPLE_ACCOUNT")
        global_before = adapter.provider_binding()
        extra = copy.deepcopy(fixture.rows[0])
        extra["id"] = "EXAMPLE_UNRELATED_ACCOUNT"
        fixture.rows.append(extra)
        self.assertEqual(target, adapter.provider_binding("EXAMPLE_ACCOUNT"))
        self.assertNotEqual(global_before, adapter.provider_binding())
        self.assertEqual(target["account_count"], 1)
        self.assertEqual(target["operator_binding_count"], 1)

    def test_target_binding_tracks_operator_channel_and_client(self):
        for change in ("operator", "channel", "client"):
            with self.subTest(change=change):
                fixture = ProviderFixture()
                adapter = Yxer({"yxer": {"cli": "yxer"}}, fixture)
                target = adapter.provider_binding("EXAMPLE_ACCOUNT")
                if change == "operator":
                    fixture.rows[0]["operators"][0]["userId"] = "EXAMPLE_NEW_USER"
                elif change == "channel":
                    fixture.rows[0]["authChannel"] = "EXAMPLE_OTHER_CHANNEL"
                else:
                    fixture.context["localPublishClientId"] = "EXAMPLE_OTHER_CLIENT"
                self.assertNotEqual(target, adapter.provider_binding("EXAMPLE_ACCOUNT"))
                self.assertTrue(target["limitations"])

    def test_target_binding_requires_visible_exact_account(self):
        fixture = ProviderFixture()
        with self.assertRaisesRegex(BaseError, "yxer_account_missing"):
            Yxer({"yxer": {"cli": "yxer"}}, fixture).provider_binding("EXAMPLE_MISSING_ACCOUNT")

    def test_protocol_boolean_status_is_not_a_valid_numeric_account_status(self):
        fixture = ProviderFixture()
        fixture.rows[0]["status"] = True
        with self.assertRaises(BaseError):
            Yxer({"yxer": {"cli": "yxer"}}, fixture)._account("EXAMPLE_ACCOUNT", "抖音")

    def test_doctor_does_not_treat_string_false_as_configured(self):
        fixture = ProviderFixture()
        fixture.context["publishChannelReadiness"]["cloud"]["configured"] = "false"
        with self.assertRaises(BaseError):
            Yxer({"yxer": {"cli": "yxer"}}, fixture).doctor()


class StaticFieldsFixture(ProviderFixture):
    def __init__(self, platform="哔哩哔哩"):
        super().__init__()
        self.rows[0]["platformName"] = platform
        self.calls = []
        self.schema = {"businessFields": {
            "formType": {"type": "string", "required": True},
            "title": {"type": "string", "required": True, "maxLength": 80},
            "description": {"type": "string", "maxLength": 2000},
            "images": {"type": "array", "required": False, "maxItems": 2},
            "video": {"type": "object"}, "cover": {"type": "object"},
            "category": {"type": "array", "required": platform == "哔哩哔哩"},
            "music": {"type": "object"},
            "tags": {"type": "array", "required": platform == "哔哩哔哩", "maxItems": 10, "items": {"type": "string", "maxLength": 20}},
            "createType": {"type": "number", "required": True, "default": 1, "enum": [1, 2]}}}

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs.get("write", False)))
        if argv[1:2] == ["prepare"]:
            return {"ok": True, "data": {}}
        if argv[1:3] == ["schema", "get"]:
            return {"ok": True, "data": copy.deepcopy(self.schema)}
        if argv[1:3] == ["query", "categories"]:
            return {"ok": True, "data": {"dataList": [{"yixiaoerId": "EXAMPLE_CATEGORY", "yixiaoerName": "示例分区", "raw": {"id": "EXAMPLE_CATEGORY"}}]}}
        if argv[1:3] == ["query", "music"]:
            return {"ok": True, "data": {"dataList": [{"yixiaoerId": "EXAMPLE_MUSIC", "yixiaoerName": "示例音乐", "raw": {"id": "EXAMPLE_MUSIC"}}]}}
        return super().__call__(argv, **kwargs)


class StaticFieldAudit(unittest.TestCase):
    def make(self, platform="哔哩哔哩"):
        fixture = StaticFieldsFixture(platform)
        return fixture, Yxer({"yxer": {"cli": "yxer"}}, fixture)

    def test_static_validation_uses_no_uploaded_or_fabricated_resource(self):
        fixture, adapter = self.make()
        category = adapter.choices("EXAMPLE_ACCOUNT", "video", "category")[0]
        with patch.object(adapter, "_resource", side_effect=AssertionError("resource forbidden before upload")):
            result = adapter.validate_fields("哔哩哔哩", "video", "EXAMPLE_ACCOUNT", "示例", "正文", {"category": category, "tags": ["示例"]}, ["video", "cover"])
        self.assertEqual(result["media_count"], 2)
        self.assertTrue(all(not write and argv[1] not in ("upload", "validate", "publish") for argv, write in fixture.calls))

    def test_static_validation_checks_provider_defaults_and_required_fields(self):
        fixture, adapter = self.make("小红书")
        fixture.schema["businessFields"]["createType"]["default"] = 99
        with self.assertRaisesRegex(BaseError, "yxer_field_value_invalid"):
            adapter.validate_fields("小红书", "imageText", "EXAMPLE_ACCOUNT", "示例", "正文", {}, ["image"])
        fixture.schema["businessFields"]["createType"]["default"] = 1
        fixture.schema["businessFields"]["description"]["required"] = True
        with self.assertRaises(BaseError):
            adapter.validate_fields("小红书", "imageText", "EXAMPLE_ACCOUNT", "示例", "", {}, ["image"])
        self.assertFalse(any(write for argv, write in fixture.calls))

    def test_static_media_roles_and_counts_are_checked_before_upload(self):
        fixture, adapter = self.make("小红书")
        for usages in ([], ["image"] * 3, ["image", "cover"]):
            with self.subTest(usages=usages), self.assertRaises(BaseError):
                adapter.validate_fields("小红书", "imageText", "EXAMPLE_ACCOUNT", "示例", "正文", {}, usages)
        self.assertFalse(any(write or argv[1] == "upload" for argv, write in fixture.calls))

    def test_static_music_needs_native_cached_choice_receipt(self):
        fixture, adapter = self.make("抖音")
        with self.assertRaisesRegex(BaseError, "yxer_music_required"):
            adapter.validate_fields("抖音", "imageText", "EXAMPLE_ACCOUNT", "示例", "正文", {}, ["image"])
        value = {"yixiaoerId": "EXAMPLE_MUSIC", "yixiaoerName": "示例音乐", "raw": {"id": "EXAMPLE_MUSIC"}}
        with self.assertRaisesRegex(BaseError, "yxer_choice_receipt_required"):
            adapter.validate_fields("抖音", "imageText", "EXAMPLE_ACCOUNT", "示例", "正文", {"music": value}, ["image"])
        picked = adapter.choices("EXAMPLE_ACCOUNT", "imageText", "music")[0]
        adapter.validate_fields("抖音", "imageText", "EXAMPLE_ACCOUNT", "示例", "正文", {"music": picked}, ["image"])
        self.assertFalse(any(write for argv, write in fixture.calls))

    def test_service_bad_tag_fails_before_any_upload(self):
        from contentbase.service import Service
        fixture, adapter = self.make()
        class FeishuSource:
            def identity(self):
                return {"open_id": "ou_EXAMPLE", "tenant_key": "EXAMPLE_TENANT"}
            def record(self, record_id):
                return {"record_id": record_id, "id": record_id, "fields": {"标题": "示例", "正文": "正文"}}
        config = {"feishu": {"cli": "EXAMPLE_CLI", "profile": "EXAMPLE_PROFILE", "base_token": "EXAMPLE_BASE", "table_id": "tblEXAMPLE", "fields": {"title": "标题", "body": "正文"}}, "yxer": {"cli": "yxer"}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video, cover = root / "example.mp4", root / "example.png"
            video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42")
            from test_service import synthetic_png
            cover.write_bytes(synthetic_png())
            service = Service(config, root / "state", feishu=FeishuSource(), yxer=adapter)
            with self.assertRaisesRegex(BaseError, "yxer_field_value_invalid"):
                service.publish_prepare("recEXAMPLE", "EXAMPLE_ACCOUNT", "哔哩哔哩", "video", "cloud", [(str(video), "video"), (str(cover), "cover")], {"tags": ["标" * 21]}, "EXAMPLE_REQUEST_KEY")
        self.assertFalse(any(write or argv[1] in ("upload", "validate", "publish") for argv, write in fixture.calls))


class WriteOutcomeFixture:
    def __init__(self, write_error=None, read_error=None, ignored=False):
        self.calls = []
        self.write_error, self.read_error, self.ignored = write_error, read_error, ignored
        self.wrote = False
        self.fields = {"正文": "原文"}

    def __call__(self, argv, **kwargs):
        write = kwargs.get("write", False)
        self.calls.append((argv, write))
        if "api" in argv:
            return {"ok": True, "data": {"open_id": "ou_EXAMPLE", "tenant_key": "EXAMPLE_TENANT"}}
        if "+field-list" in argv:
            return {"ok": True, "data": {"fields": [{"name": "正文", "type": "text"}], "total": 1}}
        if "+record-get" in argv:
            if self.wrote and self.read_error:
                raise self.read_error
            return {"ok": True, "data": {"fields": ["正文"], "data": [[self.fields["正文"]]], "record_id_list": ["recEXAMPLE"]}}
        if "+record-upsert" in argv:
            if "--dry-run" in argv:
                return {"ok": True, "data": {"dry_run": True}}
            if self.write_error:
                raise self.write_error
            self.wrote = True
            self.fields = json.loads(argv[argv.index("--json") + 1])
            return {"ok": True, "data": {"ignored_fields": ["正文"]} if self.ignored else {}}
        raise AssertionError("unexpected fixture command")


class WriteOutcomeAudit(unittest.TestCase):
    config = {"feishu": {"cli": "EXAMPLE_CLI", "profile": "EXAMPLE_PROFILE", "base_token": "EXAMPLE_BASE", "table_id": "tblEXAMPLE"}}

    def test_feishu_definite_preexecution_errors_keep_safe_category_and_details(self):
        for category in ("validation", "auth", "permission", "unsupported"):
            with self.subTest(category=category):
                source = BaseError("cli_" + category, category=category, details={"missing_scopes": ["base:record:write"]})
                fixture = WriteOutcomeFixture(write_error=source)
                adapter = Feishu(self.config, fixture)
                plan = adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
                with self.assertRaises(BaseError) as caught:
                    adapter.apply_update(plan)
                self.assertEqual(caught.exception.code, source.code)
                self.assertEqual(caught.exception.category, category)
                self.assertEqual(caught.exception.details, source.details)
                self.assertFalse(caught.exception.unknown)
                self.assertEqual(sum(write for argv, write in fixture.calls), 1)

    def test_feishu_post_ack_read_auth_failure_is_still_unknown(self):
        source = BaseError("cli_auth", category="auth", details={"missing_scopes": ["base:record:read"]})
        fixture = WriteOutcomeFixture(read_error=source)
        adapter = Feishu(self.config, fixture)
        plan = adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
        with self.assertRaises(BaseError) as caught:
            adapter.apply_update(plan)
        self.assertTrue(caught.exception.unknown)
        self.assertEqual(caught.exception.category, "auth")
        self.assertEqual(caught.exception.details, source.details)
        self.assertEqual(sum(write for argv, write in fixture.calls), 1)

    def test_feishu_ignored_fields_after_ack_is_unknown_and_not_retried(self):
        fixture = WriteOutcomeFixture(ignored=True)
        adapter = Feishu(self.config, fixture)
        plan = adapter.plan_update("recEXAMPLE", {"正文": "新稿"})
        with self.assertRaises(BaseError) as caught:
            adapter.apply_update(plan)
        self.assertTrue(caught.exception.unknown)
        self.assertEqual(sum(write for argv, write in fixture.calls), 1)

    def test_publish_definite_rejection_keeps_classification_and_needs_fresh_preflight(self):
        import time
        from contentbase.yxer import signature
        payload = {"action": "publish", "publishType": "imageText", "platforms": ["小红书"], "publishChannel": "cloud", "publishArgs": {"accountForms": [{"platformAccountId": "EXAMPLE_ACCOUNT", "contentPublishForm": {"formType": "task", "title": "示例", "description": "正文"}}]}}
        for category in ("validation", "auth", "permission", "unsupported"):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "payload.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                adapter = Yxer({"yxer": {"cli": "EXAMPLE_CLI"}})
                adapter._preflights[signature(payload)] = time.monotonic()
                source = BaseError("cli_" + category, category=category, details={"missing_scopes": ["base:record:write"]})
                with patch.object(adapter, "_account"), patch.object(adapter, "_dynamic_payload"), patch.object(adapter, "_call", side_effect=source) as command:
                    with self.assertRaises(BaseError) as caught:
                        adapter.submit(payload, path)
                    self.assertEqual(caught.exception.code, source.code)
                    self.assertEqual(caught.exception.category, category)
                    self.assertEqual(caught.exception.details, source.details)
                    self.assertFalse(caught.exception.unknown)
                    self.assertEqual(command.call_count, 1)
                    with self.assertRaisesRegex(BaseError, "yxer_preflight_required"):
                        adapter.submit(payload, path)
                    self.assertEqual(command.call_count, 1)


if __name__ == "__main__":
    unittest.main()
