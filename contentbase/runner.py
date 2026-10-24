"""No shell, credential copying, error-body echo, or automatic vendor-write retries."""
import json
import math
import re
import subprocess
from .errors import BaseError


def _packet(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _classified(error, argv, write):
    error = error if isinstance(error, dict) else {}
    labels = [error.get(key) for key in ("type", "subtype", "category", "code")]
    signal = " ".join(value.lower() for value in labels if isinstance(value, str))
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    schema_query = any(argv[i:i + 2] == ["schema", "get"] for i in range(len(argv) - 1))
    if "unsupported" in signal or "not_supported" in signal or (schema_query and error.get("code") == "YIXIAOER_USAGE_ERR" and {"platform", "type"} <= details.keys()):
        category = "unsupported"
    elif "permission" in signal or "forbidden" in signal or "access_denied" in signal:
        category = "permission"
    elif any(value in signal for value in ("authorization", "authentication", "unauthorized", "auth_required", "token_expired", "auth_error")):
        category = "auth"
    elif any(value in signal for value in ("rate_limit", "ratelimit", "too_many", "throttl")):
        category = "rate_limit"
    elif any(value in signal for value in ("network", "provider_unavailable", "service_unavailable", "connection", "timeout", "dns", "upstream", "gateway")):
        category = "provider_unavailable"
    elif any(value in signal for value in ("validation", "usage", "invalid_argument", "missing_required", "file_not_found")):
        category = "validation"
    else:
        category = "failed"
    safe = {}
    scopes = error.get("missing_scopes", details.get("missing_scopes", []))
    if isinstance(scopes, list):
        scopes = [s for s in scopes if isinstance(s, str) and re.fullmatch(r"[a-z][a-z0-9_.-]*(?::[a-z0-9_.-]+)+", s)][:50]
        if scopes:
            safe["missing_scopes"] = scopes
    delay = error.get("retry_after", details.get("retry_after"))
    if type(delay) in (int, float) and math.isfinite(delay) and 0 <= delay <= 86400:
        safe["retry_after"] = delay
    # Known validation/auth rejections precede the business action. Transport/provider
    # failures still have no evidence whether a write reached the downstream system.
    uncertain = bool(write and category not in ("validation", "auth", "permission", "unsupported"))
    return BaseError("cli_" + category, unknown=uncertain, category=category, details=safe)


class Runner:
    def __call__(self, argv, *, timeout=60, write=False):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise BaseError("cli_not_installed", category="unsupported") from None
        except UnicodeError:
            raise BaseError("cli_invalid_response", unknown=write) from None
        except (OSError, subprocess.TimeoutExpired):
            raise BaseError("cli_execution_unknown" if write else "cli_transport_failed", unknown=write, category="provider_unavailable") from None
        stdout = _packet(result.stdout)
        stderr = _packet(result.stderr)
        if isinstance(stdout, dict) and stdout.get("ok") is False:
            raise _classified(stdout.get("error"), argv, write) from None
        if result.returncode:
            if isinstance(stderr, dict) and stderr.get("ok") is False:
                raise _classified(stderr.get("error"), argv, write) from None
            raise BaseError("cli_failed", unknown=write, category="failed") from None
        if stdout is None:
            if isinstance(stderr, dict) and stderr.get("ok") is False:
                raise _classified(stderr.get("error"), argv, write) from None
            # stderr may contain notices, but never substitutes for a success packet.
            raise BaseError("cli_invalid_response", unknown=write) from None
        return stdout


def unwrap(value):
    if isinstance(value, dict) and value.get("ok") is True:
        return value.get("data", {})
    return value
