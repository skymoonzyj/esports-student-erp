from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import UserAccount
from app.core.privacy import (
    contains_email,
    contains_labeled_sensitive_value,
    contains_phone_or_identity,
    looks_like_raw_secret,
)
from app.core.time import business_date_at
from app.core.transactions import write_transaction
from app.identity.membership_locks import (
    lock_person_memberships,
    person_membership_lock_keys,
)
from app.identity.models import Person
from app.identity.normalization import (
    IdentityValidationError,
    normalize_cn_id,
    normalize_email,
    normalize_phone,
)
from app.identity.readiness import is_minor
from app.registration.finalization_service import (
    RegistrationFinalizationResult,
    RegistrationFinalizationService,
    RegistrationFinalizationUnavailable,
    clear_registration_application_secrets,
)
from app.registration.identifiers import (
    NormalizedRegistrationIdentifier,
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.matching_service import (
    RegistrationApplicationInvalid,
    RegistrationIdentitySnapshot,
    RegistrationMatchingService,
)
from app.registration.models import (
    IdentifierKind,
    RegistrationApplication,
    RegistrationPurpose,
    RegistrationSession,
)
from app.registration.schemas import (
    RegistrationAdminCandidate,
    RegistrationAdminDetail,
    RegistrationAdminListItem,
)
from app.security.encryption import EnvelopeCipher, LookupDigest


class RegistrationReviewError(ValueError):
    code = "registration_review_error"


class RegistrationReviewNotFound(RegistrationReviewError):
    code = "registration_application_not_found"


class RegistrationReviewDataUnavailable(RegistrationReviewError):
    code = "registration_review_data_unavailable"


class RegistrationReviewReasonInvalid(RegistrationReviewError):
    code = "registration_review_reason_invalid"


class RegistrationReviewService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        finalization: RegistrationFinalizationService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup
        self.finalization = finalization
        self.matching = RegistrationMatchingService(
            db,
            cipher=cipher,
            lookup=lookup,
        )
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registration review clock must be timezone-aware")
        return value

    @staticmethod
    def _validate_reason(reason: str) -> None:
        if (
            contains_email(reason)
            or contains_labeled_sensitive_value(reason)
            or contains_phone_or_identity(reason)
            or looks_like_raw_secret(reason)
        ):
            raise RegistrationReviewReasonInvalid(
                "registration review reason is invalid"
            )

    @staticmethod
    def _mask_identifier(identifier: NormalizedRegistrationIdentifier) -> str:
        if identifier.kind == "phone":
            return f"{identifier.value[:3]}****{identifier.value[-4:]}"
        local, domain = identifier.value.rsplit("@", 1)
        return f"{local[:1]}***@{domain}"

    def _identifier(
        self,
        application: RegistrationApplication,
    ) -> NormalizedRegistrationIdentifier:
        if (
            application.verified_identifier_kind not in {"phone", "email"}
            or application.verified_identifier_digest is None
            or application.verified_identifier_ciphertext is None
        ):
            raise RegistrationReviewDataUnavailable(
                "registration identifier is unavailable"
            )
        try:
            identifier = normalize_registration_identifier(
                cast(IdentifierKind, application.verified_identifier_kind),
                self.cipher.decrypt(
                    application.verified_identifier_ciphertext,
                    field_name="registration_identifier",
                ),
            )
        except (IdentityValidationError, ValueError, TypeError) as exc:
            raise RegistrationReviewDataUnavailable(
                "registration identifier is unavailable"
            ) from exc
        if not hmac.compare_digest(
            application.verified_identifier_digest,
            registration_identifier_digest(self.lookup, identifier),
        ):
            raise RegistrationReviewDataUnavailable(
                "registration identifier is unavailable"
            )
        return identifier

    def _identity_candidate_ids(
        self,
        application: RegistrationApplication,
    ) -> list[UUID]:
        if application.identity_digest is None:
            raise RegistrationReviewDataUnavailable(
                "registration identity digest is unavailable"
            )
        return sorted(
            self.db.scalars(
                select(Person.id).where(
                    Person.identity_digest == application.identity_digest
                )
            )
        )

    def _candidate_ids(
        self,
        application: RegistrationApplication,
        identifier: NormalizedRegistrationIdentifier,
        *,
        identity_candidate_ids: list[UUID] | None = None,
    ) -> list[UUID]:
        identity_ids = set(
            self._identity_candidate_ids(application)
            if identity_candidate_ids is None
            else identity_candidate_ids
        )
        if identifier.kind == "phone":
            contact_column = Person.phone_digest
            contact_digest = self.lookup.digest(identifier.value)
        else:
            contact_column = Person.email_digest
            contact_digest = self.lookup.digest_domain(
                "person-email",
                identifier.value,
            )
        contact_ids = set(
            self.db.scalars(
                select(Person.id).where(contact_column == contact_digest)
            )
        )
        return sorted(identity_ids | contact_ids)

    def _lock_candidate_memberships(
        self,
        application: RegistrationApplication,
        identifier: NormalizedRegistrationIdentifier,
    ) -> None:
        if application.identity_digest is None:
            raise RegistrationReviewDataUnavailable(
                "registration identity digest is unavailable"
            )
        if identifier.kind == "phone":
            keys = person_membership_lock_keys(
                identity_digests=[application.identity_digest],
                phone_digests=[self.lookup.digest(identifier.value)],
            )
        else:
            keys = person_membership_lock_keys(
                identity_digests=[application.identity_digest],
                email_digests=[
                    self.lookup.digest_domain("person-email", identifier.value)
                ],
            )
        lock_person_memberships(self.db, keys)

    def _person_identity(self, person: Person) -> str:
        try:
            normalized = normalize_cn_id(
                self.cipher.decrypt(
                    person.identity_ciphertext,
                    field_name="identity_number",
                ),
                expected_birth_date=person.date_of_birth,
            )
        except (IdentityValidationError, ValueError, TypeError) as exc:
            raise RegistrationReviewDataUnavailable(
                "candidate identity is unavailable"
            ) from exc
        if (
            not hmac.compare_digest(
                person.identity_digest,
                self.lookup.digest(normalized.number),
            )
            or normalized.sex != person.sex
        ):
            raise RegistrationReviewDataUnavailable(
                "candidate identity is unavailable"
            )
        return normalized.number

    def _person_contact(
        self,
        person: Person,
        snapshot: RegistrationIdentitySnapshot,
    ) -> str | None:
        try:
            if snapshot.identifier.kind == "phone":
                if (person.phone_ciphertext is None) != (person.phone_digest is None):
                    raise ValueError("candidate phone pair is incomplete")
                if person.phone_ciphertext is None:
                    return None
                value = normalize_phone(
                    self.cipher.decrypt(person.phone_ciphertext, field_name="phone")
                )
                if not hmac.compare_digest(
                    cast(str, person.phone_digest),
                    self.lookup.digest(value),
                ):
                    raise ValueError("candidate phone digest does not match")
                return value
            if (person.email_ciphertext is None) != (person.email_digest is None):
                raise ValueError("candidate email pair is incomplete")
            if person.email_ciphertext is None:
                return None
            value = normalize_email(
                self.cipher.decrypt(person.email_ciphertext, field_name="email")
            )
            if not hmac.compare_digest(
                cast(str, person.email_digest),
                self.lookup.digest_domain("person-email", value),
            ):
                raise ValueError("candidate email digest does not match")
            return value
        except (IdentityValidationError, ValueError, TypeError) as exc:
            raise RegistrationReviewDataUnavailable(
                "candidate contact is unavailable"
            ) from exc

    def _snapshot(
        self,
        application: RegistrationApplication,
    ) -> RegistrationIdentitySnapshot:
        try:
            return self.matching.application_snapshot(application)
        except RegistrationApplicationInvalid as exc:
            raise RegistrationReviewDataUnavailable(
                "registration review data is unavailable"
            ) from exc

    def _base_item(
        self,
        application: RegistrationApplication,
        identifier: NormalizedRegistrationIdentifier,
        *,
        candidate_person_ids: list[UUID] | None = None,
    ) -> RegistrationAdminListItem:
        candidate_ids = (
            self._candidate_ids(application, identifier)
            if candidate_person_ids is None
            else candidate_person_ids
        )
        return RegistrationAdminListItem(
            application_id=application.id,
            primary_purpose=cast(RegistrationPurpose, application.primary_purpose),
            state="conflict",
            masked_identifier=self._mask_identifier(identifier),
            conflict_code=application.conflict_code,
            candidate_count=len(candidate_ids),
            expires_at=application.expires_at,
            created_at=application.created_at,
        )

    def list_conflicts(
        self,
        *,
        candidate_allowed: Callable[[list[UUID]], bool] | None = None,
    ) -> list[RegistrationAdminListItem]:
        applications = list(
            self.db.scalars(
                select(RegistrationApplication)
                .where(RegistrationApplication.state == "conflict")
                .order_by(RegistrationApplication.created_at, RegistrationApplication.id)
            )
        )
        result: list[RegistrationAdminListItem] = []
        for application in applications:
            identity_candidate_ids = self._identity_candidate_ids(application)
            if (
                candidate_allowed is not None
                and not candidate_allowed(identity_candidate_ids)
            ):
                continue
            identifier = self._identifier(application)
            candidate_person_ids = self._candidate_ids(
                application,
                identifier,
                identity_candidate_ids=identity_candidate_ids,
            )
            if (
                candidate_allowed is not None
                and not candidate_allowed(candidate_person_ids)
            ):
                continue
            result.append(
                self._base_item(
                    application,
                    identifier,
                    candidate_person_ids=candidate_person_ids,
                )
            )
        return result

    def detail(
        self,
        application_id: UUID,
        *,
        sensitive: bool,
        candidate_person_ids: list[UUID],
    ) -> RegistrationAdminDetail:
        application = self.db.get(RegistrationApplication, application_id)
        if application is None or application.state != "conflict":
            raise RegistrationReviewNotFound("registration application not found")
        identifier = self._identifier(application)
        base = self._base_item(
            application,
            identifier,
            candidate_person_ids=candidate_person_ids,
        )
        if not sensitive:
            return RegistrationAdminDetail(**base.model_dump())
        snapshot = self._snapshot(application)
        candidates: list[RegistrationAdminCandidate] = []
        for person in self.db.scalars(
            select(Person)
            .where(Person.id.in_(candidate_person_ids))
            .order_by(Person.id)
        ):
            account_status = self.db.scalar(
                select(UserAccount.status).where(UserAccount.person_id == person.id)
            )
            candidates.append(
                RegistrationAdminCandidate(
                    person_id=person.id,
                    full_name=person.full_name,
                    identity_number=self._person_identity(person),
                    sex=cast(Literal["male", "female"], person.sex),
                    date_of_birth=person.date_of_birth,
                    contact=self._person_contact(person, snapshot),
                    deleted=person.deleted_at is not None,
                    account_status=account_status,
                )
            )
        return RegistrationAdminDetail(
            **base.model_dump(),
            full_name=snapshot.full_name,
            identity_number=snapshot.identity_number,
            sex=snapshot.sex,
            date_of_birth=snapshot.date_of_birth,
            verified_identifier=snapshot.identifier.value,
            candidates=candidates,
        )

    def candidate_person_ids(self, application_id: UUID) -> list[UUID]:
        application = self.db.get(RegistrationApplication, application_id)
        if application is None or application.state != "conflict":
            raise RegistrationReviewNotFound("registration application not found")
        return self._candidate_ids(application, self._identifier(application))

    def resolve(
        self,
        application_id: UUID,
        *,
        candidate_person_id: UUID,
        actor_user_id: UUID,
        reason: str,
        request_id: str,
    ) -> RegistrationFinalizationResult | None:
        self._validate_reason(reason)
        now = self._now()
        try:
            with write_transaction(self.db):
                try:
                    claim_snapshot = (
                        self.finalization.lock_candidate_claim_snapshot_in_transaction(
                            application_id
                        )
                    )
                except RegistrationFinalizationUnavailable as exc:
                    raise RegistrationReviewNotFound(
                        "registration application not found"
                    ) from exc
                application = self.db.scalar(
                    select(RegistrationApplication)
                    .where(RegistrationApplication.id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if (
                    application is None
                    or claim_snapshot.state != "conflict"
                    or not self.finalization.candidate_claim_snapshot_matches(
                        claim_snapshot,
                        application,
                    )
                    or application.state != "conflict"
                    or application.expires_at <= now
                ):
                    raise RegistrationReviewNotFound(
                        "registration application not found"
                    )
                match = self.matching.classify_in_transaction(application)
                if (
                    match.kind not in {"claim_existing", "existing_account"}
                    or match.person_id != candidate_person_id
                ):
                    raise RegistrationReviewNotFound(
                        "registration candidate not found"
                    )
                person = self.db.get(Person, candidate_person_id)
                if person is None or person.deleted_at is not None:
                    raise RegistrationReviewNotFound(
                        "registration candidate not found"
                    )
                account = self.db.scalar(
                    select(UserAccount)
                    .where(UserAccount.person_id == candidate_person_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if account is not None and account.status != "active":
                    raise RegistrationReviewNotFound(
                        "registration candidate not found"
                    )
                guardian_confirmation_required = (
                    application.primary_purpose == "student"
                    and application.date_of_birth is not None
                    and is_minor(
                        application.date_of_birth,
                        business_date_at(now),
                    )
                )
                application.state = (
                    "pending_guardian"
                    if guardian_confirmation_required
                    else "ready_to_finalize"
                )
                application.conflict_code = None
                application.review_decision = "approved"
                application.reviewed_by_user_id = actor_user_id
                application.reviewed_candidate_person_id = (
                    candidate_person_id
                    if guardian_confirmation_required
                    else None
                )
                application.review_reason = reason
                application.reviewed_at = now
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="registration.conflict.resolve",
                    resource_type="registration_application",
                    resource_id=application.id,
                    purpose="registration_conflict_review",
                    outcome="allowed",
                    request_id=request_id,
                    metadata={"review_decision": "approved"},
                )
                if guardian_confirmation_required:
                    self.db.flush()
                    return None
                return self.finalization.finalize(
                    application_id,
                    request_id=request_id,
                )
        except AuditMetadataRejected as exc:
            raise RegistrationReviewReasonInvalid(
                "registration review reason is invalid"
            ) from exc

    def reject(
        self,
        application_id: UUID,
        *,
        actor_user_id: UUID,
        reason: str,
        request_id: str,
        candidate_allowed: Callable[[list[UUID]], bool] | None = None,
    ) -> None:
        self._validate_reason(reason)
        now = self._now()
        try:
            with write_transaction(self.db):
                try:
                    claim_snapshot = (
                        self.finalization.lock_candidate_claim_snapshot_in_transaction(
                            application_id
                        )
                    )
                except RegistrationFinalizationUnavailable as exc:
                    raise RegistrationReviewNotFound(
                        "registration application not found"
                    ) from exc
                application = self.db.scalar(
                    select(RegistrationApplication)
                    .where(RegistrationApplication.id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if (
                    application is None
                    or claim_snapshot.state != "conflict"
                    or not self.finalization.candidate_claim_snapshot_matches(
                        claim_snapshot,
                        application,
                    )
                    or application.state != "conflict"
                ):
                    raise RegistrationReviewNotFound(
                        "registration application not found"
                    )
                identity_candidate_ids = self._identity_candidate_ids(
                    application
                )
                if (
                    candidate_allowed is not None
                    and not candidate_allowed(identity_candidate_ids)
                ):
                    raise RegistrationReviewNotFound(
                        "registration application not found"
                    )
                identifier = self._identifier(application)
                candidate_person_ids = self._candidate_ids(
                    application,
                    identifier,
                    identity_candidate_ids=identity_candidate_ids,
                )
                if (
                    candidate_allowed is not None
                    and not candidate_allowed(candidate_person_ids)
                ):
                    raise RegistrationReviewNotFound(
                        "registration application not found"
                    )
                if candidate_person_ids:
                    self.db.execute(
                        select(Person.id)
                        .where(Person.id.in_(candidate_person_ids))
                        .order_by(Person.id)
                        .with_for_update()
                    ).all()
                self._lock_candidate_memberships(application, identifier)
                refreshed_candidate_ids = self._candidate_ids(
                    application,
                    identifier,
                )
                if (
                    refreshed_candidate_ids != candidate_person_ids
                    or (
                        candidate_allowed is not None
                        and not candidate_allowed(refreshed_candidate_ids)
                    )
                ):
                    raise RegistrationReviewNotFound(
                        "registration application not found"
                    )
                clear_registration_application_secrets(application)
                application.state = "rejected"
                application.conflict_code = "review_rejected"
                application.rejected_at = now
                application.review_decision = "rejected"
                application.reviewed_by_user_id = actor_user_id
                application.reviewed_candidate_person_id = None
                application.review_reason = reason
                application.reviewed_at = now
                self.db.execute(
                    update(RegistrationSession)
                    .where(
                        RegistrationSession.application_id == application.id,
                        RegistrationSession.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="registration.conflict.reject",
                    resource_type="registration_application",
                    resource_id=application.id,
                    purpose="registration_conflict_review",
                    outcome="allowed",
                    request_id=request_id,
                    metadata={"review_decision": "rejected"},
                )
                self.db.flush()
        except AuditMetadataRejected as exc:
            raise RegistrationReviewReasonInvalid(
                "registration review reason is invalid"
            ) from exc
