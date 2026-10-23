from __future__ import annotations

import pytest

from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.security.encryption import LookupDigest


def test_registration_identifiers_normalize_phone_and_email() -> None:
    phone = normalize_registration_identifier("phone", " 13800138000 ")
    email = normalize_registration_identifier("email", " User@Example.COM ")

    assert (phone.kind, phone.value) == ("phone", "13800138000")
    assert (email.kind, email.value) == ("email", "user@example.com")
    assert phone.value not in repr(phone)
    assert email.value not in repr(email)


def test_registration_identifier_digest_is_domain_separated() -> None:
    lookup = LookupDigest(bytes(range(32)))
    phone = normalize_registration_identifier("phone", "13800138000")
    login_phone = normalize_login_identifier(phone.value)

    digest = registration_identifier_digest(lookup, phone)

    assert digest != lookup.digest(phone.value)
    assert digest != login_identifier_digest(lookup, login_phone)


def test_registration_identifier_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="identifier kind"):
        normalize_registration_identifier("username", "player-one")  # type: ignore[arg-type]
