#!/usr/bin/env python3
"""Notification lifecycle state machine for the Cyber ContentBase notification center."""
from __future__ import annotations

STATE_PENDING = "pending"
STATE_SENDING = "sending"
STATE_SENT = "sent"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_EXPIRED = "expired"

ALL_STATES = (
    STATE_PENDING,
    STATE_SENDING,
    STATE_SENT,
    STATE_FAILED,
    STATE_CANCELLED,
    STATE_EXPIRED,
)
TERMINAL_STATES = frozenset({STATE_SENT, STATE_CANCELLED, STATE_EXPIRED})
UNSENT_STATES = frozenset({STATE_PENDING, STATE_SENDING, STATE_FAILED})

ALLOWED_TRANSITIONS = {
    STATE_PENDING: frozenset({STATE_SENDING, STATE_CANCELLED, STATE_EXPIRED}),
    STATE_SENDING: frozenset({STATE_SENT, STATE_FAILED, STATE_PENDING}),
    STATE_FAILED: frozenset({STATE_PENDING, STATE_CANCELLED, STATE_EXPIRED}),
    STATE_SENT: frozenset(),
    STATE_CANCELLED: frozenset(),
    STATE_EXPIRED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a notification state change is not allowed."""


def ensure_transition(current: str, target: str) -> None:
    if current not in ALL_STATES:
        raise InvalidTransition(f"unknown_state:{current}")
    if target not in ALL_STATES:
        raise InvalidTransition(f"unknown_state:{target}")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"transition_not_allowed:{current}->{target}")
