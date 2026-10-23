from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, SessionTransactionOrigin

from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.core.time import business_date_at
from app.core.transactions import write_transaction
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.identity.readiness import GuardianReadinessPolicy, age_status_for, is_minor
from app.identity.service import IdentityService, IdentityServiceError
from app.rbac.models import Role, UserRole
from app.registration.delivery import VerificationSender
from app.registration.finalization_service import (
    RegistrationConflictPendingReview,
    RegistrationFinalizationError,
    RegistrationFinalizationService,
)
from app.registration.identifiers import (
    NormalizedRegistrationIdentifier,
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.invitation_flow import (
    guardian_invitation_handoff_token_digest,
    issue_guardian_invitation_handoff_token,
)
from app.registration.invitation_retention import (
    REDACTED_GUARDIAN_INVITATION_CIPHERTEXT,
    REDACTED_GUARDIAN_INVITATION_DIGEST,
    REDACTED_GUARDIAN_INVITATION_MASK,
    clear_guardian_invitation_identifier,
    guardian_invitation_identifier_is_cleared,
)
from app.registration.models import (
    GuardianRegistrationInvitation,
    IdentifierKind,
    RegistrationApplication,
    RegistrationConsentAcceptance,
)
from app.registration.privacy import contains_sensitive_registration_text
from app.security.encryption import EnvelopeCipher, LookupDigest

InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]

_INVITATION_TTL = timedelta(days=7)
_GUARDIAN_CONSENT_CONTENT = {
    "2026-08": (
        "青训 ERP 监护关系确认|版本 2026-08|本人确认受邀联系方式归本人所有，"
        "同意建立监护关系并按家庭范围查看被监护学员资料。"
    )
}


class GuardianInvitationError(ValueError):
    code = "guardian_invitation_error"


class GuardianInvitationUnavailable(GuardianInvitationError):
    code = "registration_action_unavailable"


@dataclass(frozen=True, slots=True)
class GuardianInvitationSummary:
    id: UUID
    identifier_kind: IdentifierKind
    masked_identifier: str
    relationship: str
    is_primary: bool
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GuardianInvitationAcceptance:
    invitation_id: UUID
    relationship_id: UUID
    student_finalized: bool


@dataclass(frozen=True, slots=True)
class GuardianInvitationFlowContext:
    invitation_id: UUID
    identifier: NormalizedRegistrationIdentifier = field(repr=False)
    masked_identifier: str
    relationship: str
    is_primary: bool
    expires_at: datetime
    consent_version: str


class GuardianRegistrationService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        identity: IdentityService,
        finalization: RegistrationFinalizationService,
        sender: VerificationSender,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup
        self.identity = identity
        self.finalization = finalization
        self.sender = sender
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("guardian invitation clock must be timezone-aware")
        return value

    @contextmanager
    def _acceptance_transaction(self) -> Iterator[None]:
        transaction = self.db.get_transaction()
        if transaction is not None and (
            transaction.origin is SessionTransactionOrigin.AUTOBEGIN
        ):
            self.db.commit()
            transaction = None
        if transaction is None:
            with self.db.begin():
                yield
            return
        with self.db.begin_nested():
            yield

    @staticmethod
    def _mask(identifier: NormalizedRegistrationIdentifier) -> str:
        if identifier.kind == "phone":
            return f"{identifier.value[:3]}****{identifier.value[-4:]}"
        local, domain = identifier.value.rsplit("@", 1)
        return f"{local[:1]}***@{domain}"

    def _identifier(
        self,
        invitation: GuardianRegistrationInvitation,
    ) -> NormalizedRegistrationIdentifier:
        if guardian_invitation_identifier_is_cleared(invitation):
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        try:
            normalized = normalize_registration_identifier(
                cast(IdentifierKind, invitation.invited_identifier_kind),
                self.cipher.decrypt(
                    invitation.invited_identifier_ciphertext,
                    field_name="guardian_invitation_identifier",
                ),
            )
        except (ValueError, TypeError) as exc:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            ) from exc
        if not hmac.compare_digest(
            invitation.invited_identifier_digest,
            registration_identifier_digest(self.lookup, normalized),
        ):
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        return normalized

    def _summary(
        self,
        invitation: GuardianRegistrationInvitation,
    ) -> GuardianInvitationSummary:
        if guardian_invitation_identifier_is_cleared(invitation):
            return GuardianInvitationSummary(
                id=invitation.id,
                identifier_kind=cast(
                    IdentifierKind,
                    invitation.invited_identifier_kind,
                ),
                masked_identifier=REDACTED_GUARDIAN_INVITATION_MASK,
                relationship=invitation.relationship,
                is_primary=invitation.is_primary,
                status=cast(InvitationStatus, invitation.status),
                expires_at=invitation.expires_at,
                created_at=invitation.created_at,
            )
        identifier = self._identifier(invitation)
        return GuardianInvitationSummary(
            id=invitation.id,
            identifier_kind=identifier.kind,
            masked_identifier=self._mask(identifier),
            relationship=invitation.relationship,
            is_primary=invitation.is_primary,
            status=cast(InvitationStatus, invitation.status),
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
        )

    def invitation_flow_context(
        self,
        invitation_id: UUID,
        *,
        lock: bool = False,
    ) -> GuardianInvitationFlowContext:
        target = None
        if lock:
            target = self.db.execute(
                select(
                    GuardianRegistrationInvitation.application_id,
                    GuardianRegistrationInvitation.student_person_id,
                ).where(GuardianRegistrationInvitation.id == invitation_id)
            ).one_or_none()
            if target is not None and target[0] is not None:
                self.db.scalar(
                    select(RegistrationApplication)
                    .where(RegistrationApplication.id == target[0])
                    .with_for_update()
                )
        invitation_query = select(GuardianRegistrationInvitation).where(
            GuardianRegistrationInvitation.id == invitation_id
        )
        if lock:
            invitation_query = invitation_query.with_for_update()
        invitation = self.db.scalar(
            invitation_query.execution_options(populate_existing=True)
        )
        now = self._now()
        if (
            invitation is None
            or (
                target is not None
                and (
                    invitation.application_id != target[0]
                    or invitation.student_person_id != target[1]
                )
            )
            or invitation.status != "pending"
            or invitation.expires_at <= now
            or guardian_invitation_identifier_is_cleared(invitation)
            or not self._invitation_target_available(invitation, now=now, lock=lock)
        ):
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        identifier = self._identifier(invitation)
        return GuardianInvitationFlowContext(
            invitation_id=invitation.id,
            identifier=identifier,
            masked_identifier=self._mask(identifier),
            relationship=invitation.relationship,
            is_primary=invitation.is_primary,
            expires_at=invitation.expires_at,
            consent_version=next(iter(_GUARDIAN_CONSENT_CONTENT)),
        )

    def invitation_id_for_handoff_token(self, token: str) -> UUID:
        token_digest = guardian_invitation_handoff_token_digest(self.lookup, token)
        if token_digest is None:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        invitation_id = self.db.scalar(
            select(GuardianRegistrationInvitation.id).where(
                GuardianRegistrationInvitation.handoff_token_digest == token_digest,
                GuardianRegistrationInvitation.status == "pending",
            )
        )
        if invitation_id is None:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        self.invitation_flow_context(invitation_id)
        return invitation_id

    def _invitation_target_available(
        self,
        invitation: GuardianRegistrationInvitation,
        *,
        now: datetime,
        lock: bool,
    ) -> bool:
        if invitation.application_id is not None:
            application_query = select(RegistrationApplication).where(
                RegistrationApplication.id == invitation.application_id
            )
            if lock:
                application_query = application_query.with_for_update()
            application = self.db.scalar(
                application_query.execution_options(populate_existing=True)
            )
            if application is None or application.primary_purpose != "student":
                return False
            if application.state in {"identity_completed", "pending_guardian"}:
                return bool(
                    application.expires_at > now
                    and application.date_of_birth is not None
                    and is_minor(
                        application.date_of_birth,
                        business_date_at(now),
                    )
                )
            if application.state != "finalized" or application.matched_person_id is None:
                return False
            return self._formal_student_target_available(
                application.matched_person_id,
                now=now,
                require_adult=False,
                lock=lock,
            )
        if invitation.student_person_id is None:
            return False
        return self._formal_student_target_available(
            invitation.student_person_id,
            now=now,
            require_adult=True,
            lock=lock,
        )

    def _formal_student_target_available(
        self,
        student_person_id: UUID,
        *,
        now: datetime,
        require_adult: bool,
        lock: bool,
    ) -> bool:
        person_query = select(Person).where(Person.id == student_person_id)
        if lock:
            person_query = person_query.with_for_update()
        person = self.db.scalar(person_query.execution_options(populate_existing=True))
        account_query = select(UserAccount).where(
            UserAccount.person_id == student_person_id
        )
        if lock:
            account_query = account_query.with_for_update()
        account = self.db.scalar(account_query.execution_options(populate_existing=True))
        return bool(
            person is not None
            and person.deleted_at is None
            and person.date_of_birth is not None
            and self.db.get(StudentProfile, student_person_id) is not None
            and account is not None
            and account.status == "active"
            and (
                not require_adult
                or not is_minor(person.date_of_birth, business_date_at(now))
            )
        )

    @staticmethod
    def _relationship(value: str) -> str:
        normalized = value.strip()
        if (
            not 2 <= len(normalized) <= 40
            or contains_sensitive_registration_text(normalized)
        ):
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        return normalized

    def _expire_pending(
        self,
        *,
        now: datetime,
        application_id: UUID | None = None,
        student_person_id: UUID | None = None,
    ) -> None:
        target = (
            GuardianRegistrationInvitation.application_id == application_id
            if application_id is not None
            else GuardianRegistrationInvitation.student_person_id == student_person_id
        )
        self.db.execute(
            update(GuardianRegistrationInvitation)
            .where(
                target,
                GuardianRegistrationInvitation.status == "pending",
                GuardianRegistrationInvitation.expires_at <= now,
            )
            .values(
                status="expired",
                handoff_token_digest=None,
                invited_identifier_digest=REDACTED_GUARDIAN_INVITATION_DIGEST,
                invited_identifier_ciphertext=(
                    REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
                ),
            )
        )

    def _create_invitation(
        self,
        *,
        application_id: UUID | None,
        student_person_id: UUID | None,
        identifier_kind: IdentifierKind,
        identifier: str,
        relationship: str,
        is_primary: bool,
        now: datetime,
    ) -> GuardianInvitationSummary:
        normalized = normalize_registration_identifier(identifier_kind, identifier)
        handoff_token = issue_guardian_invitation_handoff_token()
        handoff_token_digest = guardian_invitation_handoff_token_digest(
            self.lookup,
            handoff_token,
        )
        if handoff_token_digest is None:
            raise RuntimeError("generated guardian invitation token is invalid")
        invitation = GuardianRegistrationInvitation(
            application_id=application_id,
            student_person_id=student_person_id,
            invited_identifier_kind=normalized.kind,
            invited_identifier_digest=registration_identifier_digest(
                self.lookup,
                normalized,
            ),
            invited_identifier_ciphertext=self.cipher.encrypt(
                normalized.value,
                field_name="guardian_invitation_identifier",
            ),
            handoff_token_digest=handoff_token_digest,
            relationship=self._relationship(relationship),
            is_primary=is_primary,
            status="pending",
            expires_at=now + _INVITATION_TTL,
        )
        self.db.add(invitation)
        self.db.flush()
        self.sender.send_code(
            challenge_id=invitation.id,
            destination=normalized.value,
            code=handoff_token,
            purpose="guardian_invitation",
        )
        return self._summary(invitation)

    def _lock_minor_application(
        self,
        application_id: UUID,
        *,
        now: datetime,
    ) -> RegistrationApplication:
        application = self.db.scalar(
            select(RegistrationApplication)
            .where(RegistrationApplication.id == application_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        reference_date = business_date_at(now)
        if (
            application is None
            or application.primary_purpose != "student"
            or application.state not in {"identity_completed", "pending_guardian"}
            or application.expires_at <= now
            or application.date_of_birth is None
            or not is_minor(application.date_of_birth, reference_date)
        ):
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        return application

    def _lock_adult_student_account(
        self,
        account_id: UUID,
        *,
        now: datetime,
    ) -> tuple[UserAccount, Person]:
        account, person = self._lock_account_person(account_id)
        if (
            account is None
            or account.status != "active"
            or person is None
            or person.deleted_at is not None
            or self.db.get(StudentProfile, person.id) is None
            or age_status_for(person.date_of_birth, business_date_at(now)) != "adult"
        ):
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        return account, person

    def _lock_account_person(
        self,
        account_id: UUID,
    ) -> tuple[UserAccount | None, Person | None]:
        person_id = self.db.scalar(
            select(UserAccount.person_id).where(UserAccount.id == account_id)
        )
        person = (
            self.db.scalar(
                select(Person)
                .where(Person.id == person_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if person_id is not None
            else None
        )
        account = (
            self.db.scalar(
                select(UserAccount)
                .where(
                    UserAccount.id == account_id,
                    UserAccount.person_id == person_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if person is not None
            else None
        )
        return account, person

    def invite(
        self,
        application_id: UUID,
        *,
        identifier_kind: IdentifierKind,
        identifier: str,
        relationship: str,
        is_primary: bool,
        request_id: str,
    ) -> GuardianInvitationSummary:
        del request_id
        try:
            with write_transaction(self.db):
                now = self._now()
                application = self._lock_minor_application(application_id, now=now)
                self._expire_pending(now=now, application_id=application.id)
                application.state = "pending_guardian"
                return self._create_invitation(
                    application_id=application.id,
                    student_person_id=None,
                    identifier_kind=identifier_kind,
                    identifier=identifier,
                    relationship=relationship,
                    is_primary=is_primary,
                    now=now,
                )
        except GuardianInvitationUnavailable:
            raise
        except (IntegrityError, ValueError) as exc:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            ) from exc

    def invite_for_account(
        self,
        student_account_id: UUID,
        *,
        identifier_kind: IdentifierKind,
        identifier: str,
        relationship: str,
        is_primary: bool,
        request_id: str,
    ) -> GuardianInvitationSummary:
        del request_id
        try:
            with write_transaction(self.db):
                now = self._now()
                _account, student = self._lock_adult_student_account(
                    student_account_id,
                    now=now,
                )
                self._expire_pending(now=now, student_person_id=student.id)
                return self._create_invitation(
                    application_id=None,
                    student_person_id=student.id,
                    identifier_kind=identifier_kind,
                    identifier=identifier,
                    relationship=relationship,
                    is_primary=is_primary,
                    now=now,
                )
        except GuardianInvitationUnavailable:
            raise
        except (IntegrityError, ValueError) as exc:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            ) from exc

    def list_for_application(
        self,
        application_id: UUID,
    ) -> list[GuardianInvitationSummary]:
        with write_transaction(self.db):
            now = self._now()
            application = self._lock_minor_application(application_id, now=now)
            self._expire_pending(now=now, application_id=application.id)
            rows = list(
                self.db.scalars(
                    select(GuardianRegistrationInvitation)
                    .where(
                        GuardianRegistrationInvitation.application_id == application.id
                    )
                    .order_by(
                        GuardianRegistrationInvitation.created_at,
                        GuardianRegistrationInvitation.id,
                    )
                )
            )
            return [self._summary(row) for row in rows]

    def list_for_account(
        self,
        student_account_id: UUID,
    ) -> list[GuardianInvitationSummary]:
        with write_transaction(self.db):
            now = self._now()
            _account, student = self._lock_adult_student_account(
                student_account_id,
                now=now,
            )
            self._expire_pending(now=now, student_person_id=student.id)
            rows = list(
                self.db.scalars(
                    select(GuardianRegistrationInvitation)
                    .where(
                        GuardianRegistrationInvitation.student_person_id == student.id
                    )
                    .order_by(
                        GuardianRegistrationInvitation.created_at,
                        GuardianRegistrationInvitation.id,
                    )
                )
            )
            return [self._summary(row) for row in rows]

    def resend(
        self,
        application_id: UUID,
        invitation_id: UUID,
        *,
        request_id: str,
    ) -> GuardianInvitationSummary:
        del request_id
        expired = False
        result: GuardianInvitationSummary | None = None
        with write_transaction(self.db):
            now = self._now()
            application = self._lock_minor_application(application_id, now=now)
            invitation = self.db.scalar(
                select(GuardianRegistrationInvitation)
                .where(
                    GuardianRegistrationInvitation.id == invitation_id,
                    GuardianRegistrationInvitation.application_id == application.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if invitation is None or invitation.status != "pending":
                raise GuardianInvitationUnavailable(
                    "guardian invitation is unavailable"
                )
            if invitation.expires_at <= now:
                invitation.status = "expired"
                clear_guardian_invitation_identifier(invitation)
                expired = True
            else:
                identifier = self._identifier(invitation)
                invitation.status = "revoked"
                invitation.revoked_at = now
                clear_guardian_invitation_identifier(invitation)
                self.db.flush()
                result = self._create_invitation(
                    application_id=application.id,
                    student_person_id=None,
                    identifier_kind=identifier.kind,
                    identifier=identifier.value,
                    relationship=invitation.relationship,
                    is_primary=invitation.is_primary,
                    now=now,
                )
        if expired or result is None:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        return result

    def revoke(
        self,
        application_id: UUID,
        invitation_id: UUID,
        *,
        request_id: str,
    ) -> GuardianInvitationSummary:
        del request_id
        expired = False
        result: GuardianInvitationSummary | None = None
        with write_transaction(self.db):
            now = self._now()
            application = self._lock_minor_application(application_id, now=now)
            invitation = self.db.scalar(
                select(GuardianRegistrationInvitation)
                .where(
                    GuardianRegistrationInvitation.id == invitation_id,
                    GuardianRegistrationInvitation.application_id == application.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if invitation is None or invitation.status != "pending":
                raise GuardianInvitationUnavailable(
                    "guardian invitation is unavailable"
                )
            if invitation.expires_at <= now:
                invitation.status = "expired"
                clear_guardian_invitation_identifier(invitation)
                expired = True
            else:
                invitation.status = "revoked"
                invitation.revoked_at = now
                clear_guardian_invitation_identifier(invitation)
                self.db.flush()
                result = self._summary(invitation)
        if expired or result is None:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        return result

    def _account_owns_identifier(
        self,
        account_id: UUID,
        identifier: NormalizedRegistrationIdentifier,
    ) -> bool:
        digest = login_identifier_digest(
            self.lookup,
            NormalizedLoginIdentifier(identifier.kind, identifier.value),
        )
        return (
            self.db.scalar(
                select(AccountLoginIdentifier.id).where(
                    AccountLoginIdentifier.account_id == account_id,
                    AccountLoginIdentifier.kind == identifier.kind,
                    AccountLoginIdentifier.identifier_digest == digest,
                )
            )
            is not None
        )

    def _ensure_guardian_role(self, account_id: UUID) -> None:
        role = self.db.scalar(select(Role).where(Role.key == "guardian"))
        if role is None:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        if self.db.get(UserRole, (account_id, role.id)) is None:
            self.db.add(UserRole(user_id=account_id, role_id=role.id))
            self.db.flush()

    def _active_relationship_id(
        self,
        *,
        student_person_id: UUID,
        guardian_person_id: UUID,
        reference_date: date,
    ) -> UUID | None:
        return self.db.scalar(
            select(GuardianRelationship.id)
            .where(
                GuardianRelationship.student_person_id == student_person_id,
                GuardianRelationship.guardian_person_id == guardian_person_id,
                GuardianRelationship.ended_at.is_(None),
                GuardianRelationship.valid_from <= reference_date,
                (
                    GuardianRelationship.valid_until.is_(None)
                    | (GuardianRelationship.valid_until >= reference_date)
                ),
            )
            .order_by(
                GuardianRelationship.is_primary.desc(),
                GuardianRelationship.valid_from.desc(),
                GuardianRelationship.id,
            )
        )

    def _relationship_for_accepted(
        self,
        *,
        invitation: GuardianRegistrationInvitation,
        guardian_person_id: UUID,
        application: RegistrationApplication | None,
    ) -> GuardianInvitationAcceptance:
        student_person_id = (
            application.matched_person_id
            if application is not None
            else invitation.student_person_id
        )
        if student_person_id is None:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        relationship_id = self.db.scalar(
            select(GuardianRelationship.id)
            .where(
                GuardianRelationship.student_person_id == student_person_id,
                GuardianRelationship.guardian_person_id == guardian_person_id,
                GuardianRelationship.ended_at.is_(None),
            )
            .order_by(GuardianRelationship.created_at.desc())
        )
        if relationship_id is None:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        return GuardianInvitationAcceptance(
            invitation_id=invitation.id,
            relationship_id=relationship_id,
            student_finalized=(
                application is not None
                and application.finalized_at is not None
                and invitation.accepted_at is not None
                and application.finalized_at >= invitation.accepted_at
            ),
        )

    def acceptance_receipt(
        self,
        invitation_id: UUID,
        *,
        guardian_account_id: UUID,
    ) -> GuardianInvitationAcceptance:
        invitation = self.db.get(GuardianRegistrationInvitation, invitation_id)
        account = self.db.get(UserAccount, guardian_account_id)
        if (
            invitation is None
            or invitation.status != "accepted"
            or account is None
            or account.status != "active"
            or invitation.accepted_by_person_id != account.person_id
        ):
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        application = (
            self.db.get(RegistrationApplication, invitation.application_id)
            if invitation.application_id is not None
            else None
        )
        return self._relationship_for_accepted(
            invitation=invitation,
            guardian_person_id=account.person_id,
            application=application,
        )

    def accept(
        self,
        invitation_id: UUID,
        *,
        guardian_account_id: UUID,
        consent_version: str,
        request_id: str,
    ) -> GuardianInvitationAcceptance:
        result: GuardianInvitationAcceptance | None = None
        conflict_pending = False
        try:
            with self._acceptance_transaction():
                now = self._now()
                target = self.db.execute(
                    select(
                        GuardianRegistrationInvitation.application_id,
                        GuardianRegistrationInvitation.student_person_id,
                    ).where(GuardianRegistrationInvitation.id == invitation_id)
                ).one_or_none()
                claim_snapshot = (
                    self.finalization.lock_candidate_claim_snapshot_in_transaction(
                        target[0]
                    )
                    if target is not None and target[0] is not None
                    else None
                )
                application = (
                    self.db.scalar(
                        select(RegistrationApplication)
                        .where(RegistrationApplication.id == target[0])
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if target is not None and target[0] is not None
                    else None
                )
                if (
                    claim_snapshot is not None
                    and (
                        application is None
                        or (
                            not self.finalization.candidate_claim_snapshot_matches(
                                claim_snapshot,
                                application,
                            )
                            and not (
                                claim_snapshot.state
                                in {"identity_completed", "pending_guardian"}
                                and application.state == "finalized"
                            )
                        )
                    )
                ):
                    raise GuardianInvitationUnavailable(
                        "guardian invitation is unavailable"
                    )
                invitation = self.db.scalar(
                    select(GuardianRegistrationInvitation)
                    .where(GuardianRegistrationInvitation.id == invitation_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if invitation is None or (
                    target is not None
                    and (
                        invitation.application_id != target[0]
                        or invitation.student_person_id != target[1]
                    )
                ):
                    raise GuardianInvitationUnavailable(
                        "guardian invitation is unavailable"
                    )
                if (
                    invitation.status == "pending" and invitation.expires_at <= now
                ) or invitation.status not in {"pending", "accepted"}:
                    raise GuardianInvitationUnavailable(
                        "guardian invitation is unavailable"
                    )
                else:
                    guardian_person_id = self.db.scalar(
                        select(UserAccount.person_id).where(
                            UserAccount.id == guardian_account_id
                        )
                    )
                    if guardian_person_id is None:
                        raise GuardianInvitationUnavailable(
                            "guardian invitation is unavailable"
                        )
                    readiness_policy = GuardianReadinessPolicy(self.db)
                    readiness_policy.lock_guardian_relationships(
                        guardian_person_id
                    )
                    student_person_id_to_lock = invitation.student_person_id
                    if application is not None and application.state == "finalized":
                        student_person_id_to_lock = application.matched_person_id
                    people_to_lock = {guardian_person_id}
                    if application is not None and application.state != "finalized":
                        if (
                            application.primary_purpose != "student"
                            or application.state
                            not in {"identity_completed", "pending_guardian"}
                            or application.expires_at <= now
                            or application.date_of_birth is None
                            or not is_minor(
                                application.date_of_birth,
                                business_date_at(now),
                            )
                        ):
                            raise GuardianInvitationUnavailable(
                                "guardian invitation is unavailable"
                            )
                        people_to_lock.update(
                            self.finalization.lock_candidate_person_ids_in_transaction(
                                application
                            )
                        )
                    if student_person_id_to_lock is not None:
                        people_to_lock.add(student_person_id_to_lock)
                    locked_people = {
                        person.id: person
                        for person in readiness_policy.lock_students(people_to_lock)
                    }
                    guardian = locked_people.get(guardian_person_id)
                    locked_accounts = {
                        row.id: row
                        for row in self.db.scalars(
                            select(UserAccount)
                            .where(UserAccount.person_id.in_(people_to_lock))
                            .order_by(UserAccount.id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                    }
                    account = locked_accounts.get(guardian_account_id)
                    student = (
                        locked_people.get(student_person_id_to_lock)
                        if student_person_id_to_lock is not None
                        else None
                    )
                    student_account = next(
                        (
                            row
                            for row in locked_accounts.values()
                            if row.person_id == student_person_id_to_lock
                        ),
                        None,
                    )
                    if (
                        account is None
                        or account.status != "active"
                        or guardian is None
                        or guardian.id != guardian_person_id
                        or guardian.deleted_at is not None
                        or age_status_for(guardian.date_of_birth, business_date_at(now))
                        != "adult"
                    ):
                        raise GuardianInvitationUnavailable(
                            "guardian invitation is unavailable"
                        )
                    if student_person_id_to_lock is not None and (
                        student is None
                        or student.deleted_at is not None
                        or student.date_of_birth is None
                        or student_account is None
                        or student_account.status != "active"
                        or self.db.get(StudentProfile, student_person_id_to_lock) is None
                        or (
                            application is None
                            and is_minor(
                                student.date_of_birth,
                                business_date_at(now),
                            )
                        )
                    ):
                        raise GuardianInvitationUnavailable(
                            "guardian invitation is unavailable"
                        )
                    if not readiness_policy.guardian_profile_complete(
                        guardian_person_id
                    ):
                        raise GuardianInvitationUnavailable(
                            "guardian invitation is unavailable"
                        )
                    if invitation.status == "accepted":
                        if invitation.accepted_by_person_id != guardian_person_id:
                            raise GuardianInvitationUnavailable(
                                "guardian invitation is unavailable"
                            )
                        result = self._relationship_for_accepted(
                            invitation=invitation,
                            guardian_person_id=guardian_person_id,
                            application=application,
                        )
                    else:
                        identifier = self._identifier(invitation)
                        if not self._account_owns_identifier(account.id, identifier):
                            raise GuardianInvitationUnavailable(
                                "guardian invitation is unavailable"
                            )
                        consent_content = _GUARDIAN_CONSENT_CONTENT.get(
                            consent_version
                        )
                        if consent_content is None:
                            raise GuardianInvitationUnavailable(
                                "guardian invitation is unavailable"
                            )
                        finalized_now = False
                        student_person_id: UUID | None = None
                        if application is not None:
                            if application.state == "finalized":
                                student_person_id = application.matched_person_id
                            elif (
                                application.state
                                in {"identity_completed", "pending_guardian"}
                                and application.expires_at > now
                            ):
                                application.state = "ready_to_finalize"
                                try:
                                    finalized = self.finalization.finalize(
                                        application.id,
                                        request_id=request_id,
                                    )
                                except RegistrationConflictPendingReview:
                                    conflict_pending = True
                                else:
                                    student_person_id = finalized.person_id
                                    finalized_now = True
                            else:
                                raise GuardianInvitationUnavailable(
                                    "guardian invitation is unavailable"
                                )
                        else:
                            student_person_id = invitation.student_person_id
                        if not conflict_pending:
                            if (
                                student_person_id is None
                                or self.db.get(StudentProfile, student_person_id) is None
                            ):
                                raise GuardianInvitationUnavailable(
                                    "guardian invitation is unavailable"
                                )
                            self._ensure_guardian_role(account.id)
                            self.db.add(
                                RegistrationConsentAcceptance(
                                    registration_application_id=None,
                                    account_id=account.id,
                                    consent_type="guardian_relationship",
                                    policy_version=consent_version,
                                    content_digest=hashlib.sha256(
                                        consent_content.encode("utf-8")
                                    ).hexdigest(),
                                    accepted_at=now,
                                    request_id=request_id,
                                    source="guardian_invitation",
                                )
                            )
                            self.db.flush()
                            reference_date = business_date_at(now)
                            relationship_id = self._active_relationship_id(
                                student_person_id=student_person_id,
                                guardian_person_id=guardian_person_id,
                                reference_date=reference_date,
                            )
                            if relationship_id is None:
                                relationship = self.identity.add_guardian(
                                    student_person_id,
                                    guardian_person_id,
                                    relationship=invitation.relationship,
                                    is_primary=invitation.is_primary,
                                    valid_from=reference_date,
                                    valid_until=None,
                                )
                                relationship_id = relationship.relationship_id
                            # Verified identifier ownership and real consent above
                            # authorize this exact active pair, never every link of
                            # an already-verified guardian account.
                            confirmed_relationship = self.db.scalar(
                                select(GuardianRelationship)
                                .where(
                                    GuardianRelationship.id == relationship_id,
                                    GuardianRelationship.guardian_person_id
                                    == guardian_person_id,
                                    GuardianRelationship.student_person_id
                                    == student_person_id,
                                    GuardianRelationship.ended_at.is_(None),
                                    GuardianRelationship.valid_from <= reference_date,
                                    or_(
                                        GuardianRelationship.valid_until.is_(None),
                                        GuardianRelationship.valid_until
                                        >= reference_date,
                                    ),
                                )
                                .with_for_update()
                                .execution_options(populate_existing=True)
                            )
                            if confirmed_relationship is None:
                                raise GuardianInvitationUnavailable(
                                    "guardian invitation is unavailable"
                                )
                            confirmed_relationship.requires_verification = False
                            self.db.flush()
                            readiness = readiness_policy.evaluate_one(
                                student_person_id,
                                reference_date,
                            )
                            student = self.db.get(Person, student_person_id)
                            if (
                                student is None
                                or student.date_of_birth is None
                                or (
                                    is_minor(
                                        student.date_of_birth,
                                        reference_date,
                                    )
                                    and readiness.guardian_status != "ready"
                                )
                            ):
                                raise GuardianInvitationUnavailable(
                                    "guardian invitation is unavailable"
                                )
                            invitation.accepted_by_person_id = guardian_person_id
                            invitation.accepted_at = now
                            invitation.status = "accepted"
                            clear_guardian_invitation_identifier(invitation)
                            self.db.flush()
                            result = GuardianInvitationAcceptance(
                                invitation_id=invitation.id,
                                relationship_id=relationship_id,
                                student_finalized=finalized_now,
                            )
        except GuardianInvitationUnavailable:
            raise
        except (
            IdentityServiceError,
            RegistrationFinalizationError,
            IntegrityError,
            ValueError,
        ) as exc:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            ) from exc
        if conflict_pending:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        if result is None:
            raise GuardianInvitationUnavailable(
                "guardian invitation is unavailable"
            )
        return result
