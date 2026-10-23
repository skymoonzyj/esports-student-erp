from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from app.security.encryption import LookupDigest

_TOKEN_VERSION = "v1"
_SIGNATURE_DOMAIN = b"guardian-invitation-flow\x00"
_MAX_TOKEN_LENGTH = 256
_HANDOFF_TOKEN_BYTES = 32
_HANDOFF_TOKEN_LENGTH = 43
_HANDOFF_DIGEST_DOMAIN = "guardian-invitation-handoff"


def issue_guardian_invitation_handoff_token() -> str:
    token = secrets.token_urlsafe(_HANDOFF_TOKEN_BYTES)
    if len(token) != _HANDOFF_TOKEN_LENGTH:
        raise RuntimeError("guardian invitation handoff token length is invalid")
    return token


def is_guardian_invitation_handoff_token(token: str) -> bool:
    if not token or len(token) != _HANDOFF_TOKEN_LENGTH:
        return False
    try:
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError):
        return False
    if len(raw) != _HANDOFF_TOKEN_BYTES:
        return False
    canonical = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return hmac.compare_digest(canonical, token)


def guardian_invitation_handoff_token_digest(
    lookup: LookupDigest,
    token: str,
) -> str | None:
    if not is_guardian_invitation_handoff_token(token):
        return None
    return lookup.digest_domain(_HANDOFF_DIGEST_DOMAIN, token)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("guardian invitation flow clock must be timezone-aware")


def _signature(payload: str, *, pepper: bytes) -> bytes:
    return hmac.new(
        pepper,
        _SIGNATURE_DOMAIN + payload.encode("ascii"),
        hashlib.sha256,
    ).digest()


def issue_invitation_flow_token(
    invitation_id: UUID,
    *,
    pepper: bytes,
    now: datetime,
    ttl: timedelta,
) -> str:
    _require_aware(now)
    if not pepper:
        raise ValueError("guardian invitation flow pepper must not be empty")
    if ttl <= timedelta(0):
        raise ValueError("guardian invitation flow TTL must be positive")
    expires_at = int((now + ttl).timestamp())
    payload = f"{_TOKEN_VERSION}.{invitation_id.hex}.{expires_at}"
    signature = base64.urlsafe_b64encode(
        _signature(payload, pepper=pepper)
    ).decode("ascii").rstrip("=")
    return f"{payload}.{signature}"


def invitation_id_from_flow_token(
    token: str,
    *,
    pepper: bytes,
    now: datetime,
) -> UUID | None:
    _require_aware(now)
    if not pepper or not token or len(token) > _MAX_TOKEN_LENGTH:
        return None
    parts = token.split(".")
    if len(parts) != 4:
        return None
    version, raw_invitation_id, raw_expires_at, raw_signature = parts
    if version != _TOKEN_VERSION or not raw_expires_at.isascii():
        return None
    try:
        invitation_id = UUID(hex=raw_invitation_id)
        expires_at = int(raw_expires_at)
        signature = base64.b64decode(
            raw_signature + "=" * (-len(raw_signature) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError):
        return None
    canonical_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    if not hmac.compare_digest(canonical_signature, raw_signature):
        return None
    payload = f"{version}.{raw_invitation_id}.{raw_expires_at}"
    if not hmac.compare_digest(signature, _signature(payload, pepper=pepper)):
        return None
    if expires_at <= int(now.timestamp()):
        return None
    return invitation_id
