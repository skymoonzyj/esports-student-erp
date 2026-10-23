import pytest

from app.security.passwords import PasswordPolicyError, PasswordService


@pytest.fixture
def passwords() -> PasswordService:
    return PasswordService()


def test_password_hash_uses_argon2id_and_verifies(passwords: PasswordService) -> None:
    encoded = passwords.hash("Aa1-strong")

    assert encoded.startswith("$argon2id$")
    assert passwords.verify("Aa1-strong", encoded) is True
    assert passwords.verify("wrong-password-2026", encoded) is False


@pytest.mark.parametrize(
    "password",
    [
        "short1",
        "aaaaaaaa",
        "AAAAAAAA",
        "Abbbbbbb",
        "12345678",
        "Aa1" + "x" * 14,
    ],
)
def test_new_password_policy_requires_length_case_and_digits(
    passwords: PasswordService,
    password: str,
) -> None:
    with pytest.raises(PasswordPolicyError):
        passwords.hash(password)


@pytest.mark.parametrize("password", ["Aa1xxxxx", "Aa1" + "x" * 13])
def test_new_password_policy_accepts_eight_and_sixteen_character_boundaries(
    passwords: PasswordService,
    password: str,
) -> None:
    assert passwords.verify(password, passwords.hash(password)) is True


def test_temporary_password_meets_current_policy(passwords: PasswordService) -> None:
    temporary = passwords.temporary_password()

    assert 8 <= len(temporary) <= 16
    assert any("A" <= character <= "Z" for character in temporary)
    assert any("a" <= character <= "z" for character in temporary)
    assert any("0" <= character <= "9" for character in temporary)


def test_malformed_hash_is_rejected_without_error(passwords: PasswordService) -> None:
    assert passwords.verify("Aa1-strong", "not-an-argon-hash") is False


def test_encoded_hash_validation_rejects_broken_argon2_prefix(
    passwords: PasswordService,
) -> None:
    encoded = passwords.hash("Aa1-strong")

    assert passwords.is_valid_encoded_hash(encoded) is True
    assert passwords.is_valid_encoded_hash("$argon2id$broken") is False
    assert (
        passwords.is_valid_encoded_hash(
            "$argon2id$v=19$m=65536,t=3,p=4$%%%$%%%"
        )
        is False
    )
