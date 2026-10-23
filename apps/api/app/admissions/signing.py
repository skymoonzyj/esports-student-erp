from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime

_LOWER_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_UNIX_SECONDS = re.compile(r"[0-9]{1,20}")


def canonical_request(
    *,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> bytes:
    body_digest = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_digest}".encode()


def sign_request(*, secret: bytes, canonical: bytes) -> str:
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def verify_signature(*, secret: bytes, provided: str, canonical: bytes) -> bool:
    if _LOWER_SHA256_HEX.fullmatch(provided) is None:
        return False
    expected = sign_request(secret=secret, canonical=canonical)
    return hmac.compare_digest(expected, provided)


def request_timestamp_is_current(
    timestamp: str,
    *,
    now: datetime,
    tolerance_seconds: int,
) -> bool:
    if now.utcoffset() is None or tolerance_seconds < 0:
        raise ValueError("timestamp validation requires an aware clock and non-negative tolerance")
    if _UNIX_SECONDS.fullmatch(timestamp) is None:
        return False
    return abs(now.timestamp() - int(timestamp)) <= tolerance_seconds
