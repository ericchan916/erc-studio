"""Shared domain validation. No storage, network or caller-controlled identity."""
import hashlib, re, time, uuid

STATES = ('queued', 'running', 'waiting_authorization', 'waiting_confirmation',
          'succeeded', 'failed', 'cancel_requested', 'cancelled', 'execution_unknown')

class Problem(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)

def uid(value):
    try:
        if str(uuid.UUID(str(value))) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise Problem(400, 'invalid_request')
    return value

def shape(value, allowed, required=()):
    if (not isinstance(value, dict) or set(value)-set(allowed)
            or not set(required) <= set(value)):
        raise Problem(400, 'invalid_request')
    return value

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
