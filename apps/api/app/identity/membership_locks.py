from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

PersonMembershipScope = Literal["identity", "phone", "email"]


def person_membership_advisory_lock_key(
    scope: PersonMembershipScope,
    digest: str,
) -> int:
    return int.from_bytes(
        hashlib.sha256(
            f"person-membership:{scope}\x00{digest}".encode()
        ).digest()[:8],
        byteorder="big",
        signed=True,
    )


def person_membership_lock_keys(
    *,
    identity_digests: Iterable[str] = (),
    phone_digests: Iterable[str] = (),
    email_digests: Iterable[str] = (),
) -> list[int]:
    return sorted(
        {
            *(
                person_membership_advisory_lock_key("identity", digest)
                for digest in identity_digests
                if digest
            ),
            *(
                person_membership_advisory_lock_key("phone", digest)
                for digest in phone_digests
                if digest
            ),
            *(
                person_membership_advisory_lock_key("email", digest)
                for digest in email_digests
                if digest
            ),
        }
    )


def lock_person_memberships(db: Session, keys: Iterable[int]) -> None:
    for key in sorted(set(keys)):
        db.execute(select(func.pg_advisory_xact_lock(key)))
