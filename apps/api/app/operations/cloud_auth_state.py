"""Pure transitions, fixed deadlines, and record-bound encryption context."""

from datetime import datetime, timedelta
from uuid import UUID

from app.operations.cloud_auth_schemas import (
    AUTH_PROVIDERS,
    TERMINAL_AUTH_STATES,
    AuthMode,
    AuthProvider,
    AuthState,
)

AUTH_LIFETIME = timedelta(minutes=10)
_SAFE_TERMINAL_EVENTS: dict[str, AuthState] = {
    "expire": "expired",
    "cancel": "cancelled",
    "fail": "failed",
}


class AuthTransitionError(ValueError):
    pass


class AuthExpired(AuthTransitionError):
    pass


_TRANSITIONS: dict[tuple[AuthState, str], AuthState] = {
    ("creating", "import_created"): "awaiting_import",
    ("awaiting_import", "verify_import"): "verifying_import",
    ("verifying_import", "authorized"): "authorized",
    ("creating", "created"): "awaiting_confirmation",
    ("awaiting_confirmation", "authorized"): "authorized",
    ("authorized", "configure"): "configuring",
    ("configuring", "configured"): "test_pending",
    ("test_pending", "test"): "test_pending",
    ("test_pending", "test_passed"): "ready",
    ("ready", "configure"): "configuring",
    ("ready", "test"): "test_pending",
    ("ready", "apply"): "applying",
    ("applying", "applied"): "applied",
}


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("authorization deadlines must be timezone-aware")


def scan_deadline(*, now: datetime, provider_expires_at: datetime) -> datetime:
    _aware(now)
    _aware(provider_expires_at)
    return min(provider_expires_at, now + AUTH_LIFETIME)


def configuration_deadline(authorized_at: datetime) -> datetime:
    """Call once on authorization; persist this result, never renew on polling."""
    _aware(authorized_at)
    return authorized_at + AUTH_LIFETIME


def transition(current: AuthState, event: str, *, now: datetime, expires_at: datetime) -> AuthState:
    _aware(now)
    _aware(expires_at)
    if current in TERMINAL_AUTH_STATES:
        raise AuthTransitionError("authorization session is terminal")
    if event in _SAFE_TERMINAL_EVENTS:
        return _SAFE_TERMINAL_EVENTS[event]
    if now >= expires_at:
        raise AuthExpired("authorization session expired")
    result = _TRANSITIONS.get((current, event))
    if result is None:
        raise AuthTransitionError("invalid authorization transition")
    return result


def aad(
    provider: AuthProvider,
    record_id: UUID,
    field: str,
    *,
    mode: AuthMode = "own_app",
    protocol_version: int = 1,
) -> str:
    if provider not in AUTH_PROVIDERS or not isinstance(record_id, UUID):
        raise ValueError("invalid authorization encryption identity")
    if (
        not field
        or len(field) > 64
        or not all(c.isascii() and (c.isalnum() or c == "_") for c in field)
    ):
        raise ValueError("invalid authorization encryption field")
    if protocol_version == 1 and mode == "own_app":
        return f"cloud_auth:{provider}:{record_id}:{field}"
    if protocol_version == 2 and mode in ("own_app", "openlist_builtin_import"):
        return f"cloud_auth:v2:{provider}:{mode}:{record_id}:{field}"
    raise ValueError("invalid authorization encryption protocol")
