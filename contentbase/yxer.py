"""Real yxer CLI contracts; publishing is single-channel, frozen and never retried."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import time
import threading
from .errors import BaseError
from .runner import Runner, unwrap


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class Yxer:
    def __init__(self, config, runner=None):
        self.config = copy.deepcopy(config.get("yxer", config))
        self.cli = self.config.get("cli", "yxer")
        self.runner = runner or Runner()
        self._choices = {}
        self._preflights = {}
        self._submissions = set()
        self._lock = threading.RLock()

    def _call(self, args, *, timeout=60, write=False):
        packet = self.runner([self.cli, *args, "--json"], timeout=timeout, write=write)
        if not isinstance(packet, dict) or packet.get("ok") is not True:
            raise BaseError("yxer_response_invalid", unknown=write)
        return unwrap(packet)

    @staticmethod
    def _channels(data):
        if not isinstance(data, dict) or not isinstance(data.get("publishChannelReadiness"), dict):
            raise BaseError("yxer_doctor_invalid")
        result = {}
        for key, value in data["publishChannelReadiness"].items():
            if key in ("cloud", "local"):
                if not isinstance(value, dict) or type(value.get("configured")) is not bool:
                    raise BaseError("yxer_doctor_invalid")
                result[key] = {"configured": value["configured"]}
        return result

    def doctor(self):
        return {"channels": self._channels(self._call(["doctor"]))}

    def _raw_accounts(self):
        data = self._call(["accounts", "list", "--all"])
        if not isinstance(data, list) or any(not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"] or not isinstance(a.get("platformName"), str) or not a["platformName"] or type(a.get("status")) is not int for a in data):
            raise BaseError("yxer_accounts_invalid")
        if len({a["id"] for a in data}) != len(data):
            raise BaseError("yxer_accounts_invalid")
        for account in data:
            if any(key in account and type(account[key]) is not bool for key in ("isLock", "isFreeze", "isOperate")):
                raise BaseError("yxer_accounts_invalid")
            if "platformType" in account and type(account["platformType"]) is not int:
                raise BaseError("yxer_accounts_invalid")
            if "authChannel" in account and not isinstance(account["authChannel"], str):
                raise BaseError("yxer_accounts_invalid")
        return data

    @staticmethod
    def _operators(account):
        operators = account.get("operators")
        if not isinstance(operators, list):
            return None
        normalized = []
        for member in operators:
            if not isinstance(member, dict) or any(not isinstance(member.get(key), str) or not member[key] for key in ("id", "userId")):
                return None
            normalized.append({key: member[key] for key in ("id", "userId")})
        return sorted(normalized, key=lambda row: (row["id"], row["userId"]))

    def accounts(self):
        allowed = ("id", "platformName", "platformAccountName", "status", "isLock", "isFreeze", "isOperate", "authChannel", "platformType")
        result = []
        for account in self._raw_accounts():
            row = {k: copy.deepcopy(account[k]) for k in allowed if k in account}
            operators = self._operators(account)
            row["operatorBinding"] = signature(operators) if operators is not None else None
            result.append(row)
        return result

    def provider_binding(self, account_id=None):
        """Fingerprint observable execution context, never claim authenticated user/UID."""
        context = self._call(["doctor"])
        channels = self._channels(context)
        if not isinstance(context.get("apiUrl"), str) or not context["apiUrl"] or type(context.get("apiKeyPresent")) is not bool:
            raise BaseError("yxer_provider_binding_unavailable")
        client = context.get("localPublishClientId")
        if client is not None and not isinstance(client, str):
            raise BaseError("yxer_provider_binding_unavailable")
        accounts = self._raw_accounts()
        if account_id is not None:
            if not isinstance(account_id, str) or not account_id:
                raise BaseError("yxer_account_missing")
            accounts = [account for account in accounts if account["id"] == account_id]
            if len(accounts) != 1:
                raise BaseError("yxer_account_missing")
        limitations = {"provider_current_user_unverified", "stable_platform_account_uid_unverified"}
        if not context["apiKeyPresent"]:
            limitations.add("provider_credential_not_present")
        if channels.get("local", {}).get("configured") and not client:
            limitations.add("local_target_identifier_unavailable")
        bound = []
        operator_coverage = 0
        for account in accounts:
            operators = self._operators(account)
            if operators:
                operator_coverage += 1
            else:
                limitations.add("account_operator_membership_unavailable")
            if "authChannel" not in account or "platformType" not in account:
                limitations.add("account_execution_metadata_unavailable")
            bound.append({"id": account["id"], "platform": account["platformName"], "authChannel": account.get("authChannel"), "platformType": account.get("platformType"), "operators": operators})
        return {"version": 1, "scope": "account" if account_id is not None else "all_accounts",
                "provider_hash": signature({"apiUrl": context["apiUrl"], "apiKeyPresent": context["apiKeyPresent"]}),
                "local_target_hash": signature({"clientId": client}),
                "accounts_hash": signature(sorted(bound, key=lambda row: row["id"])),
                "credential_present": context["apiKeyPresent"],
                "account_count": len(accounts), "operator_binding_count": operator_coverage,
                "limitations": sorted(limitations)}

    def _account(self, account_id, platform=None):
        selected = [a for a in self.accounts() if a["id"] == account_id]
        if len(selected) != 1:
            raise BaseError("yxer_account_missing")
        a = selected[0]
        if platform is not None and a["platformName"] != platform:
            raise BaseError("yxer_account_platform_changed")
        if type(a.get("status")) is not int or a["status"] != 1 or any(type(a.get(key)) is not bool for key in ("isLock", "isFreeze", "isOperate")) or a["isLock"] or a["isFreeze"] or not a["isOperate"]:
            raise BaseError("yxer_account_unavailable")
        return a

    def contract(self, platform, kind):
        if not isinstance(platform, str) or not platform or kind not in ("imageText", "video"):
            raise BaseError("yxer_contract_invalid")
        prepared = self._call(["prepare", platform, kind])
        schema = self._call(["schema", "get", platform, kind])
        if not isinstance(schema, dict) or not isinstance(schema.get("businessFields"), dict):
            raise BaseError("yxer_schema_invalid")
        return {"schema": schema, "prepare": self._redact(prepared)}

    def choices(self, account_id, kind, field, query=""):
        a = self._account(account_id)
        schema = self.contract(a["platformName"], kind)["schema"]["businessFields"]
        if field not in ("music", "category") or field not in schema or not isinstance(query, str) or len(query) > 100:
            raise BaseError("yxer_choice_invalid")
        args = ["query", "music", account_id, *(["--query", query] if query else [])] if field == "music" else ["query", "categories", account_id, "--type", kind]
        data = self._call(args)
        rows = data.get("dataList", data.get("data", [])) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise BaseError("yxer_choices_invalid")
        result = []
        def visit(items, parents=()):
            for item in items:
                if not isinstance(item, dict) or not item.get("yixiaoerId"):
                    continue
                children = item.get("children") or item.get("dataList") or []
                if not isinstance(children, list):
                    raise BaseError("yxer_choices_invalid")
                current = (*parents, item)
                if children:
                    visit(children, current)
                else:
                    value = list(current) if field == "category" else item
                    value = copy.deepcopy(value)
                    key = (account_id, a["platformName"], kind, field, signature(value))
                    self._choices[key] = (time.monotonic(), value)
                    result.append(value)
        visit(rows)
        return result

    def _choice(self, account_id, platform, kind, field, value):
        key = (account_id, platform, kind, field, signature(value))
        receipt = self._choices.get(key)
        if not receipt or time.monotonic() - receipt[0] > 1800 or receipt[1] != value:
            raise BaseError("yxer_choice_receipt_required")
        return copy.deepcopy(receipt[1])

    @staticmethod
    def _resource(value, usage):
        if not isinstance(value, dict) or not isinstance(value.get("key"), str) or not value["key"]:
            raise BaseError("yxer_resource_invalid")
        for name in ("size", "width", "height", *(["duration"] if usage == "video" else [])):
            if type(value.get(name)) not in (int, float) or not math.isfinite(value[name]) or value[name] <= 0:
                raise BaseError("yxer_resource_metadata_missing")
        return copy.deepcopy(value)

    def upload(self, path, platform, usage):
        path = Path(path)
        if usage not in ("image", "video", "cover") or path.is_symlink() or not path.is_file():
            raise BaseError("yxer_upload_file_invalid")
        with path.open("rb") as stream:
            header = stream.read(16)
        image = header.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8")) or (header.startswith(b"RIFF") and b"WEBP" in header)
        if not ((usage == "video" and b"ftyp" in header) or (usage != "video" and image)):
            raise BaseError("yxer_upload_media_invalid")
        result = self._call(["upload", str(path.resolve()), "--platform", platform, "--usage", usage, "--auto-meta"], timeout=600, write=True)
        return self._resource(result, usage)

    @staticmethod
    def _field_value(value, spec, depth=0):
        if not isinstance(spec, dict) or depth > 12:
            raise BaseError("yxer_field_schema_invalid")
        kind = spec.get("type")
        if kind == "string":
            valid = isinstance(value, str)
        elif kind == "boolean":
            valid = type(value) is bool
        elif kind in ("integer", "number"):
            valid = type(value) in (int, float) and math.isfinite(value) and (kind != "integer" or type(value) is int)
        elif kind == "array":
            valid = isinstance(value, list)
        elif kind == "object":
            valid = isinstance(value, dict)
        else:
            raise BaseError("yxer_field_type_unsupported")
        if not valid:
            raise BaseError("yxer_field_value_invalid")
        if "enum" in spec:
            options = spec["enum"]
            if not isinstance(options, list):
                raise BaseError("yxer_field_schema_invalid")
            if not any(value == option and (type(value) is type(option) or type(value) in (int, float) and type(option) in (int, float)) for option in options):
                raise BaseError("yxer_field_value_invalid")
        if isinstance(value, (str, list)):
            maximum = spec.get("maxLength" if isinstance(value, str) else "maxItems", 200000)
            minimum = spec.get("minLength" if isinstance(value, str) else "minItems", 0)
            if type(maximum) is not int or type(minimum) is not int or minimum < 0 or maximum < minimum:
                raise BaseError("yxer_field_schema_invalid")
            if not minimum <= len(value) <= maximum:
                raise BaseError("yxer_field_value_invalid")
        if kind in ("integer", "number"):
            if value < spec.get("minimum", -math.inf) or value > spec.get("maximum", math.inf):
                raise BaseError("yxer_field_value_invalid")
        elif kind == "string" and "pattern" in spec:
            try:
                matched = re.search(spec["pattern"], value)
            except (TypeError, re.error):
                raise BaseError("yxer_field_schema_invalid") from None
            if not matched:
                raise BaseError("yxer_field_value_invalid")
        elif kind == "array":
            item_rule = spec.get("items", {"type": "string"})
            for item in value:
                Yxer._field_value(item, item_rule, depth + 1)
            if spec.get("uniqueItems") and len({signature(item) for item in value}) != len(value):
                raise BaseError("yxer_field_value_invalid")
        elif kind == "object":
            properties = spec.get("properties")
            if not isinstance(properties, dict):
                # Opaque dynamic provider objects require a dedicated queried adapter.
                raise BaseError("yxer_dynamic_field_unsupported")
            required = spec.get("required", [])
            required = required if isinstance(required, list) else []
            for key, rule in properties.items():
                if key not in value and (key in required or isinstance(rule, dict) and rule.get("required") is True):
                    raise BaseError("yxer_field_value_invalid")
            for key, item in value.items():
                if key not in properties:
                    raise BaseError("yxer_field_value_invalid")
                Yxer._field_value(item, properties[key], depth + 1)
        return copy.deepcopy(value)

    @staticmethod
    def _required_form(form, spec, *, before_upload=False):
        resources = {"images", "video", "cover", "coverKey", "horizontalCover", "accountForms"}
        for key, rule in spec.items():
            if not isinstance(rule, dict):
                raise BaseError("yxer_field_schema_invalid")
            if key == "accountForms" or before_upload and key in resources:
                continue
            if rule.get("required") and (key not in form or form[key] is None or form[key] == "" or form[key] == []):
                raise BaseError("yxer_required_field_missing")

    def _prepare_form(self, platform, kind, account_id, title, body, fields):
        if not isinstance(title, str) or not isinstance(body, str) or not isinstance(fields, dict):
            raise BaseError("yxer_payload_invalid")
        self._account(account_id, platform)
        spec = self.contract(platform, kind)["schema"]["businessFields"]
        if any(not isinstance(rule, dict) for rule in spec.values()):
            raise BaseError("yxer_field_schema_invalid")
        form = {key: copy.deepcopy(rule["default"]) for key, rule in spec.items() if "default" in rule}
        form.update(formType="task", title=title, description=body)
        resources = {"images", "video", "cover", "coverKey", "horizontalCover", "accountForms"}
        reserved = {"formType", "title", "description", *resources}
        for key, value in fields.items():
            if key not in spec or key in reserved:
                raise BaseError("yxer_field_unsupported")
            form[key] = copy.deepcopy(value)
        for key, value in list(form.items()):
            if key in ("music", "category"):
                if value:
                    form[key] = self._choice(account_id, platform, kind, key, value)
            elif key not in resources:
                # Check provider defaults as well as explicitly supplied values.
                form[key] = self._field_value(value, spec.get(key, {"type": "string"}))
        self._required_form(form, spec, before_upload=True)
        if platform == "抖音" and kind == "imageText" and not form.get("music"):
            raise BaseError("yxer_music_required")
        if platform == "哔哩哔哩" and kind == "video" and (not form.get("category") or not form.get("tags")):
            raise BaseError("yxer_bilibili_fields_required")
        return form, spec

    @staticmethod
    def _basic_media_usages(kind, usages):
        if not isinstance(usages, list) or any(not isinstance(usage, str) or usage not in ("image", "video", "cover") for usage in usages):
            raise BaseError("yxer_resources_invalid")
        if kind == "imageText":
            if not usages or any(usage != "image" for usage in usages):
                raise BaseError("yxer_images_invalid")
        elif kind == "video":
            if sorted(usages) != ["cover", "video"]:
                raise BaseError("yxer_video_cover_required")
        else:
            raise BaseError("yxer_type_unsupported")

    @staticmethod
    def _media_usages(kind, spec, usages):
        if not isinstance(usages, list) or any(usage not in ("image", "video", "cover") for usage in usages):
            raise BaseError("yxer_resources_invalid")
        if kind == "imageText":
            rule = spec.get("images")
            if not isinstance(rule, dict) or not usages or any(usage != "image" for usage in usages):
                raise BaseError("yxer_images_invalid")
            maximum, minimum = rule.get("maxItems", 20), rule.get("minItems", 1)
            if type(maximum) is not int or type(minimum) is not int or not 0 <= minimum <= maximum:
                raise BaseError("yxer_field_schema_invalid")
            if not minimum <= len(usages) <= maximum:
                raise BaseError("yxer_images_invalid")
        elif kind == "video":
            if not isinstance(spec.get("video"), dict) or sorted(usages) != ["cover", "video"]:
                raise BaseError("yxer_video_cover_required")
        else:
            raise BaseError("yxer_type_unsupported")

    def validate_fields(self, platform, kind, account_id, title, body, fields, media_usages):
        """Read/check before uploads; no fabricated resource, validate or publish call."""
        self._basic_media_usages(kind, media_usages)
        form, spec = self._prepare_form(platform, kind, account_id, title, body, fields)
        self._media_usages(kind, spec, media_usages)
        return {"platform": platform, "kind": kind, "account_id": account_id,
                "media_count": len(media_usages),
                "fields_checked": sorted(key for key in form if key not in ("images", "video", "cover", "coverKey", "accountForms"))}

    def prepare_payload(self, platform, kind, channel, account_id, title, body, resources, fields):
        if channel not in ("cloud", "local") or not isinstance(resources, list):
            raise BaseError("yxer_payload_invalid")
        if any(not isinstance(item, dict) or set(item) != {"usage", "resource"} for item in resources):
            raise BaseError("yxer_resources_invalid")
        usages = [item["usage"] for item in resources]
        self._basic_media_usages(kind, usages)
        form, spec = self._prepare_form(platform, kind, account_id, title, body, fields)
        normalized = [(item["usage"], self._resource(item["resource"], item["usage"])) for item in resources]
        self._media_usages(kind, spec, usages)
        account_form = {"platformAccountId": account_id, "contentPublishForm": form}
        if kind == "imageText":
            images = [value for role, value in normalized]
            account_form.update(images=images, cover=images[0], coverKey=images[0]["key"])
            form["images"] = images
        else:
            cover = next(value for role, value in normalized if role == "cover")
            video = next(value for role, value in normalized if role == "video")
            account_form.update(video=video, cover=cover, coverKey=cover["key"])
            form.update(video=video, cover=cover, coverKey=cover["key"])
        self._required_form(form, spec)
        return {"action": "publish", "publishType": kind, "platforms": [platform], "publishChannel": channel, "publishArgs": {"accountForms": [account_form]}}

    @staticmethod
    def _payload(payload, path):
        if not isinstance(payload, dict) or payload.get("action") != "publish" or payload.get("publishType") not in ("imageText", "video") or payload.get("publishChannel") not in ("cloud", "local") or not isinstance(payload.get("platforms"), list) or len(payload["platforms"]) != 1:
            raise BaseError("yxer_payload_invalid")
        args = payload.get("publishArgs")
        if not isinstance(args, dict):
            raise BaseError("yxer_payload_invalid")
        forms = args.get("accountForms")
        if not isinstance(forms, list) or len(forms) != 1 or not isinstance(forms[0], dict) or not isinstance(forms[0].get("platformAccountId"), str):
            raise BaseError("yxer_payload_invalid")
        path = Path(path)
        try:
            if path.is_symlink() or not path.is_file() or json.loads(path.read_text(encoding="utf-8")) != payload:
                raise BaseError("yxer_payload_file_changed")
        except (OSError, ValueError):
            raise BaseError("yxer_payload_file_changed") from None
        return payload["platforms"][0], payload["publishType"], payload["publishChannel"], forms[0]["platformAccountId"], str(path.resolve())

    def _dynamic_payload(self, payload, platform, kind, account_id):
        form = payload["publishArgs"]["accountForms"][0].get("contentPublishForm")
        if not isinstance(form, dict):
            raise BaseError("yxer_payload_invalid")
        for field in ("music", "category"):
            if form.get(field):
                self._choice(account_id, platform, kind, field, form[field])
        if platform == "抖音" and kind == "imageText" and not form.get("music"):
            raise BaseError("yxer_music_required")
        if platform == "哔哩哔哩" and kind == "video" and (not form.get("category") or not form.get("tags")):
            raise BaseError("yxer_bilibili_fields_required")

    def preflight(self, payload, path):
        platform, kind, channel, account_id, path = self._payload(payload, path)
        self._account(account_id, platform)
        self._dynamic_payload(payload, platform, kind, account_id)
        validated = self._call(["validate", platform, kind, path, "--publish-channel", channel], timeout=120)
        if not isinstance(validated, dict) or validated.get("valid") is False or validated.get("errors"):
            raise BaseError("yxer_validation_failed")
        dry = self._call(["publish", kind, platform, path, "--publish-channel", channel, "--dry-run"], timeout=120)
        if not isinstance(dry, dict) or not isinstance(dry.get("meta"), dict) or dry["meta"].get("effectivePublishChannel") != channel:
            raise BaseError("yxer_channel_mismatch")
        digest = signature(payload)
        self._preflights[digest] = time.monotonic()
        return {"validation": self._redact(validated), "dry_run": self._redact(dry), "payload_hash": digest, "channel": channel}

    def revalidate(self, payload, path, fields=None):
        """Rebuild read-only receipts after restart; caller must gate persisted state/approval."""
        platform, kind, channel, account_id, resolved = self._payload(payload, path)
        frozen_hash = signature(payload)
        with self._lock:
            if frozen_hash in self._submissions:
                raise BaseError("yxer_submission_already_attempted", unknown=True)
            # A failed refresh must not leave an older usable preflight receipt.
            self._preflights.pop(frozen_hash, None)
        if fields is not None and not isinstance(fields, dict):
            raise BaseError("yxer_choice_invalid")
        fields = fields or {}
        query = fields.get("music_query", "")
        if not isinstance(query, str) or len(query) > 100:
            raise BaseError("yxer_choice_invalid")
        self._account(account_id, platform)
        self.contract(platform, kind)
        form = payload["publishArgs"]["accountForms"][0].get("contentPublishForm")
        if not isinstance(form, dict):
            raise BaseError("yxer_payload_invalid")
        for field in ("music", "category"):
            if form.get(field):
                values = self.choices(account_id, kind, field, query if field == "music" else "")
                if not any(value == form[field] for value in values):
                    raise BaseError("yxer_dynamic_selection_changed")
        if signature(payload) != frozen_hash:
            raise BaseError("yxer_payload_changed")
        self._payload(payload, resolved)
        result = self.preflight(payload, resolved)
        if signature(payload) != frozen_hash:
            self._preflights.pop(frozen_hash, None)
            raise BaseError("yxer_payload_changed")
        return result

    def submit(self, payload, path):
        platform, kind, channel, account_id, path = self._payload(payload, path)
        digest = signature(payload)
        with self._lock:
            if digest in self._submissions:
                raise BaseError("yxer_submission_already_attempted", unknown=True)
            if digest not in self._preflights or time.monotonic() - self._preflights[digest] > 1800:
                raise BaseError("yxer_preflight_required")
            self._account(account_id, platform)
            self._dynamic_payload(payload, platform, kind, account_id)
            self._submissions.add(digest)
        written_response_received = False
        try:
            result = self._call(["publish", kind, platform, path, "--publish-channel", channel], timeout=120, write=True)
            written_response_received = True
            if not isinstance(result, dict):
                raise BaseError("yxer_receipt_missing", unknown=True)
            taskset = result.get("taskSet")
            task = result.get("taskSetId") or (taskset.get("id") if isinstance(taskset, dict) else None) or result.get("id")
            vendor_receipt = self._redact(result)
            if not isinstance(task, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", task):
                error = BaseError("yxer_receipt_missing", unknown=True)
                error.vendor_receipt = vendor_receipt
                raise error
            return {"taskSetId": task, "accepted": True, "publicVisible": None, "vendor_receipt": vendor_receipt}
        except BaseError as error:
            if not written_response_received and not error.unknown and error.category in ("validation", "auth", "permission", "unsupported"):
                # No automatic retry. A later explicit attempt needs a new preflight.
                with self._lock:
                    self._submissions.discard(digest)
                    self._preflights.pop(digest, None)
                raise
            failure = BaseError(error.code, unknown=True, category=error.category, details=error.details)
            if hasattr(error, "vendor_receipt"):
                failure.vendor_receipt = error.vendor_receipt
            raise failure from None
        except Exception:
            raise BaseError("yxer_submission_unknown", unknown=True) from None

    @classmethod
    def _redact(cls, value):
        if isinstance(value, dict):
            return {key: cls._redact(item) for key, item in value.items() if not re.search(r"token|cookie|secret|client.?id|authorization|password|api.?key", key, re.I)}
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        if isinstance(value, str):
            value = re.sub(r"(?i)(token|cookie|secret|authorization|password|api.?key|client.?id)\s*[:=]\s*\S+", r"\1=[redacted]", value)
            return value[:2000]
        return value

    def details(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", task_id):
            raise BaseError("yxer_task_id_invalid")
        value = self._call(["query", "details", task_id])
        if not isinstance(value, dict) or not isinstance(value.get("tasks", []), list):
            raise BaseError("yxer_details_invalid")
        allowed = {"platformName", "platformAccountName", "stageStatus", "taskStatus", "queryStatus", "publishId", "openUrl", "errorMessage", "visibleType", "title"}
        return {"id": value.get("id", task_id), "tasks": [{key: self._redact(item) for key, item in task.items() if key in allowed} for task in value.get("tasks", []) if isinstance(task, dict)], "publicVisible": None}
