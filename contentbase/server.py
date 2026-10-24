import base64
import hmac
import json
import re
import secrets
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from .config import private_write
from .errors import BaseError
from .store import digest


def media_range(value, size):
    if value is None:
        return None, None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()) or any(len(item) > 20 for item in match.groups()):
        raise BaseError("invalid_media_range")
    left, right = match.groups()
    if left:
        start, end = int(left), int(right) if right else size - 1
        if start >= size or end < start:
            raise BaseError("invalid_media_range")
        return start, min(end, size - 1)
    suffix = int(right)
    if suffix == 0:
        raise BaseError("invalid_media_range")
    return max(0, size - suffix), size - 1


def error_value(error):
    value = {"code": error.code, "unknown": error.unknown}
    if error.category:
        value["category"] = error.category
    if error.details:
        value["details"] = error.details
    if hasattr(error, "identity_fingerprint"):
        value["identity_fingerprint"] = error.identity_fingerprint
    return value


def make_server(service, port=8787, token=None):
    token = token or secrets.token_urlsafe(32)
    public = Path(__file__).parent / "web"
    workbench = Path(__file__).resolve().parents[1] / "workbench"
    inspect_page = workbench / "web" / "inspection.html"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def response(self, status, value):
            raw = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def guard(self):
            host = self.headers.get("Host", "")
            allowed = {"127.0.0.1:%s" % self.server.server_port, "localhost:%s" % self.server.server_port}
            if host not in allowed:
                raise BaseError("invalid_host")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + item for item in allowed}:
                raise BaseError("invalid_origin")

        def auth(self):
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + token).encode("utf-8")):
                raise BaseError("authentication_required")

        def client_context(self, path, required=False):
            expected = self.headers.get("X-Contentbase-Identity")
            if not expected and required:
                raise BaseError("client_identity_required")
            if required and (path.startswith("/api/publish/") or path == "/api/choices"):
                provider = self.headers.get("X-Contentbase-Provider")
                if not provider:
                    raise BaseError("client_provider_required")
                if provider != digest(service.yxer.provider_binding()):
                    raise BaseError("client_provider_changed")
            return service.client_identity(expected) if expected else nullcontext()

        def do_GET(self):
            streaming_headers_sent = False
            try:
                self.guard()
                path = urlparse(self.path).path
                # The source distribution opens the original workbench, explicitly
                # read-only. The wheel keeps its compatibility controller; the full
                # Electron workbench is distributed as source, not a Python wheel.
                if path == "/" and inspect_page.is_file():
                    import re as html_re
                    from scripts.privacy_audit import read_source
                    raw = read_source(workbench.parent, "workbench/web/inspection.html").decode("utf-8")
                    raw = html_re.sub(r'(src|href)="(?!https?:|/|#)([^\"]+)"',
                                      lambda match: match[1] + '="/workbench/web/' + match[2] + '"', raw)
                    raw = raw.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers(); self.wfile.write(raw); return
                if path.startswith("/workbench/") or path.startswith("/vendor/"):
                    if not inspect_page.is_file():
                        raise BaseError("invalid_static_asset")
                    from scripts.privacy_audit import PrivacyError, manifest_files, read_source
                    relative = path.lstrip("/") if path.startswith("/workbench/") else "workbench/web/" + path.lstrip("/")
                    root = Path(__file__).resolve().parents[1]
                    allowed = relative.startswith("workbench/web/") and relative in manifest_files(root)
                    if not allowed or Path(relative).suffix not in {".js", ".css", ".svg", ".html"}:
                        raise BaseError("invalid_static_asset")
                    try: raw = read_source(root, relative)
                    except PrivacyError: raise BaseError("invalid_static_asset") from None
                    import mimetypes
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(relative)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers(); self.wfile.write(raw); return
                if path == "/control":
                    path = "/"
                if path in ("/", "/app.js", "/style.css"):
                    name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
                    raw = (public / name).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[name])
                    self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if path == "/api/health":
                    self.response(200, {"ok": True, "data": {"status": "running", "business_verified": False}})
                    return
                self.auth()
                query = parse_qs(urlparse(self.path).query)
                media_match = re.fullmatch(r"/api/operations/(op_[a-f0-9]{32})/media/(0|[1-9]\d*)", path)
                review_match = re.fullmatch(r"/api/operations/(op_[a-f0-9]{32})/review", path)
                with self.client_context(path, required=bool(media_match or review_match)):
                    if path in ("/api/operations", "/api/operation-page"):
                        limit, offset = int(query.get("limit", ["200"])[0]), int(query.get("offset", ["0"])[0])
                        result = service.inspect_page(limit, offset) if path == "/api/operation-page" else service.inspect(limit=limit, offset=offset)
                    elif media_match:
                        oid, index = media_match.group(1), int(media_match.group(2))
                        review = service.media_review(oid, verify_files=False)
                        if not 0 <= index < len(review["media"]):
                            raise BaseError("invalid_media_index")
                        size = review["media"][index]["size"]
                        try:
                            start, end = media_range(self.headers.get("Range"), size)
                        except BaseError:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.send_header("Content-Length", "0")
                            self.send_header("Cache-Control", "no-store")
                            self.end_headers()
                            return
                        with service.media_stream(oid, index, start, end) as result:
                            self.connection.settimeout(30)
                            self.send_response(206 if start is not None else 200)
                            self.send_header("Content-Type", result["mime"])
                            self.send_header("Content-Length", str(result["content_length"]))
                            self.send_header("Accept-Ranges", "bytes")
                            if start is not None:
                                self.send_header("Content-Range", f"bytes {result['start']}-{result['end']}/{result['size']}")
                            self.send_header("Cache-Control", "no-store")
                            self.send_header("X-Content-Type-Options", "nosniff")
                            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
                            streaming_headers_sent = True
                            self.end_headers()
                            remaining = result["content_length"]
                            while remaining:
                                block = result["stream"].read(min(1024 * 1024, remaining))
                                if not isinstance(block, bytes) or not block or len(block) > remaining:
                                    raise BaseError("media_stream_incomplete")
                                self.wfile.write(block)
                                remaining -= len(block)
                            if result["stream"].read(1):
                                raise BaseError("media_stream_size_changed")
                        return
                    elif review_match:
                        result = service.media_review(review_match.group(1))
                    elif path.startswith("/api/operations/"):
                        result = service.inspect(path.removeprefix("/api/operations/"))
                    elif path.startswith("/api/records/"):
                        result = service.source_record(path.removeprefix("/api/records/"))
                    elif path == "/api/accounts":
                        result = service.account_snapshot()
                    else:
                        raise BaseError("not_found")
                self.response(200, {"ok": True, "data": result})
            except BaseError as error:
                if streaming_headers_sent:
                    self.close_connection = True
                    return
                self.response(401 if error.code == "authentication_required" else 429 if error.code == "media_stream_busy" else 409 if error.code in ("client_identity_changed", "workspace_operator_changed", "workspace_operator_ambiguous") else 400, {"ok": False, "error": error_value(error)})
            except (ValueError, TypeError):
                if streaming_headers_sent:
                    self.close_connection = True
                    return
                self.response(400, {"ok": False, "error": {"code": "invalid_pagination"}})
            except Exception:
                if streaming_headers_sent:
                    self.close_connection = True
                    return
                self.response(500, {"ok": False, "error": {"code": "internal_error"}})

        def do_POST(self):
            try:
                self.guard()
                self.auth()
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 29 * 1024 * 1024:
                    raise BaseError("request_size_limit")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise BaseError("json_required")
                self.connection.settimeout(30)
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise BaseError("json_object_required")
                path = urlparse(self.path).path
                with self.client_context(path, required=path not in ("/api/doctor", "/api/sync")):
                    result = self.route_post(path, body)
                self.response(200, {"ok": True, "data": result})
            except BaseError as error:
                self.response(401 if error.code == "authentication_required" else 409, {"ok": False, "error": error_value(error)})
            except (ValueError, TypeError, KeyError):
                self.response(400, {"ok": False, "error": {"code": "invalid_request"}})
            except Exception:
                self.response(500, {"ok": False, "error": {"code": "internal_error"}})

        def route_post(self, path, body):
            review_export = re.fullmatch(r"/api/operations/(op_[a-f0-9]{32})/review-export", path)
            if review_export:
                if body:
                    raise BaseError("review_export_has_fixed_destination")
                return service.export_publish_review(review_export.group(1))
            if path == "/api/doctor":
                return service.doctor()
            if path == "/api/sync":
                return service.sync()
            if path == "/api/draft":
                return service.draft(body["record_id"], body["title"], body["body"], body["key"], body.get("expected_source"))
            if path == "/api/approve":
                return service.approve(body["operation_id"], body["digest"], body.get("approved", False))
            if path == "/api/apply":
                return service.apply(body["operation_id"])
            if path == "/api/verify-write":
                return service.verify_write(body["operation_id"])
            if path == "/api/media":
                return service.register_media(body["name"], base64.b64decode(body["base64"], validate=True), body["usage"])
            if path == "/api/choices":
                return service.query_choices(body["account_id"], body["kind"], body["field"], body.get("query", ""))
            if path == "/api/publish/prepare":
                return service.publish_prepare(body["record_id"], body["account_id"], body["platform"], body["kind"], body["channel"],
                                               service.media_paths(body["media_ids"]), body.get("fields", {}), body["key"])
            if path == "/api/publish/submit":
                return service.publish_submit(body["operation_id"], body["digest"], body.get("approved", False), body.get("reviewed_media_digest"))
            if path == "/api/publish/verify":
                return service.publish_verify(body["operation_id"])
            if path == "/api/published":
                return service.published(body["record_id"], body["url"], body["key"])
            if path == "/api/review":
                return service.review(body["record_id"], body["metrics"], body["body"], body["key"])
            raise BaseError("not_found")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server, token


def serve(service, port):
    server, token = make_server(service, port)
    private_write(service.root / "web-access.json", {"token": token})
    print("工作台：http://127.0.0.1:%d" % server.server_port, flush=True)
    print("在工作台输入私有状态目录 web-access.json 中的 token；此文件勿分享或提交。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
