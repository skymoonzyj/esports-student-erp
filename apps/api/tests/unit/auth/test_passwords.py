import re

import pytest

from app.security.passwords import PasswordPolicyError, PasswordService


@pytest.fixture
def passwords() -> PasswordService:
    return PasswordService()


def test_password_hash_uses_argon2id_and_verifies(passwords: PasswordService) -> None:
    encoded = passwords.hash("Aa1-strong-26")

    assert encoded.startswith("$argon2id$")
    assert passwords.verify("Aa1-strong-26", encoded) is True
    assert passwords.verify("wrong-password-2026", encoded) is False


@pytest.mark.parametrize("password", ["Aa1xxxxx", "Aa1" + "x" * 13])
def test_new_password_policy_accepts_eight_to_sixteen_characters(
    passwords: PasswordService,
    password: str,
) -> None:
    assert passwords.verify(password, passwords.hash(password)) is True


@pytest.mark.parametrize(
    "password",
    [
        "Aa1xxx",                 # fewer than 8 characters
        "aa1xxxxx",               # no uppercase letter
        "AA1XXXXX",               # no lowercase letter
        "Aaxxxxxx",               # no digit
        "Aa1" + "x" * 14,        # 17 characters
    ],
)
def test_new_password_policy_rejects_invalid_length_or_character_class(
    passwords: PasswordService,
    password: str,
) -> None:
    with pytest.raises(PasswordPolicyError):
        passwords.hash(password)


def test_temporary_password_is_eight_to_sixteen_and_has_all_required_classes(
    passwords: PasswordService,
) -> None:
    password = passwords.temporary_password()

    assert 8 <= len(password) <= 16
    assert re.search(r"[A-Z]", password)
    assert re.search(r"[a-z]", password)
    assert re.search(r"[0-9]", password)
    assert passwords.verify(password, passwords.hash(password)) is True


def test_malformed_hash_is_rejected_without_error(passwords: PasswordService) -> None:
    assert passwords.verify("Aa1-strong-26", "not-an-argon-hash") is False
