"""Loopback-only signed studio/Bridge transport. No platform credentials or generic RPC.

This stdlib leaf is installed byte-identically beside both processes. Request
replays are handled by the receiving durable operation ledger, not a new model run.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

MAX_PACKET = 1048576


class ProtocolError(Exception):
    def __init__(self, code, status=0, uncertain=False):
        super().__init__(code)
        self.code, self.status, self.uncertain = code, status, uncertain


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError):
        raise ProtocolError('studio_packet_invalid', 400) from None


def identifier(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ProtocolError('studio_identifier_invalid', 400) from None
    return value


def read_key(filename):
    path = Path(filename)
    if not path.is_absolute() or path.is_symlink():
        raise ProtocolError('studio_ipc_key_invalid', 503)
    # Open the same file we validate; no symlink-follow race at the leaf.
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(str(path), flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ProtocolError('studio_ipc_key_invalid', 503)
            data = os.read(descriptor, 256).strip()
        finally:
            os.close(descriptor)
    except OSError:
        raise ProtocolError('studio_ipc_key_unavailable', 503) from None
    if not re.fullmatch(b'[a-f0-9]{64}', data):
        raise ProtocolError('studio_ipc_key_invalid', 503)
    return data


def endpoint(origin):
    try:
        value = urllib.parse.urlsplit(origin)
        if value.scheme != 'http' or value.hostname != '127.0.0.1' or value.username or value.password or value.query or value.fragment or value.path not in ('', '/') or not value.port or not 1024 <= value.port <= 65535:
            raise ValueError()
    except (ValueError, TypeError):
        raise ProtocolError('studio_loopback_endpoint_required', 503) from None
    return 'http://127.0.0.1:'+str(value.port)


def signature(key, method, path, timestamp, nonce, raw):
    digest = hashlib.sha256(raw).hexdigest()
    return hmac.new(key, '\n'.join([method, path, timestamp, nonce, digest]).encode(), hashlib.sha256).hexdigest()


def headers_for(key, method, path, raw, clock=time.time, nonce=None):
    timestamp, nonce = str(int(clock())), nonce or uuid.uuid4().hex
    if not re.fullmatch(r'[a-f0-9]{32}', nonce):
        raise ProtocolError('studio_packet_invalid', 400)
    return {'Content-Type': 'application/json', 'X-Studio-Time': timestamp,
            'X-Studio-Nonce': nonce, 'X-Studio-Signature': signature(key, method, path, timestamp, nonce, raw)}


def verify(key, method, path, raw, headers, clock=time.time):
    if any(headers.get(name) is not None for name in ('Origin', 'Cookie', 'Forwarded', 'X-Forwarded-For', 'X-Forwarded-Proto')):
        raise ProtocolError('studio_internal_origin_denied', 403)
    timestamp = headers.get('X-Studio-Time', '')
    nonce = headers.get('X-Studio-Nonce', '')
    supplied = headers.get('X-Studio-Signature', '')
    if not re.fullmatch(r'[0-9]{1,12}', timestamp) or abs(clock()-int(timestamp)) > 60 or not re.fullmatch(r'[a-f0-9]{32}', nonce) or not re.fullmatch(r'[a-f0-9]{64}', supplied):
        raise ProtocolError('studio_internal_authentication_required', 401)
    expected = signature(key, method, path, timestamp, nonce, raw)
    if not hmac.compare_digest(expected, supplied):
        raise ProtocolError('studio_internal_authentication_required', 401)


def verify_response(key, path, raw, headers, nonce, clock=time.time):
    # A process that happens to bind the loopback port is not a trusted Bridge.
    # Responses must prove key possession and bind to this particular request.
    if headers.get('X-Studio-Nonce') != nonce:
        raise ProtocolError('studio_response_authentication_failed', uncertain=True)
    try:
        verify(key, 'RESPONSE', path, raw, headers, clock)
    except ProtocolError:
        raise ProtocolError('studio_response_authentication_failed', uncertain=True) from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


def request(origin, path, body, key_file, timeout=30):
    origin = endpoint(origin)
    if not isinstance(path, str) or not path.startswith('/internal/studio/') or '?' in path or '#' in path or '..' in path:
        raise ProtocolError('studio_internal_route_invalid', 400)
    raw = canonical(body)
    if len(raw) > MAX_PACKET:
        raise ProtocolError('studio_packet_too_large', 413)
    key = read_key(key_file)
    signed = headers_for(key, 'POST', path, raw)
    req = urllib.request.Request(origin+path, data=raw, headers=signed, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            data = response.read(MAX_PACKET+1)
            if len(data) > MAX_PACKET:
                raise ProtocolError('studio_response_too_large', uncertain=True)
            verify_response(key, path, data, response.headers, signed['X-Studio-Nonce'])
            packet = json.loads(data)
            if not isinstance(packet, dict):
                raise ValueError()
            return packet
    except urllib.error.HTTPError as error:
        # Only an explicit pre-execution rejection proves nothing started.
        try:
            data = error.read(4096)
            verify_response(key, path, data, error.headers, signed['X-Studio-Nonce'])
            value = json.loads(data)
            rejected = value.get('execution_started') is False
            code = value.get('code', '')
            if not isinstance(code, str) or not re.fullmatch('[a-z0-9_]{1,80}', code):
                code = 'studio_transport_http_'+str(error.code)
        except Exception:
            code, rejected = 'studio_transport_http_'+str(error.code), False
        finally:
            error.close()
        raise ProtocolError(code, error.code, uncertain=not rejected) from None
    except ProtocolError:
        raise
    except Exception:
        raise ProtocolError('studio_execution_transport_unknown', uncertain=True) from None
