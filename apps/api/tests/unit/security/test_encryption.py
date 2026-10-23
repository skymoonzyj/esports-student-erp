from __future__ import annotations

import json

import pytest

from app.security.encryption import (
    EncryptedFieldError,
    EnvelopeCipher,
    LookupDigest,
)


@pytest.fixture
def cipher() -> EnvelopeCipher:
    return EnvelopeCipher(bytes(range(32)))


@pytest.fixture
def lookup() -> LookupDigest:
    return LookupDigest(bytes(reversed(range(32))))


def test_envelope_cipher_round_trip_and_randomizes_ciphertext(cipher: EnvelopeCipher) -> None:
    one = cipher.encrypt("11010519491231002X", field_name="identity_number")
    two = cipher.encrypt("11010519491231002X", field_name="identity_number")

    assert one != two
    assert cipher.decrypt(one, field_name="identity_number") == "11010519491231002X"
    assert cipher.decrypt(two, field_name="identity_number") == "11010519491231002X"


def test_envelope_cipher_binds_ciphertext_to_field_name(cipher: EnvelopeCipher) -> None:
    encrypted = cipher.encrypt("13800138000", field_name="phone")

    with pytest.raises(EncryptedFieldError, match="unable to decrypt encrypted field"):
        cipher.decrypt(encrypted, field_name="identity_number")


def test_envelope_cipher_rejects_tampering_without_echoing_payload(
    cipher: EnvelopeCipher,
) -> None:
    encrypted = cipher.encrypt("13800138000", field_name="phone")
    payload = json.loads(encrypted)
    payload["ciphertext"] = payload["ciphertext"][:-2] + "AA"
    tampered = json.dumps(payload)

    with pytest.raises(EncryptedFieldError) as error:
        cipher.decrypt(tampered, field_name="phone")
    assert "13800138000" not in str(error.value)
    assert tampered not in str(error.value)


def test_lookup_digest_is_stable_and_keyed(lookup: LookupDigest) -> None:
    assert lookup.digest("13800138000") == lookup.digest("13800138000")
    assert lookup.digest("13800138000") != lookup.digest("13900139000")
    assert lookup.digest("13800138000") != LookupDigest(bytes(range(32))).digest("13800138000")


def test_lookup_digest_supports_stable_domain_separation(lookup: LookupDigest) -> None:
    digest_domain = getattr(lookup, "digest_domain", None)

    assert callable(digest_domain)
    assert digest_domain("student_game_id", "13800138000") == digest_domain(
        "student_game_id",
        "13800138000",
    )
    assert digest_domain("student_game_id", "13800138000") != lookup.digest(
        "13800138000"
    )
    assert digest_domain("student_game_id", "13800138000") != digest_domain(
        "student_auxiliary_phone",
        "13800138000",
    )


@pytest.mark.parametrize("key", [b"", b"short", bytes(range(31)), bytes(range(33))])
def test_encryption_keys_must_be_32_bytes(key: bytes) -> None:
    with pytest.raises(ValueError, match="must be 32 bytes"):
        EnvelopeCipher(key)
    with pytest.raises(ValueError, match="must be 32 bytes"):
        LookupDigest(key)
