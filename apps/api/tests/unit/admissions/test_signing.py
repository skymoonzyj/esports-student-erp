import hashlib
from datetime import UTC, datetime, timedelta

from app.admissions.signing import (
    canonical_request,
    request_timestamp_is_current,
    sign_request,
    verify_signature,
)


def test_canonical_request_is_byte_stable() -> None:
    assert canonical_request(
        method="POST",
        path="/api/v1/payment-imports",
        timestamp="1785196800",
        nonce="n-123",
        body=b'{"amount":"100.00"}',
    ) == (
        b"POST\n/api/v1/payment-imports\n1785196800\nn-123\n"
        + hashlib.sha256(b'{"amount":"100.00"}').hexdigest().encode()
    )


def test_canonical_request_hashes_exact_raw_body_bytes() -> None:
    compact = canonical_request(
        method="post",
        path="/api/v1/payment-imports",
        timestamp="1785196800",
        nonce="n-123",
        body=b'{"amount":"100.00"}',
    )
    spaced = canonical_request(
        method="POST",
        path="/api/v1/payment-imports",
        timestamp="1785196800",
        nonce="n-123",
        body=b'{ "amount": "100.00" }',
    )

    assert compact.startswith(b"POST\n")
    assert compact != spaced


def test_signature_verification_requires_lowercase_sha256_hex() -> None:
    canonical = b"POST\n/path\n1\nnonce\nbody-digest"
    signature = sign_request(secret=b"secret", canonical=canonical)

    assert verify_signature(secret=b"secret", provided=signature, canonical=canonical)
    assert not verify_signature(secret=b"wrong", provided=signature, canonical=canonical)
    assert not verify_signature(
        secret=b"secret",
        provided=signature.upper(),
        canonical=canonical,
    )
    assert not verify_signature(secret=b"secret", provided="abc", canonical=canonical)


def test_request_timestamp_accepts_exact_tolerance_boundaries() -> None:
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    assert request_timestamp_is_current(
        str(int((now - timedelta(seconds=300)).timestamp())),
        now=now,
        tolerance_seconds=300,
    )
    assert request_timestamp_is_current(
        str(int((now + timedelta(seconds=300)).timestamp())),
        now=now,
        tolerance_seconds=300,
    )


def test_request_timestamp_rejects_expired_future_and_malformed_values() -> None:
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    assert not request_timestamp_is_current(
        str(int((now - timedelta(seconds=301)).timestamp())),
        now=now,
        tolerance_seconds=300,
    )
    assert not request_timestamp_is_current(
        str(int((now + timedelta(seconds=301)).timestamp())),
        now=now,
        tolerance_seconds=300,
    )
    assert not request_timestamp_is_current(
        "1785196800.0",
        now=now,
        tolerance_seconds=300,
    )
    assert not request_timestamp_is_current(
        "+1785196800",
        now=now,
        tolerance_seconds=300,
    )
    assert not request_timestamp_is_current(
        "9" * 10_000,
        now=now,
        tolerance_seconds=300,
    )
