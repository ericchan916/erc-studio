"""lark-cli OAuth device-flow manager."""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import time
import uuid
from typing import List

from bridge_utils import SCOPE_PATTERN


@dataclass(frozen=True)
class AuthRequest:
    auth_id: str
    scopes: List[str]
    device_code: str
    verification_url: str
    expires_at: float


class LarkAuthManager:
    def __init__(self, profile: str, lark_cli: str = "lark-cli"):
        self.profile = profile
        self.lark_cli = lark_cli

    def start(self, scopes: List[str]) -> AuthRequest:
        if not scopes or any(not SCOPE_PATTERN.fullmatch(scope) for scope in scopes):
            raise ValueError("invalid OAuth scope request")
        result = subprocess.run(
            [
                self.lark_cli,
                "--profile",
                self.profile,
                "auth",
                "login",
                "--scope",
                " ".join(scopes),
                "--no-wait",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"OAuth start failed ({result.returncode}): {result.stderr[-500:]}"
            )
        payload = json.loads(result.stdout)
        device_code = str(payload.get("device_code") or "")
        verification_url = str(
            payload.get("verification_url")
            or payload.get("verification_uri_complete")
            or payload.get("verification_uri")
            or ""
        )
        expires_in = int(payload.get("expires_in") or 600)
        if not device_code or not verification_url:
            raise RuntimeError("OAuth start returned incomplete device flow")
        return AuthRequest(
            auth_id=f"auth_{uuid.uuid4().hex}",
            scopes=scopes,
            device_code=device_code,
            verification_url=verification_url,
            expires_at=time.time() + expires_in,
        )

    def complete(self, request: AuthRequest) -> None:
        timeout = max(30, int(request.expires_at - time.time()) + 30)
        result = subprocess.run(
            [
                self.lark_cli,
                "--profile",
                self.profile,
                "auth",
                "login",
                "--device-code",
                request.device_code,
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"OAuth completion failed ({result.returncode}): {result.stderr[-500:]}"
            )
        check = subprocess.run(
            [
                self.lark_cli,
                "--profile",
                self.profile,
                "auth",
                "check",
                "--scope",
                " ".join(request.scopes),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check.returncode != 0:
            raise RuntimeError(f"OAuth scope verification failed: {check.stdout[-500:]}")

