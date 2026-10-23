from __future__ import annotations

import hmac
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.transactions import write_transaction
from app.identity.models import (
    GuardianProfile,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.normalization import (
    IdentityValidationError,
    normalize_cn_id,
    normalize_email,
    normalize_phone,
)
from app.registration.identifiers import (
    NormalizedRegistrationIdentifier,
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import RegistrationApplication, RegistrationPurpose
from app.registration.privacy import contains_sensitive_registration_text
from app.security.encryption import EnvelopeCipher, LookupDigest

MatchKind = Literal[
    "new_person",
    "claim_existing",
    "existing_account",
    "conflict",
]


class RegistrationMatchingError(ValueError):
    code = "registration_matching_error"


class RegistrationApplicationInvalid(RegistrationMatchingError):
    code = "registration_application_invalid"


class _InvalidPersonContact(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RegistrationMatch:
    kind: MatchKind
    person_id: UUID | None
    conflict_code: str | None


@dataclass(frozen=True, slots=True)
class RegistrationCandidateMembership:
    identity_person_ids: frozenset[UUID]
    contact_person_ids: frozenset[UUID]

    @property
    def person_ids(self) -> frozenset[UUID]:
        return self.identity_person_ids | self.contact_person_ids


@dataclass(frozen=True, slots=True)
class RegistrationIdentitySnapshot:
    full_name: str
    identity_number: str = field(repr=False)
    identity_digest: str = field(repr=False)
    sex: Literal["male", "female"]
    date_of_birth: date
    identifier: NormalizedRegistrationIdentifier = field(repr=False)
    primary_purpose: RegistrationPurpose
    requested_staff_type: str | None
    staff_application_reason: str | None
    visit_reason: str | None


class RegistrationMatchingService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup

    @staticmethod
    def _name(value: str) -> str:
        return unicodedata.normalize("NFKC", value).strip()

    def _application_snapshot(
        self,
        application: RegistrationApplication,
    ) -> RegistrationIdentitySnapshot:
        if (
            application.full_name is None
            or application.identity_ciphertext is None
            or application.identity_digest is None
            or application.sex not in {"male", "female"}
            or application.date_of_birth is None
            or application.verified_identifier_kind not in {"phone", "email"}
            or application.verified_identifier_digest is None
            or application.verified_identifier_ciphertext is None
            or application.primary_purpose
            not in {"student", "guardian", "staff_applicant", "visitor"}
        ):
            raise RegistrationApplicationInvalid("registration identity is incomplete")
        try:
            identity_number = self.cipher.decrypt(
                application.identity_ciphertext,
                field_name="registration_identity",
            )
            normalized_identity = normalize_cn_id(
                identity_number,
                expected_birth_date=application.date_of_birth,
            )
            if normalized_identity.sex != application.sex:
                raise RegistrationApplicationInvalid(
                    "registration identity sex does not match"
                )
            if not hmac.compare_digest(
                application.identity_digest,
                self.lookup.digest(normalized_identity.number),
            ):
                raise RegistrationApplicationInvalid(
                    "registration identity digest does not match"
                )
            identifier = normalize_registration_identifier(
                cast(Literal["phone", "email"], application.verified_identifier_kind),
                self.cipher.decrypt(
                    application.verified_identifier_ciphertext,
                    field_name="registration_identifier",
                ),
            )
        except (IdentityValidationError, ValueError, TypeError) as exc:
            if isinstance(exc, RegistrationApplicationInvalid):
                raise
            raise RegistrationApplicationInvalid(
                "registration identity cannot be verified"
            ) from exc
        if not hmac.compare_digest(
            application.verified_identifier_digest,
            registration_identifier_digest(self.lookup, identifier),
        ):
            raise RegistrationApplicationInvalid(
                "registration identifier digest does not match"
            )
        full_name = self._name(application.full_name)
        if not full_name:
            raise RegistrationApplicationInvalid("registration name is invalid")
        if any(
            value is not None and contains_sensitive_registration_text(value)
            for value in (
                application.staff_application_reason,
                application.visit_reason,
            )
        ):
            raise RegistrationApplicationInvalid(
                "registration business text contains sensitive data"
            )
        return RegistrationIdentitySnapshot(
            full_name=full_name,
            identity_number=normalized_identity.number,
            identity_digest=application.identity_digest,
            sex=application.sex,
            date_of_birth=application.date_of_birth,
            identifier=identifier,
            primary_purpose=cast(RegistrationPurpose, application.primary_purpose),
            requested_staff_type=application.requested_staff_type,
            staff_application_reason=application.staff_application_reason,
            visit_reason=application.visit_reason,
        )

    def application_snapshot(
        self,
        application: RegistrationApplication,
    ) -> RegistrationIdentitySnapshot:
        return self._application_snapshot(application)

    def _person_contact_digest(
        self,
        identifier: NormalizedRegistrationIdentifier,
    ) -> str:
        if identifier.kind == "phone":
            return self.lookup.digest(identifier.value)
        return self.lookup.digest_domain("person-email", identifier.value)

    def _candidate_ids(
        self,
        snapshot: RegistrationIdentitySnapshot,
    ) -> tuple[set[UUID], set[UUID]]:
        identity_ids = set(
            self.db.scalars(
                select(Person.id).where(
                    Person.identity_digest == snapshot.identity_digest
                )
            )
        )
        contact_column = (
            Person.phone_digest
            if snapshot.identifier.kind == "phone"
            else Person.email_digest
        )
        contact_ids = set(
            self.db.scalars(
                select(Person.id).where(
                    contact_column == self._person_contact_digest(snapshot.identifier)
                )
            )
        )
        return identity_ids, contact_ids

    def candidate_person_ids_in_transaction(
        self,
        application: RegistrationApplication,
    ) -> set[UUID]:
        snapshot = self._application_snapshot(application)
        return set(
            self.candidate_membership_for_snapshot_in_transaction(snapshot).person_ids
        )

    def candidate_membership_for_snapshot_in_transaction(
        self,
        snapshot: RegistrationIdentitySnapshot,
    ) -> RegistrationCandidateMembership:
        identity_ids, contact_ids = self._candidate_ids(snapshot)
        return RegistrationCandidateMembership(
            identity_person_ids=frozenset(identity_ids),
            contact_person_ids=frozenset(contact_ids),
        )

    def lock_candidate_people_in_transaction(
        self,
        person_ids: Iterable[UUID],
    ) -> list[Person]:
        ordered_ids = sorted(set(person_ids))
        if not ordered_ids:
            return []
        return list(
            self.db.scalars(
                select(Person)
                .where(Person.id.in_(ordered_ids))
                .order_by(Person.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def classify_locked_candidates_in_transaction(
        self,
        snapshot: RegistrationIdentitySnapshot,
        membership: RegistrationCandidateMembership,
        people: Iterable[Person],
    ) -> RegistrationMatch:
        people_by_id = {person.id: person for person in people}
        identity_ids = set(membership.identity_person_ids)
        contact_ids = set(membership.contact_person_ids)

        if not membership.person_ids.issubset(people_by_id):
            return RegistrationMatch("conflict", None, "candidate_person_unavailable")
        candidate_people = [
            people_by_id[person_id] for person_id in sorted(membership.person_ids)
        ]
        if any(person.deleted_at is not None for person in candidate_people):
            return RegistrationMatch("conflict", None, "deleted_person_match")
        if len(identity_ids) > 1 or len(contact_ids) > 1:
            return RegistrationMatch("conflict", None, "multiple_person_match")
        if identity_ids and contact_ids and identity_ids != contact_ids:
            return RegistrationMatch("conflict", None, "split_identifier_match")
        if not identity_ids:
            if contact_ids:
                return RegistrationMatch("conflict", None, "contact_already_used")
            return RegistrationMatch("new_person", None, None)

        person_id = next(iter(identity_ids))
        person = people_by_id[person_id]
        if not self._person_identity_matches(person, snapshot):
            return RegistrationMatch("conflict", None, "identity_profile_mismatch")
        try:
            person_contact = self._person_contact(person, snapshot.identifier.kind)
        except _InvalidPersonContact:
            return RegistrationMatch("conflict", None, "invalid_person_contact")
        if person_contact is not None and not hmac.compare_digest(
            person_contact,
            snapshot.identifier.value,
        ):
            return RegistrationMatch("conflict", None, "contact_mismatch")
        if contact_ids and contact_ids != {person.id}:
            return RegistrationMatch("conflict", None, "split_identifier_match")
        account_id = self.db.scalar(
            select(UserAccount.id)
            .where(UserAccount.person_id == person.id)
            .with_for_update()
        )
        if account_id is not None:
            return RegistrationMatch("existing_account", person.id, None)
        if not self._purpose_is_compatible(person.id, snapshot):
            return RegistrationMatch("conflict", None, "purpose_conflict")
        return RegistrationMatch("claim_existing", person.id, None)

    def classify_in_transaction(
        self,
        application: RegistrationApplication,
    ) -> RegistrationMatch:
        snapshot = self._application_snapshot(application)
        membership = self.candidate_membership_for_snapshot_in_transaction(snapshot)
        people = self.lock_candidate_people_in_transaction(membership.person_ids)
        return self.classify_locked_candidates_in_transaction(
            snapshot,
            membership,
            people,
        )

    def _person_identity_matches(
        self,
        person: Person,
        snapshot: RegistrationIdentitySnapshot,
    ) -> bool:
        try:
            identity_number = normalize_cn_id(
                self.cipher.decrypt(
                    person.identity_ciphertext,
                    field_name="identity_number",
                ),
                expected_birth_date=person.date_of_birth,
            )
        except (IdentityValidationError, ValueError, TypeError):
            return False
        return (
            hmac.compare_digest(person.identity_digest, snapshot.identity_digest)
            and hmac.compare_digest(identity_number.number, snapshot.identity_number)
            and self._name(person.full_name) == snapshot.full_name
            and person.date_of_birth == snapshot.date_of_birth
            and person.sex == snapshot.sex
            and identity_number.sex == snapshot.sex
        )

    def _person_contact(
        self,
        person: Person,
        kind: Literal["phone", "email"],
    ) -> str | None:
        try:
            if kind == "phone":
                if (person.phone_ciphertext is None) != (person.phone_digest is None):
                    raise _InvalidPersonContact
                if person.phone_ciphertext is None:
                    return None
                value = normalize_phone(
                    self.cipher.decrypt(person.phone_ciphertext, field_name="phone")
                )
                if person.phone_digest != self.lookup.digest(value):
                    raise _InvalidPersonContact
                return value
            if (person.email_ciphertext is None) != (person.email_digest is None):
                raise _InvalidPersonContact
            if person.email_ciphertext is None:
                return None
            value = normalize_email(
                self.cipher.decrypt(person.email_ciphertext, field_name="email")
            )
            if person.email_digest != self.lookup.digest_domain("person-email", value):
                raise _InvalidPersonContact
            return value
        except (IdentityValidationError, ValueError, TypeError) as exc:
            if isinstance(exc, _InvalidPersonContact):
                raise
            raise _InvalidPersonContact from exc

    def _purpose_is_compatible(
        self,
        person_id: UUID,
        snapshot: RegistrationIdentitySnapshot,
    ) -> bool:
        student_profile = self.db.get(StudentProfile, person_id)
        guardian_profile = self.db.get(GuardianProfile, person_id)
        staff_profile = self.db.get(StaffProfile, person_id)
        visitor_profile = self.db.get(VisitorProfile, person_id)
        existing_profile_kinds = {
            kind
            for kind, profile in (
                ("student", student_profile),
                ("guardian", guardian_profile),
                ("staff", staff_profile),
                ("visitor", visitor_profile),
            )
            if profile is not None
        }
        if not existing_profile_kinds:
            return True
        matching_profile_kind = {
            "student": "student",
            "guardian": "guardian",
            "staff_applicant": None,
            "visitor": "visitor",
        }[snapshot.primary_purpose]
        if matching_profile_kind is None or existing_profile_kinds != {matching_profile_kind}:
            return False
        if snapshot.primary_purpose != "visitor":
            return True
        return (
            visitor_profile is not None
            and visitor_profile.visit_reason == snapshot.visit_reason
        )


    def classify(self, application_id: UUID) -> RegistrationMatch:
        result: RegistrationMatch | None = None
        with write_transaction(self.db):
            application = self.db.scalar(
                select(RegistrationApplication)
                .where(RegistrationApplication.id == application_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if application is None:
                raise RegistrationApplicationInvalid(
                    "registration application is unavailable"
                )
            result = self.classify_in_transaction(application)
        assert result is not None
        return result
