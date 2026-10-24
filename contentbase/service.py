import copy
import hashlib
import json
import math
import os
import time
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from .config import private_write, state_home
from .errors import BaseError
from .store import Store, digest
from .feishu import Feishu
from .yxer import Yxer
from .frozen_media import FrozenMedia, inspect_media


def text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, list):
        if all(isinstance(x, str) or (isinstance(x, dict) and isinstance(x.get("text"), str)) for x in value):
            return "".join(x["text"] if isinstance(x, dict) else x for x in value)
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    raise BaseError("feishu_text_cell_unsupported")


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


class Service:
    def __init__(self, config, state=None, feishu=None, yxer=None):
        self.config = copy.deepcopy(config)
        self.root = Path(state or state_home()).resolve()
        source = Path(__file__).resolve().parents[1]
        if self.root.is_relative_to(source):
            raise BaseError("runtime_state_must_be_outside_source")
        home = Path.home().resolve()
        reserved = {Path("/"), home, *source.parents, *home.parents,
                    home / ".config", home / ".local", home / ".local/state", home / "Library",
                    home / "Library/Application Support", home / "Desktop", home / "Documents"}
        if self.root in reserved:
            raise BaseError("dedicated_state_directory_required")
        allowed = {"outbox.db", "outbox.db-journal", "outbox.db-wal", "outbox.db-shm", "config.json", "web-access.json", "publish", "media", "choices", ".contentbase-state.json"}
        if self.root.exists() and any(p.is_symlink() or p.name not in allowed for p in self.root.iterdir()):
            raise BaseError("state_directory_contains_unrelated_files")
        self.expected_identity = ContextVar("contentbase_expected_identity", default=None)
        self.store = Store(self.root / "outbox.db")
        self.feishu = feishu or Feishu(config)
        self.yxer = yxer or Yxer(config)
        self.frozen_media = FrozenMedia(self.root)
        self.media_slots = threading.BoundedSemaphore(2)
        private_write(self.root / ".contentbase-state.json", {"format_version": 1})

    def binding_hash(self):
        return digest(self.config)

    def remote_binding(self):
        return {name: self.config["feishu"][name] for name in ("base_token", "table_id")}

    def record_resource(self, record_id):
        return digest({"binding": self.remote_binding(), "record": record_id})

    def actor(self):
        identity = self.feishu.identity()
        try:
            self.store.pin_operator(identity)
        except BaseError as error:
            error.identity_fingerprint = self.identity_fingerprint(identity)
            raise
        expected = self.expected_identity.get()
        if expected and self.identity_fingerprint(identity) != expected:
            error = BaseError("client_identity_changed")
            error.identity_fingerprint = self.identity_fingerprint(identity)
            raise error
        return identity

    def identity_fingerprint(self, identity=None):
        return digest({"binding": self.binding_hash(), "identity": identity or self.actor()})

    @contextmanager
    def client_identity(self, fingerprint):
        current = self.identity_fingerprint()
        if fingerprint != current:
            error = BaseError("client_identity_changed")
            error.identity_fingerprint = current
            raise error
        token = self.expected_identity.set(current)
        try:
            yield
        finally:
            self.expected_identity.reset(token)

    def owned_operation(self, oid, identity=None, check_provider=False):
        identity = identity or self.actor()
        op = self.store.get(oid)
        data = op["data"]
        bound, operator = (data.get("binding_hash"), data.get("plan", {}).get("identity")) if op["kind"] != "publish" else (data.get("inputs", {}).get("binding"), data.get("inputs", {}).get("identity"))
        if bound != self.binding_hash() or operator != identity:
            raise BaseError("operation_not_found")
        if check_provider and op["kind"] == "publish" and data["inputs"].get("provider_binding") != self.yxer.provider_binding(data["inputs"]["account_id"]):
            raise BaseError("publish_provider_changed")
        return op

    def inspect_page(self, limit=200, offset=0):
        identity = self.actor()
        scope = self.identity_fingerprint(identity)
        result = self.store.scoped_page([scope], limit, offset)
        result.update(identity_fingerprint=scope)
        return result

    def inspect(self, oid=None, limit=200, offset=0):
        if oid:
            return self.owned_operation(oid)
        return self.inspect_page(limit, offset)["items"]

    def account_snapshot(self):
        identity = self.actor()
        provider = self.yxer.provider_binding()
        return {"accounts": self.yxer.accounts(), "provider_binding": provider,
                "provider_fingerprint": digest(provider), "identity_fingerprint": self.identity_fingerprint(identity)}

    def doctor(self):
        identity = self.actor()
        binding = self.feishu.binding()
        vendor = self.yxer.doctor()
        accounts = self.yxer.accounts()
        return {"feishu": {"identity_verified": bool(identity.get("open_id")), "binding": binding},
                "yxer": {"doctor": vendor, "account_count": len(accounts)},
                "boundary": "single-user-local; no account authorization copied"}

    def sync(self):
        identity = self.actor()
        self.feishu.binding()
        # Live response only. No second local table holding business facts.
        records = self.feishu.records()
        if self.actor() != identity:
            raise BaseError("feishu_identity_changed")
        projected = [{**record, "source_fingerprint": digest(record)} for record in records]
        return {"source": "feishu", "records": projected, "field_map": self.config["feishu"]["fields"], "checked_at": time.time(), "identity_fingerprint": self.identity_fingerprint(identity)}

    def source_record(self, record_id):
        identity = self.actor()
        record = self.feishu.record(record_id)
        if self.actor() != identity:
            raise BaseError("feishu_identity_changed")
        return {"record": record, "source_fingerprint": digest(record), "field_map": self.config["feishu"]["fields"], "identity_fingerprint": self.identity_fingerprint(identity)}

    def plan(self, record_id, logical_fields, key, kind, expected_source=None):
        fields = {self.config["feishu"]["fields"][key]: value for key, value in logical_fields.items()}
        identity = self.actor()
        request = {"binding": self.binding_hash(), "record_id": record_id, "fields": fields, "identity": identity}
        if expected_source is not None:
            if not isinstance(expected_source, str) or len(expected_source) != 64 or any(c not in "0123456789abcdef" for c in expected_source):
                raise BaseError("invalid_source_fingerprint")
            request["expected_source"] = expected_source
        existing = self.store.lookup(kind, key)
        if existing:
            old = existing["data"]
            old_request = old.get("request") or {"binding": old["binding_hash"], "record_id": old["plan"]["record_id"], "fields": old["plan"]["fields"], "identity": old["plan"]["identity"]}
            if digest(old_request) != digest(request):
                raise BaseError("idempotency_conflict")
            return self.owned_operation(existing["id"], identity)
        if expected_source is not None and digest(self.feishu.record(record_id)) != expected_source:
            raise BaseError("feishu_source_changed")
        plan = self.feishu.plan_update(record_id, fields)
        if expected_source is not None and plan.get("before_hash") != expected_source:
            raise BaseError("feishu_source_changed")
        if plan.get("identity") != identity or self.actor() != identity:
            raise BaseError("feishu_identity_changed")
        data = {"plan": plan, "request": request, "binding_hash": self.binding_hash(), "confirmation": digest(plan)}
        op, _ = self.store.create(kind, key, request, data)
        return op

    def draft(self, record_id, title, body, key, expected_source=None):
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise BaseError("invalid_title")
        if not isinstance(body, str) or not body.strip() or len(body) > 100000:
            raise BaseError("invalid_body")
        stage = self.config["feishu"].get("stages", {}).get("review", "待审核")
        return self.plan(record_id, {"title": title, "body": body, "stage": [stage]}, key, "draft", expected_source)

    def approve(self, oid, confirmation, approved=False):
        op = self.owned_operation(oid)
        if op["kind"] == "publish" or approved is not True:
            raise BaseError("explicit_human_confirmation_required")
        if confirmation != op["data"]["confirmation"]:
            raise BaseError("confirmation_changed")
        if op["state"] in ("approved", "applied"):
            return op
        return self.store.transition(oid, {"prepared"}, "approved")

    def apply(self, oid):
        op = self.owned_operation(oid)
        if op["kind"] == "publish":
            raise BaseError("wrong_operation_kind")
        with self.store.resource_lock(self.record_resource(op["data"]["plan"]["record_id"])):
            return self._apply(oid)

    def _apply(self, oid):
        op = self.owned_operation(oid)
        if op["kind"] == "publish":
            raise BaseError("wrong_operation_kind")
        if op["state"] == "applied":
            return op
        if op["data"]["binding_hash"] != self.binding_hash():
            raise BaseError("configuration_changed")
        plan = op["data"]["plan"]
        for previous in self.store.all_operations():
            other = previous["data"].get("plan", {})
            if previous["id"] != oid and previous["state"] in ("unknown", "applying") and previous["kind"] != "publish" and other.get("record_id") == plan["record_id"]:
                binding = other.get("binding", {})
                if all(binding.get(k) == v for k, v in self.remote_binding().items()):
                    raise BaseError("prior_write_requires_verification")
        self.store.transition(oid, {"approved"}, "applying")
        data = op["data"]
        readback_received = False
        try:
            data["readback"] = self.feishu.apply_update(data["plan"])
            data["readback_source_fingerprint"] = digest(data["readback"])
            readback_received = True
            return self.store.transition(oid, {"applying"}, "applied", data)
        except BaseError as error:
            data["error"] = error.code
            uncertain = error.unknown or readback_received
            try:
                self.store.transition(oid, {"applying"}, "unknown" if uncertain else "failed", data)
            except BaseError:
                raise BaseError("write_state_persistence_unknown", unknown=True) from None
            if uncertain and not error.unknown:
                raise BaseError("write_state_persistence_unknown", unknown=True) from None
            raise
        except Exception:
            self.store.transition(oid, {"applying"}, "unknown")
            raise BaseError("write_result_unknown", unknown=True) from None

    def verify_write(self, oid):
        op = self.owned_operation(oid)
        if op["kind"] == "publish" or op["state"] not in ("unknown", "applying"):
            raise BaseError("unknown_write_required")
        if op["data"]["binding_hash"] != self.binding_hash():
            raise BaseError("configuration_changed")
        with self.store.resource_lock(self.record_resource(op["data"]["plan"]["record_id"])):
            data = op["data"]
            data["readback"] = self.feishu.verify_update(data["plan"])
            data["readback_source_fingerprint"] = digest(data["readback"])
            data["reconciliation"] = "desired_fields_verified; original_request_receipt_not_proven"
            return self.store.transition(oid, {op["state"]}, "applied", data)

    def published(self, record_id, url, key):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise BaseError("public_https_url_required")
        stage = self.config["feishu"].get("stages", {}).get("published", "已发布")
        return self.plan(record_id, {"published_url": url, "stage": [stage]}, key, "published")

    def review(self, record_id, metrics, body, key):
        if not isinstance(metrics, dict) or not {"platform", "work_id", "observed_at", "values"} <= metrics.keys():
            raise BaseError("metric_provenance_required")
        if not isinstance(metrics["values"], dict) or not isinstance(body, str) or not body.strip():
            raise BaseError("invalid_review")
        if len(body) > 100000 or any(not isinstance(metrics[k], str) or not metrics[k].strip() or len(metrics[k]) > 500 for k in ("platform", "work_id", "observed_at")):
            raise BaseError("metric_provenance_required")
        try:
            observed = datetime.fromisoformat(metrics["observed_at"].replace("Z", "+00:00"))
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError()
        except ValueError:
            raise BaseError("metric_timestamp_with_timezone_required") from None
        if any(not isinstance(k, str) or not k or (v is not None and (type(v) not in (int, float) or not math.isfinite(v))) for k, v in metrics["values"].items()):
            raise BaseError("metric_values_must_be_finite_or_missing")
        stage = self.config["feishu"].get("stages", {}).get("reviewed", "已复盘")
        return self.plan(record_id, {"metrics": json.dumps(metrics, ensure_ascii=False), "review": body, "stage": [stage]}, key, "review")

    def register_media(self, name, raw, usage):
        if not isinstance(name, str) or Path(name).name != name or "\\" in name or len(name) > 255 or any(ord(c) < 32 for c in name):
            raise BaseError("invalid_media_name")
        if usage not in ("image", "video", "cover") or not raw or len(raw) > 20 * 1024 * 1024:
            raise BaseError("web_media_limit_20mb")
        suffix = Path(name).suffix.lower()
        allowed = (".mp4", ".mov") if usage == "video" else (".png", ".jpg", ".jpeg", ".webp", ".gif")
        if suffix not in allowed:
            raise BaseError("unsupported_media")
        mid = "media_" + uuid.uuid4().hex
        folder = self.root / "media"
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / (mid + suffix)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        meta = {"id": mid, "filename": path.name, "usage": usage, "sha256": file_hash(path)}
        private_write(folder / (mid + ".json"), meta)
        return {"id": mid, "usage": usage, "sha256": meta["sha256"], "size": len(raw)}

    def query_choices(self, account_id, kind, field, query=""):
        if field not in ("music", "category") or kind not in ("imageText", "video") or not isinstance(query, str) or len(query) > 100:
            raise BaseError("invalid_choice_query")
        if field == "category" and query:
            raise BaseError("category_query_not_supported")
        identity = self.actor()
        provider = self.yxer.provider_binding(account_id)
        values = self.yxer.choices(account_id, kind, field, query)
        if self.actor() != identity or self.yxer.provider_binding(account_id) != provider:
            raise BaseError("choice_identity_or_provider_changed")
        rid = "choice_" + uuid.uuid4().hex
        def label(value):
            if isinstance(value, list):
                return " / ".join(str(v.get("yixiaoerName", "")) for v in value if isinstance(v, dict))
            return str(value.get("yixiaoerName", "")) if isinstance(value, dict) else ""
        snapshot = {"receipt_id": rid, "field": field, "account_id": account_id, "kind": kind,
                    "query": query, "identity": identity, "binding_hash": self.binding_hash(),
                    "provider_binding": provider, "created_at": time.time(),
                    "options": [{"index": index, "id": digest(value), "value": value, "label": label(value)} for index, value in enumerate(values)]}
        private_write(self.root / "choices" / (rid + ".json"), snapshot)
        return {key: snapshot[key] for key in ("receipt_id", "field", "account_id", "kind", "query", "options")}

    def media_paths(self, ids):
        files = []
        for mid in ids:
            if not isinstance(mid, str) or not mid.startswith("media_") or len(mid) != 38 or any(c not in "0123456789abcdef" for c in mid[6:]):
                raise BaseError("invalid_media_id")
            try:
                meta = json.loads((self.root / "media" / (mid + ".json")).read_text())
                path = self.root / "media" / meta["filename"]
                if path.parent != self.root / "media" or path.is_symlink() or file_hash(path) != meta["sha256"]:
                    raise BaseError("media_changed")
                files.append((str(path), meta["usage"]))
            except (OSError, ValueError, KeyError):
                raise BaseError("media_not_found") from None
        return files

    def _dynamic_fields(self, account_id, kind, fields):
        if fields is not None and not isinstance(fields, dict):
            raise BaseError("platform_fields_object_required")
        fields = copy.deepcopy(fields or {})
        query = fields.pop("music_query", "")
        selectors = {}
        for name in ("music", "category"):
            selectors[name] = (fields.pop(name + "_choice", None), fields.pop(name + "_choice_id", None), fields.pop(name + "_choice_receipt", None))
        if any(key in fields for key in ("music", "category", "images", "video", "cover", "coverKey", "accountForms", "formType")):
            raise BaseError("dynamic_or_resource_field_requires_vendor_receipt")
        for field, (index, selected_id, receipt) in selectors.items():
            if index is None and selected_id is None:
                if receipt is not None:
                    raise BaseError("vendor_choice_selector_required")
                continue
            if index is not None and selected_id is not None:
                raise BaseError("vendor_choice_selector_conflict")
            if not isinstance(receipt, str) or len(receipt) != 39 or not receipt.startswith("choice_") or any(c not in "0123456789abcdef" for c in receipt[7:]):
                raise BaseError("vendor_choice_receipt_required")
            try:
                snapshot = json.loads((self.root / "choices" / (receipt + ".json")).read_text())
            except (OSError, ValueError):
                raise BaseError("vendor_choice_receipt_missing") from None
            expected_query = query if field == "music" else ""
            if (snapshot["binding_hash"] != self.binding_hash() or snapshot["identity"] != self.actor()
                    or snapshot["provider_binding"] != self.yxer.provider_binding(account_id)
                    or (snapshot["account_id"], snapshot["kind"], snapshot["field"], snapshot["query"]) != (account_id, kind, field, expected_query)
                    or time.time() - snapshot["created_at"] > 1800):
                raise BaseError("vendor_choice_receipt_stale_or_mismatched")
            options = snapshot["options"]
            if index is not None:
                if type(index) is not int or not 0 <= index < len(options):
                    raise BaseError("vendor_choice_index_invalid")
                selected_id = options[index]["id"]
            picked = next((option for option in options if option["id"] == selected_id), None)
            if not picked or picked["id"] != digest(picked["value"]):
                raise BaseError("vendor_choice_id_invalid")
            values = self.yxer.choices(account_id, kind, field, expected_query)
            exact = next((value for value in values if digest(value) == selected_id), None)
            if exact is None:
                raise BaseError("vendor_choice_changed_refresh_required")
            fields[field] = exact
        return fields

    def publish_prepare(self, record_id, account_id, platform, kind, channel, media, fields, key):
        with self.store.resource_lock(self.record_resource(record_id)):
            return self._publish_prepare(record_id, account_id, platform, kind, channel, media, fields, key)

    def _publish_prepare(self, record_id, account_id, platform, kind, channel, media, fields, key):
        if kind not in ("imageText", "video") or channel not in ("cloud", "local"):
            raise BaseError("invalid_publish_type_or_channel")
        if not media or len(media) > 21:
            raise BaseError("media_required")
        if kind == "video" and sorted(usage for _, usage in media) != ["cover", "video"]:
            raise BaseError("video_and_cover_required")
        if kind == "imageText" and any(usage != "image" for _, usage in media):
            raise BaseError("image_media_required")
        file_info = [inspect_media(file, usage) for file, usage in media]
        identity = self.actor()
        provider = self.yxer.provider_binding(account_id)
        source = self.feishu.record(record_id)
        mapping = self.config["feishu"]["fields"]
        title, body = text(source["fields"].get(mapping["title"])), text(source["fields"].get(mapping["body"]))
        if not title.strip() or not body.strip():
            raise BaseError("feishu_content_required")
        inputs = {"binding": self.binding_hash(), "remote_binding": self.remote_binding(), "record_id": record_id, "source": source,
                  "identity": identity, "account_id": account_id, "platform": platform,
                  "kind": kind, "channel": channel, "files": file_info, "fields": fields or {}, "provider_binding": provider}
        inputs["publication_hash"] = digest({"title": title, "body": body, "kind": kind,
                                             "files": [{"usage": f["usage"], "sha256": f["sha256"]} for f in file_info]})
        data = {"inputs": inputs}
        request = {key: inputs[key] for key in ("binding", "remote_binding", "record_id", "identity", "account_id", "platform", "kind", "channel", "fields", "provider_binding", "publication_hash")}
        request["files"] = [{"usage": f["usage"], "sha256": f["sha256"]} for f in file_info]
        data["request"] = request
        op, created = self.store.create("publish", key, request, data, "preparing")
        if not created:
            return op
        try:
            self._guard_existing_publication(op)
            self.yxer.contract(platform, kind)
            dynamic = self._dynamic_fields(account_id, kind, fields)
            self.yxer.validate_fields(platform, kind, account_id, title, body, dynamic, [f["usage"] for f in file_info])
            resources = []
            folder = self.root / "publish" / op["id"]
            manifest = self.frozen_media.freeze(op["id"], file_info)
            data.update(frozen_media=manifest, media_review_digest=digest(manifest))
            self.store.transition(op["id"], {"preparing"}, "preparing", data)
            for file in manifest:
                self.frozen_media.verify(op["id"], manifest)
                resource = self.yxer.upload(str(self.frozen_media.path(op["id"], file)), platform, file["usage"])
                self.frozen_media.verify(op["id"], manifest)
                resources.append({"usage": file["usage"], "resource": resource})
                private_write(folder / ("upload-" + str(len(resources)) + ".json"), {"source_hash": file["sha256"], "usage": file["usage"], "resource": resource})
                data["resources"] = copy.deepcopy(resources)
                self.store.transition(op["id"], {"preparing"}, "preparing", data)
            payload = self.yxer.prepare_payload(platform, kind, channel, account_id, title, body, resources, dynamic)
            private_write(folder / "payload.json", payload)
            data.update(payload=payload, payload_path=str(folder / "payload.json"), resources=resources)
            # Persist uploaded resources before remote preflight, so errors do not lose provenance.
            self.store.transition(op["id"], {"preparing"}, "preparing", data)
            data["preflight"] = self.yxer.preflight(payload, data["payload_path"])
            data["prepared_at"] = time.time()
            data["confirmation"] = digest({"inputs": inputs, "payload": payload, "resources": resources, "frozen_media": manifest})
            return self.store.transition(op["id"], {"preparing"}, "ready", data)
        except BaseError as error:
            data["error"] = error.code
            self.store.transition(op["id"], {"preparing"}, "preparation_failed", data)
            raise
        except Exception:
            self.store.transition(op["id"], {"preparing"}, "preparation_failed", data)
            raise BaseError("publish_preparation_failed") from None

    def _verified_publish_media(self, op, verify_files=True):
        if op["kind"] != "publish":
            raise BaseError("wrong_operation_kind")
        data = op["data"]
        manifest = data.get("frozen_media")
        if not manifest or not data.get("media_review_digest"):
            raise BaseError("legacy_publish_media_requires_new_review")
        if (not isinstance(data.get("payload"), dict) or not isinstance(data.get("resources"), list)
                or not isinstance(data.get("confirmation"), str) or type(data.get("prepared_at")) not in (int, float)):
            raise BaseError("publish_review_not_ready")
        if digest(manifest) != data["media_review_digest"]:
            raise BaseError("frozen_media_manifest_changed")
        if data.get("confirmation") != digest({"inputs": data["inputs"], "payload": data["payload"], "resources": data["resources"], "frozen_media": manifest}):
            raise BaseError("publish_confirmation_changed")
        if verify_files:
            self.frozen_media.verify(op["id"], manifest)
        return manifest

    def media_review(self, oid, verify_files=True):
        op = self.owned_operation(oid)
        manifest = self._verified_publish_media(op, verify_files)
        data, inputs = op["data"], op["data"]["inputs"]
        fields, mapping = inputs["source"]["fields"], self.config["feishu"]["fields"]
        if self.actor() != inputs["identity"]:
            raise BaseError("feishu_identity_changed")
        return {"operation_id": oid, "confirmation_digest": data["confirmation"],
                "media_review_digest": data["media_review_digest"], "title": text(fields.get(mapping["title"])),
                "body": text(fields.get(mapping["body"])), "platform": inputs["platform"],
                "account_id": inputs["account_id"], "kind": inputs["kind"], "channel": inputs["channel"],
                "expires_at": data["prepared_at"] + 1800,
                "media": [{k: entry[k] for k in ("index", "name", "usage", "size", "sha256", "mime")} |
                          {"preview_url": f"/api/operations/{oid}/media/{entry['index']}"} for entry in manifest]}

    def media_bytes(self, oid, index, start=None, end=None):
        op = self.owned_operation(oid)
        # read() binds the stored manifest and twice hashes the selected bytes.
        # Avoid rehashing every other asset for each small video Range request.
        manifest = self._verified_publish_media(op, verify_files=False)
        packet = self.frozen_media.read(oid, manifest, index, start, end)
        if self.actor() != op["data"]["inputs"]["identity"]:
            raise BaseError("feishu_identity_changed")
        return packet

    @contextmanager
    def media_stream(self, oid, index, start=None, end=None):
        op = self.owned_operation(oid)
        manifest = self._verified_publish_media(op, verify_files=False)
        if not self.media_slots.acquire(blocking=False):
            raise BaseError("media_stream_busy")
        try:
            with self.frozen_media.open_snapshot(oid, manifest, index, start, end) as packet:
                if self.actor() != op["data"]["inputs"]["identity"]:
                    raise BaseError("feishu_identity_changed")
                yield packet
                if self.actor() != op["data"]["inputs"]["identity"]:
                    raise BaseError("feishu_identity_changed")
        finally:
            self.media_slots.release()

    def export_publish_review(self, oid, destination=None):
        from .review_package import export_review, verify_review_package
        op = self.owned_operation(oid)
        if op["kind"] != "publish":
            raise BaseError("wrong_operation_kind")
        with self.store.resource_lock(self.record_resource(op["data"]["inputs"]["record_id"])):
            review = self.media_review(oid)
            target = Path(destination).expanduser() if destination else self.root / "publish" / oid / "review"
            if target.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
                raise BaseError("review_package_must_be_outside_source")
            if destination is None and target.exists():
                result = verify_review_package(review, target)
            else:
                result = export_review(review, lambda index: self.media_stream(oid, index), target)
            if self.actor() != op["data"]["inputs"]["identity"]:
                raise BaseError("feishu_identity_changed")
            return {**result, "operation_id": oid, "media_review_digest": review["media_review_digest"]}

    def publish_submit(self, oid, confirmation, approved=False, reviewed_media_digest=None):
        op = self.owned_operation(oid, check_provider=True)
        if op["kind"] != "publish":
            raise BaseError("wrong_operation_kind")
        with self.store.resource_lock(self.record_resource(op["data"]["inputs"]["record_id"])):
            return self._publish_submit(oid, confirmation, approved, reviewed_media_digest)

    def _guard_existing_publication(self, op):
        source = op["data"]["inputs"]
        for previous in self.store.all_operations():
            if previous["kind"] != "publish" or previous["id"] == op["id"]:
                continue
            other = previous["data"]["inputs"]
            if (other["remote_binding"], other["record_id"], other["account_id"]) != (source["remote_binding"], source["record_id"], source["account_id"]):
                continue
            if previous["state"] in ("unknown", "submitting"):
                raise BaseError("prior_publish_requires_verification")
            if previous["state"] == "submitted" and other["publication_hash"] == source["publication_hash"]:
                raise BaseError("same_source_already_submitted")

    def _publish_submit(self, oid, confirmation, approved=False, reviewed_media_digest=None):
        op = self.owned_operation(oid, check_provider=True)
        if op["kind"] != "publish" or approved is not True:
            raise BaseError("explicit_publish_confirmation_required")
        data, inputs = op["data"], op["data"]["inputs"]
        if confirmation != data.get("confirmation"):
            raise BaseError("confirmation_changed")
        if op["state"] == "submitted":
            return op
        if op["state"] != "ready":
            raise BaseError("publish_result_requires_verification")
        self._verified_publish_media(op)
        if reviewed_media_digest != data["media_review_digest"]:
            raise BaseError("explicit_frozen_media_review_required")
        if confirmation != data["confirmation"]:
            raise BaseError("confirmation_changed")
        if time.time() - data["prepared_at"] > 1800:
            raise BaseError("publish_preflight_expired")
        if inputs["binding"] != self.binding_hash() or self.actor() != inputs["identity"]:
            raise BaseError("operator_or_configuration_changed")
        if digest(self.feishu.record(inputs["record_id"])) != digest(inputs["source"]):
            raise BaseError("feishu_source_changed")
        if json.loads(Path(data["payload_path"]).read_text()) != data["payload"]:
            raise BaseError("publish_payload_changed")
        self._guard_existing_publication(op)
        self.yxer.revalidate(data["payload"], data["payload_path"], inputs["fields"])
        if self.actor() != inputs["identity"] or digest(self.feishu.record(inputs["record_id"])) != digest(inputs["source"]):
            raise BaseError("feishu_source_or_identity_changed")
        if json.loads(Path(data["payload_path"]).read_text()) != data["payload"]:
            raise BaseError("publish_payload_changed")
        if time.time() - data["prepared_at"] > 1800:
            raise BaseError("publish_preflight_expired")
        if self.yxer.provider_binding(inputs["account_id"]) != inputs["provider_binding"]:
            raise BaseError("publish_provider_changed")
        self._verified_publish_media(op)
        self.store.transition(oid, {"ready"}, "submitting")
        receipt_received = False
        try:
            receipt = self.yxer.submit(data["payload"], data["payload_path"])
            data["receipt"] = receipt
            receipt_received = True
            private_write(self.root / "publish" / oid / "receipt.json", receipt)
        except BaseError as error:
            if hasattr(error, "vendor_receipt"):
                private_write(self.root / "publish" / oid / "unresolved-receipt.json", error.vendor_receipt)
                data["vendor_receipt"] = error.vendor_receipt
            data["error"] = error.code
            uncertain = error.unknown or receipt_received
            self.store.transition(oid, {"submitting"}, "unknown" if uncertain else "failed", data)
            if uncertain and not error.unknown:
                raise BaseError("publish_receipt_persistence_unknown", unknown=True) from None
            raise
        except Exception:
            self.store.transition(oid, {"submitting"}, "unknown")
            raise BaseError("publish_result_unknown", unknown=True) from None
        try:
            return self.store.transition(oid, {"submitting"}, "submitted", data)
        except BaseError:
            try:
                self.store.transition(oid, {"submitting"}, "unknown", data)
            except BaseError:
                pass
            raise BaseError("publish_state_persistence_unknown", unknown=True) from None

    def publish_verify(self, oid):
        op = self.owned_operation(oid, check_provider=True)
        if op["kind"] != "publish":
            raise BaseError("wrong_operation_kind")
        if op["state"] not in ("submitted", "unknown", "submitting"):
            raise BaseError("publish_not_submitted")
        with self.store.resource_lock(self.record_resource(op["data"]["inputs"]["record_id"])):
            return self._publish_verify(oid)

    def _publish_verify(self, oid):
        op = self.owned_operation(oid, check_provider=True)
        receipt = op["data"].get("receipt", {})
        if not receipt.get("taskSetId"):
            path = self.root / "publish" / oid / "receipt.json"
            if path.is_file():
                receipt = json.loads(path.read_text())
        task = receipt.get("taskSetId")
        if not task:
            raise BaseError("task_receipt_missing_no_automatic_replay")
        data = op["data"]
        data["receipt"] = receipt
        data["verification"] = {"details": self.yxer.details(task), "public_visible": None, "checked_at": time.time()}
        # Query results are evidence, not a substitute for the user's public-page check.
        return self.store.transition(oid, {op["state"]}, op["state"], data)
