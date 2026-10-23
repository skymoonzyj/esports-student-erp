"""Shared credential serialization. Call before request/person/membership locks.

Email digests are never compared across namespaces: each lookup is derived from
the same normalized plaintext using the namespace owned by the queried table.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.identity.models import Person
from app.identity.normalization import normalize_cn_id, normalize_email
from app.personnel_intake.errors import CredentialReservationConflict
from app.personnel_intake.models import PersonnelCredentialReservation
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.verification_service import (
    lock_registration_credential_keys,
    registration_credential_lock_keys,
)
from app.security.encryption import LookupDigest


def lock_personnel_credentials(
    db: Session,
    *,
    emails: set[str],
    identity_digests: set[str],
    lookup: LookupDigest,
) -> None:
    lock_registration_credential_keys(
        db,
        registration_credential_lock_keys(
            {
                registration_identifier_digest(
                    lookup, normalize_registration_identifier("email", email)
                )
                for email in emails
            },
            identity_digests=identity_digests,
        ),
    )


class CredentialReservationPolicy:
    def __init__(self, db: Session, *, lookup: LookupDigest) -> None:
        self.db = db
        self.lookup = lookup

    def require_reservation_available(
        self,
        *,
        kind: str,
        digest: str,
        person_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> None:
        query = select(PersonnelCredentialReservation.id).where(
            PersonnelCredentialReservation.kind == kind,
            PersonnelCredentialReservation.digest == digest,
            PersonnelCredentialReservation.ended_at.is_(None),
        )
        if request_id is not None:
            query = query.where(PersonnelCredentialReservation.request_id != request_id)
        if person_id is not None:
            query = query.where(
                (PersonnelCredentialReservation.person_id.is_(None))
                | (PersonnelCredentialReservation.person_id != person_id)
            )
        if self.db.scalar(query) is not None:
            raise CredentialReservationConflict()

    def require_identity_available(
        self,
        *,
        identity: str,
        person_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> None:
        digest = self.lookup.digest(normalize_cn_id(identity).number)
        self.require_reservation_available(
            kind="identity", digest=digest, person_id=person_id, request_id=request_id
        )
        query = select(Person.id).where(Person.identity_digest == digest)
        if person_id is not None:
            query = query.where(Person.id != person_id)
        if self.db.scalar(query) is not None:
            raise CredentialReservationConflict()

    def require_email_available(
        self,
        *,
        email: str,
        person_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> None:
        email = normalize_email(email)
        self.require_reservation_available(
            kind="email",
            digest=self.lookup.digest_domain("registration-verification-email", email),
            person_id=person_id,
            request_id=request_id,
        )
        people = select(Person.id).where(
            (Person.email_digest == self.lookup.digest_domain("person-email", email))
            | (Person.email == email)
        )
        accounts = (
            select(UserAccount.person_id)
            .join(
                AccountLoginIdentifier,
                AccountLoginIdentifier.account_id == UserAccount.id,
            )
            .where(
                AccountLoginIdentifier.kind == "email",
                AccountLoginIdentifier.identifier_digest
                == login_identifier_digest(self.lookup, NormalizedLoginIdentifier("email", email)),
            )
        )
        if person_id is not None:
            people = people.where(Person.id != person_id)
            accounts = accounts.where(UserAccount.person_id != person_id)
        if self.db.scalar(people) is not None or self.db.scalar(accounts) is not None:
            raise CredentialReservationConflict()

    def require_available(
        self,
        *,
        identity: str,
        email: str,
        person_id: UUID | None,
        request_id: UUID | None,
    ) -> None:
        self.require_identity_available(
            identity=identity, person_id=person_id, request_id=request_id
        )
        self.require_email_available(email=email, person_id=person_id, request_id=request_id)


def person_credential_fingerprint(person: Person) -> tuple[object, ...]:
    """Copy all facts used to derive old/new keys before acquiring any row lock."""
    return (
        person.identity_digest,
        person.identity_ciphertext,
        person.phone_digest,
        person.phone_ciphertext,
        person.email_digest,
        person.email_ciphertext,
        person.email,
        person.date_of_birth,
        person.sex,
        person.deleted_at,
    )


def require_registration_reservations(
    db: Session,
    *,
    lookup: LookupDigest,
    identifier_kind: str | None,
    identifier_digest: str | None,
    identity_digest: str | None,
) -> None:
    """Preserve registration's existing claim/review and anonymous privacy rules.

    People/login conflicts remain classified by the registration matching service;
    this guard only adds the new reservation boundary under the shared locks.
    """
    person_id = (
        db.scalar(select(Person.id).where(Person.identity_digest == identity_digest))
        if identity_digest
        else None
    )
    policy = CredentialReservationPolicy(db, lookup=lookup)
    if identity_digest:
        policy.require_reservation_available(
            kind="identity", digest=identity_digest, person_id=person_id
        )
    if identifier_kind == "email" and identifier_digest:
        policy.require_reservation_available(
            kind="email", digest=identifier_digest, person_id=person_id
        )
