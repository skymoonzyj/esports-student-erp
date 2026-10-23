from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.identity.normalization import normalize_email, normalize_phone
from app.security.encryption import LookupDigest

IdentifierKind = Literal["phone", "email"]


@dataclass(frozen=True, slots=True)
class NormalizedLoginIdentifier:
    kind: IdentifierKind
    value: str


def normalize_login_identifier(raw: str) -> NormalizedLoginIdentifier:
    value = raw.strip()
    if "@" in value:
        return NormalizedLoginIdentifier("email", normalize_email(value))
    return NormalizedLoginIdentifier("phone", normalize_phone(value))


def login_identifier_digest(
    lookup: LookupDigest,
    identifier: NormalizedLoginIdentifier,
) -> str:
    return lookup.digest_domain(
        f"account-login-{identifier.kind}",
        identifier.value,
    )
