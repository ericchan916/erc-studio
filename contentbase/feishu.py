"""Existing-table Feishu adapter: read, freeze, dry-run, identity check, write, readback."""
import copy
import hashlib
import json
import re
from .errors import BaseError
from .runner import Runner, unwrap


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class Feishu:
    def __init__(self, config, runner=None):
        self.config = copy.deepcopy(config.get("feishu", config))
        self.runner = runner or Runner()
        for name in ("profile", "base_token", "table_id"):
            if not isinstance(self.config.get(name), str) or not self.config[name]:
                raise BaseError("feishu_binding_required")
        self.cli = self.config.get("cli", "lark-cli")

    def _call(self, args, *, write=False):
        value = self.runner([self.cli, "--profile", self.config["profile"], *args, "--as", "user", "--format", "json"], write=write)
        if not isinstance(value, dict) or value.get("ok") is not True or value.get("identity") not in (None, "user"):
            raise BaseError("feishu_response_invalid", unknown=write)
        data = unwrap(value)
        if not isinstance(data, dict):
            raise BaseError("feishu_response_invalid", unknown=write)
        return data

    def _args(self, action):
        return ["base", action, "--base-token", self.config["base_token"], "--table-id", self.config["table_id"]]

    def _binding_ref(self):
        return {key: self.config[key] for key in ("profile", "base_token", "table_id")}

    def identity(self):
        data = self._call(["api", "GET", "/open-apis/authen/v1/user_info"])
        if not isinstance(data.get("open_id"), str) or not re.fullmatch(r"ou_[A-Za-z0-9_-]+", data["open_id"]) or not isinstance(data.get("tenant_key"), str) or not data["tenant_key"]:
            raise BaseError("feishu_identity_unverified")
        return {key: data[key] for key in ("open_id", "tenant_key")}

    def _schema(self):
        fields, seen, offset = [], set(), 0
        while True:
            data = self._call([*self._args("+field-list"), "--limit", "200", "--offset", str(offset)])
            page = data.get("fields")
            if not isinstance(page, list) or any(not isinstance(f, dict) or not isinstance(f.get("name"), str) or not f.get("type") for f in page):
                raise BaseError("feishu_schema_invalid")
            if len(page) > 200 or any(f["name"] in seen for f in page) or len({f["name"] for f in page}) != len(page):
                raise BaseError("feishu_schema_invalid")
            fields.extend(page)
            seen.update(f["name"] for f in page)
            total = data.get("total")
            more = data.get("has_more")
            if total is not None and (type(total) is not int or total < len(fields)):
                raise BaseError("feishu_schema_pagination_invalid")
            if more is not None and type(more) is not bool:
                raise BaseError("feishu_schema_pagination_invalid")
            if total is not None:
                continuing = len(fields) < total
                if more is not None and more != continuing:
                    raise BaseError("feishu_schema_pagination_invalid")
            elif more is not None:
                continuing = more
            else:
                continuing = len(page) == 200
            if not continuing:
                if not fields:
                    raise BaseError("feishu_schema_invalid")
                return fields
            if not page or len(fields) >= 10000:
                raise BaseError("feishu_schema_pagination_invalid")
            offset += len(page)

    def binding(self):
        base = self._call(["base", "+base-get", "--base-token", self.config["base_token"]])
        # The configured ID is authoritative. table-list defaults to only 50
        # entries and must not turn a later existing table into a false missing.
        data = self._call(self._args("+table-get"))
        table = data.get("table")
        if not isinstance(table, dict):
            raise BaseError("feishu_tables_invalid")
        returned = table.get("table_id") or table.get("id")
        if returned != self.config["table_id"]:
            raise BaseError("feishu_table_missing")
        if table.get("table_id") and table.get("id") and table["table_id"] != table["id"]:
            raise BaseError("feishu_tables_invalid")
        schema = self._schema()
        for name in self.config.get("fields", {}).values():
            if not any(f["name"] == name for f in schema):
                raise BaseError("feishu_field_mapping_missing")
        return {"base": base, "table": table, "fields": schema, "binding": self._binding_ref()}

    @staticmethod
    def _rows(data):
        if isinstance(data.get("fields"), list) and isinstance(data.get("data"), list) and isinstance(data.get("record_id_list"), list):
            names, values, ids = data["fields"], data["data"], data["record_id_list"]
            if any(not isinstance(n, str) for n in names) or len(set(names)) != len(names) or len(values) != len(ids):
                raise BaseError("feishu_records_invalid")
            if any(not isinstance(row, list) or len(row) != len(names) for row in values):
                raise BaseError("feishu_records_invalid")
            rows = [{"record_id": rid, "fields": dict(zip(names, row))} for rid, row in zip(ids, values)]
        elif isinstance(data.get("records"), list):
            rows = data["records"]
        else:
            raise BaseError("feishu_records_invalid")
        result = []
        for row in rows:
            if not isinstance(row, dict):
                raise BaseError("feishu_records_invalid")
            rid = row.get("record_id") or row.get("id")
            if not isinstance(rid, str) or not re.fullmatch(r"rec[A-Za-z0-9_-]+", rid) or not isinstance(row.get("fields"), dict):
                raise BaseError("feishu_records_invalid")
            result.append({"record_id": rid, "id": rid, "fields": copy.deepcopy(row["fields"])})
        if len({row["record_id"] for row in result}) != len(result):
            raise BaseError("feishu_records_invalid")
        return result

    def records(self):
        result, seen, offset = [], set(), 0
        ceiling = int(self.config.get("record_limit", 10000))
        if not 1 <= ceiling <= 100000:
            raise BaseError("feishu_record_limit_invalid")
        while True:
            data = self._call([*self._args("+record-list"), "--limit", str(min(200, ceiling - len(result))), "--offset", str(offset)])
            if type(data.get("has_more")) is not bool:
                raise BaseError("feishu_pagination_invalid")
            page = self._rows(data)
            if any(row["record_id"] in seen for row in page) or len(page) > min(200, ceiling - len(result)):
                raise BaseError("feishu_pagination_invalid")
            result.extend(page)
            seen.update(row["record_id"] for row in page)
            if not data["has_more"]:
                return result
            if not page:
                raise BaseError("feishu_pagination_invalid")
            if len(result) >= ceiling:
                raise BaseError("feishu_records_truncated")
            expected = offset + len(page)
            new_offset = data.get("next_offset", expected)
            if type(new_offset) is not int or new_offset < expected:
                raise BaseError("feishu_pagination_invalid")
            offset = new_offset

    def record(self, record_id):
        if not isinstance(record_id, str) or not re.fullmatch(r"rec[A-Za-z0-9_-]+", record_id):
            raise BaseError("feishu_record_id_invalid")
        rows = self._rows(self._call([*self._args("+record-get"), "--record-id", record_id]))
        selected = [row for row in rows if row["record_id"] == record_id]
        if len(rows) != 1 or len(selected) != 1:
            raise BaseError("feishu_record_missing")
        return selected[0]

    @staticmethod
    def _validate_fields(schema, fields):
        if not isinstance(fields, dict) or not fields:
            raise BaseError("feishu_write_fields_invalid")
        for name, value in fields.items():
            spec = next((f for f in schema if f["name"] == name or f.get("field_id") == name), None)
            if spec is None:
                raise BaseError("feishu_write_field_missing")
            kind = spec["type"]
            if kind in ("text", "url", "phone"):
                valid = isinstance(value, str) and len(value) <= 200000
            elif kind == "select":
                properties = spec.get("property") or {}
                if not isinstance(properties, dict):
                    raise BaseError("feishu_schema_invalid")
                options = spec.get("options", properties.get("options", []))
                multiple = spec.get("multiple", properties.get("multiple", False))
                if not isinstance(options, list) or type(multiple) is not bool:
                    raise BaseError("feishu_schema_invalid")
                names = [o.get("name") if isinstance(o, dict) else o for o in options]
                if any(not isinstance(n, str) for n in names):
                    raise BaseError("feishu_schema_invalid")
                allowed = set(names)
                valid = isinstance(value, list) and all(isinstance(v, str) and v in allowed for v in value)
                valid = valid and (multiple or len(value) <= 1) and len(set(value)) == len(value)
            elif kind == "link":
                valid = isinstance(value, list) and all(isinstance(v, dict) and set(v) == {"id"} and isinstance(v["id"], str) and re.fullmatch(r"rec[A-Za-z0-9_-]+", v["id"]) for v in value)
            else:
                raise BaseError("feishu_write_field_type_unsupported")
            if not valid:
                raise BaseError("feishu_write_value_invalid")

    def plan_update(self, record_id, fields):
        schema = self._schema()
        self._validate_fields(schema, fields)
        before = self.record(record_id)
        identity = self.identity()
        frozen = copy.deepcopy(fields)
        dry_run = self._call([*self._args("+record-upsert"), "--record-id", record_id, "--json", json.dumps(frozen, ensure_ascii=False), "--dry-run"])
        if dry_run.get("ignored_fields"):
            raise BaseError("feishu_fields_ignored")
        return {"binding": self._binding_ref(), "identity": identity, "before": before, "before_hash": fingerprint(before), "schema_hash": fingerprint(schema), "fields": frozen, "record_id": record_id, "dry_run": dry_run, "fields_hash": fingerprint(frozen)}

    @staticmethod
    def _matches_fields(after, schema, fields):
        by_name = {f.get("field_id"): f["name"] for f in schema if f.get("field_id")}
        for name, wanted in fields.items():
            actual = after["fields"].get(by_name.get(name, name))
            if actual == wanted:
                continue
            if isinstance(wanted, str) and isinstance(actual, list) and all(isinstance(v, dict) and isinstance(v.get("text"), str) for v in actual):
                actual = "".join(v["text"] for v in actual)
            if isinstance(wanted, str) and isinstance(actual, dict) and isinstance(actual.get("text"), str):
                actual = actual["text"]
            if isinstance(wanted, list) and wanted and isinstance(wanted[0], str) and isinstance(actual, list):
                actual = [v.get("name") if isinstance(v, dict) else v for v in actual]
            if isinstance(wanted, list) and wanted and isinstance(wanted[0], dict) and isinstance(actual, list):
                actual = [{"id": v.get("id") or v.get("record_id")} for v in actual if isinstance(v, dict)]
            if actual != wanted:
                return False
        return True

    def verify_update(self, plan):
        """Only prove current target fields; this is not a replay or an original receipt."""
        try:
            if not isinstance(plan, dict) or plan.get("binding") != self._binding_ref() or plan.get("fields_hash") != fingerprint(plan.get("fields")):
                raise BaseError("feishu_plan_changed")
            if self.identity() != plan.get("identity"):
                raise BaseError("feishu_identity_changed")
            schema = self._schema()
            if fingerprint(schema) != plan.get("schema_hash"):
                raise BaseError("feishu_schema_changed")
            self._validate_fields(schema, plan["fields"])
            current = self.record(plan.get("record_id"))
            if not self._matches_fields(current, schema, plan["fields"]):
                raise BaseError("feishu_write_unverified")
            return current
        except Exception:
            raise BaseError("feishu_write_unverified", unknown=True) from None

    def apply_update(self, plan):
        if not isinstance(plan, dict) or plan.get("binding") != self._binding_ref() or plan.get("fields_hash") != fingerprint(plan.get("fields")):
            raise BaseError("feishu_plan_changed")
        if self.identity() != plan.get("identity"):
            raise BaseError("feishu_identity_changed")
        before = self.record(plan.get("record_id"))
        if fingerprint(before) != plan.get("before_hash") or fingerprint(plan.get("before")) != plan.get("before_hash"):
            raise BaseError("feishu_record_changed")
        schema = self._schema()
        if fingerprint(schema) != plan.get("schema_hash"):
            raise BaseError("feishu_schema_changed")
        self._validate_fields(schema, plan["fields"])
        written_response_received = False
        try:
            response = self._call([*self._args("+record-upsert"), "--record-id", plan["record_id"], "--json", json.dumps(plan["fields"], ensure_ascii=False)], write=True)
            written_response_received = True
            if response.get("ignored_fields"):
                raise BaseError("feishu_fields_ignored", unknown=True)
            after = self.record(plan["record_id"])
            if not self._matches_fields(after, schema, plan["fields"]):
                raise BaseError("feishu_write_unverified", unknown=True)
            return after
        except BaseError as error:
            if not written_response_received and not error.unknown and error.category in ("validation", "auth", "permission", "unsupported"):
                raise
            raise BaseError("feishu_write_unverified", unknown=True, category=error.category, details=error.details) from None
        except Exception:
            raise BaseError("feishu_write_unverified", unknown=True) from None
