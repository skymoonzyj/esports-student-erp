from __future__ import annotations

from dataclasses import dataclass, field

from app.identity.normalization import normalize_email, normalize_phone
from app.registration.models import IdentifierKind
from app.security.encryption import LookupDigest


@dataclass(frozen=True, slots=True)
class NormalizedRegistrationIdentifier:
    kind: IdentifierKind
    value: str = field(repr=False)


def normalize_registration_identifier(
    kind: IdentifierKind,
    raw: str,
) -> NormalizedRegistrationIdentifier:
    if kind == "phone":
        return NormalizedRegistrationIdentifier(kind, normalize_phone(raw))
    if kind == "email":
        return NormalizedRegistrationIdentifier(kind, normalize_email(raw))
    raise ValueError("unsupported identifier kind")


def registration_identifier_digest(
    lookup: LookupDigest,
    identifier: NormalizedRegistrationIdentifier,
) -> str:
    return lookup.digest_domain(
        f"registration-verification-{identifier.kind}",
        identifier.value,
    )
