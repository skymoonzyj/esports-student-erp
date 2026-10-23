from __future__ import annotations

import base64
import binascii
import secrets
import string

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class PasswordPolicyError(ValueError):
    """Raised when a new password does not meet the account policy."""


MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 16


class PasswordService:
    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    @staticmethod
    def validate(password: str) -> None:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise PasswordPolicyError("password must be between 8 and 16 characters")
        if not any("A" <= character <= "Z" for character in password):
            raise PasswordPolicyError("password must contain an uppercase letter")
        if not any("a" <= character <= "z" for character in password):
            raise PasswordPolicyError("password must contain a lowercase letter")
        if not any("0" <= character <= "9" for character in password):
            raise PasswordPolicyError("password must contain a digit")

    def hash(self, password: str) -> str:
        self.validate(password)
        return self._hasher.hash(password)

    def verify(self, password: str, encoded: str) -> bool:
        try:
            return self._hasher.verify(encoded, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False

    def needs_rehash(self, encoded: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(encoded)
        except InvalidHashError:
            return True

    @staticmethod
    def is_valid_encoded_hash(encoded: str) -> bool:
        try:
            parameters = extract_parameters(encoded)
        except InvalidHashError:
            return False
        parts = encoded.split("$")
        if (
            len(parts) != 6
            or parts[0] != ""
            or parts[1] != "argon2id"
            or parameters.type is not Type.ID
            or parameters.version != 19
        ):
            return False

        def decoded(component: str) -> bytes | None:
            if not component or "=" in component:
                return None
            padding = "=" * ((4 - len(component) % 4) % 4)
            try:
                return base64.b64decode(component + padding, validate=True)
            except (binascii.Error, ValueError):
                return None

        salt = decoded(parts[4])
        digest = decoded(parts[5])
        return (
            salt is not None
            and digest is not None
            and len(salt) == parameters.salt_len
            and len(digest) == parameters.hash_len
        )

    def temporary_password(self) -> str:
        alphabet = string.ascii_letters + string.digits
        characters = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            *(secrets.choice(alphabet) for _ in range(MAX_PASSWORD_LENGTH - 3)),
        ]
        secrets.SystemRandom().shuffle(characters)
        password = "".join(characters)
        self.validate(password)
        return password
