from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, exists, or_, select, update
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.registration.finalization_service import (
    clear_registration_application_secrets,
)
from app.registration.invitation_retention import (
    REDACTED_GUARDIAN_INVITATION_CIPHERTEXT,
    REDACTED_GUARDIAN_INVITATION_DIGEST,
    clear_guardian_invitation_identifier,
)
from app.registration.models import (
    GuardianRegistrationInvitation,
    RegistrationApplication,
    RegistrationSession,
)

_ACTIVE_APPLICATION_STATES = frozenset(
    {
        "draft",
        "contact_verified",
        "identity_completed",
        "pending_guardian",
        "ready_to_finalize",
        "conflict",
    }
)
_TERMINAL_APPLICATION_STATES = frozenset({"finalized", "rejected", "expired"})
_TERMINAL_INVITATION_STATES = frozenset({"accepted", "revoked", "expired"})
_DRAFT_APPLICATION_TTL = timedelta(minutes=10)
_APPLICATION_INACTIVITY_TTL = timedelta(days=30)
_TEMPORARY_APPLICATION_FIELDS = (
    RegistrationApplication.verified_identifier_kind,
    RegistrationApplication.verified_identifier_digest,
    RegistrationApplication.verified_identifier_ciphertext,
    RegistrationApplication.password_hash,
    RegistrationApplication.full_name,
    RegistrationApplication.identity_ciphertext,
    RegistrationApplication.identity_digest,
    RegistrationApplication.sex,
    RegistrationApplication.date_of_birth,
    RegistrationApplication.requested_staff_type,
    RegistrationApplication.staff_application_reason,
    RegistrationApplication.visit_reason,
)


def touch_registration_application(
    application: RegistrationApplication,
    *,
    now: datetime,
) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("registration application activity time must be timezone-aware")
    if application.state == "draft":
        application.expires_at = registration_application_deadline(application)
        return
    application.expires_at = now + _APPLICATION_INACTIVITY_TTL


def registration_application_deadline(
    application: RegistrationApplication,
) -> datetime:
    deadline = application.expires_at
    created_at = application.created_at
    if application.state == "draft" and created_at is not None:
        deadline = min(deadline, created_at + _DRAFT_APPLICATION_TTL)
    return deadline


def _active_application_expired_at(now: datetime) -> ColumnElement[bool]:
    return or_(
        (
            (RegistrationApplication.state == "draft")
            & or_(
                RegistrationApplication.expires_at <= now,
                RegistrationApplication.created_at <= now - _DRAFT_APPLICATION_TTL,
            )
        ),
        (
            RegistrationApplication.state.in_(
                _ACTIVE_APPLICATION_STATES - {"draft"}
            )
            & (RegistrationApplication.expires_at <= now)
        ),
    )


class RegistrationCleanupService:
    def __init__(
        self,
        db: Session,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registration cleanup clock must be timezone-aware")
        return value

    @staticmethod
    def _clear_review_metadata(application: RegistrationApplication) -> None:
        application.review_decision = None
        application.reviewed_by_user_id = None
        application.reviewed_candidate_person_id = None
        application.review_reason = None
        application.reviewed_at = None

    @classmethod
    def expire_application(cls, application: RegistrationApplication) -> None:
        application.state = "expired"
        cls._clear_review_metadata(application)
        clear_registration_application_secrets(application)

    def prune(self, *, limit: int) -> int:
        if isinstance(limit, bool) or limit <= 0:
            raise ValueError("registration cleanup limit must be positive")
        now = self._now()
        changed = 0

        with write_transaction(self.db):
            temporary_payload_remains = or_(
                *(column.is_not(None) for column in _TEMPORARY_APPLICATION_FIELDS)
            )
            review_metadata_remains = or_(
                RegistrationApplication.review_decision.is_not(None),
                RegistrationApplication.reviewed_by_user_id.is_not(None),
                RegistrationApplication.reviewed_candidate_person_id.is_not(None),
                RegistrationApplication.review_reason.is_not(None),
                RegistrationApplication.reviewed_at.is_not(None),
            )
            unrevoked_session_exists = exists(
                select(RegistrationSession.id).where(
                    RegistrationSession.application_id
                    == RegistrationApplication.id,
                    RegistrationSession.revoked_at.is_(None),
                )
            )
            applications = list(
                self.db.scalars(
                    select(RegistrationApplication)
                    .where(
                        or_(
                            _active_application_expired_at(now),
                            (
                                RegistrationApplication.state.in_(
                                    _TERMINAL_APPLICATION_STATES
                                )
                                & or_(
                                    temporary_payload_remains,
                                    review_metadata_remains,
                                )
                            ),
                            (
                                RegistrationApplication.state.in_(
                                    _TERMINAL_APPLICATION_STATES
                                )
                                & unrevoked_session_exists
                            ),
                        )
                    )
                    .order_by(
                        RegistrationApplication.expires_at,
                        RegistrationApplication.id,
                    )
                    .limit(limit)
                    .with_for_update(
                        of=RegistrationApplication,
                        skip_locked=True,
                    )
                    .execution_options(populate_existing=True)
                )
            )

            for application in applications:
                if (
                    application.state in _ACTIVE_APPLICATION_STATES
                    and registration_application_deadline(application) <= now
                ):
                    self.expire_application(application)
                else:
                    self._clear_review_metadata(application)
                    clear_registration_application_secrets(application)

            application_ids = [application.id for application in applications]
            if application_ids:
                self.db.execute(
                    update(RegistrationSession)
                    .where(
                        RegistrationSession.application_id.in_(application_ids),
                        RegistrationSession.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                    .execution_options(synchronize_session=False)
                )
            changed = len(applications)
            remaining = limit - changed
            if remaining:
                expired_application_exists = exists(
                    select(RegistrationApplication.id).where(
                        RegistrationApplication.id
                        == GuardianRegistrationInvitation.application_id,
                        RegistrationApplication.state == "expired",
                    )
                )
                terminal_payload_remains = or_(
                    GuardianRegistrationInvitation.invited_identifier_digest
                    != REDACTED_GUARDIAN_INVITATION_DIGEST,
                    GuardianRegistrationInvitation.invited_identifier_ciphertext
                    != REDACTED_GUARDIAN_INVITATION_CIPHERTEXT,
                )
                invitations = list(
                    self.db.scalars(
                        select(GuardianRegistrationInvitation)
                        .where(
                            or_(
                                (
                                    GuardianRegistrationInvitation.status
                                    == "pending"
                                )
                                & (
                                    (
                                        GuardianRegistrationInvitation.expires_at
                                        <= now
                                    )
                                    | expired_application_exists
                                ),
                                (
                                    GuardianRegistrationInvitation.status.in_(
                                        _TERMINAL_INVITATION_STATES
                                    )
                                )
                                & terminal_payload_remains,
                            )
                        )
                        .order_by(
                            GuardianRegistrationInvitation.expires_at,
                            GuardianRegistrationInvitation.id,
                        )
                        .limit(remaining)
                        .with_for_update(
                            of=GuardianRegistrationInvitation,
                            skip_locked=True,
                        )
                        .execution_options(populate_existing=True)
                    )
                )
                for invitation in invitations:
                    if invitation.status == "pending":
                        invitation.status = "expired"
                    clear_guardian_invitation_identifier(invitation)
                changed += len(invitations)

            self.db.flush()
        return changed

    def prune_identifier(self, *, identifier_digest: str) -> int:
        now = self._now()
        with write_transaction(self.db):
            application = self.db.scalar(
                select(RegistrationApplication)
                .where(
                    RegistrationApplication.verified_identifier_digest
                    == identifier_digest,
                    RegistrationApplication.state.in_(_ACTIVE_APPLICATION_STATES),
                    _active_application_expired_at(now),
                )
                .order_by(
                    RegistrationApplication.expires_at,
                    RegistrationApplication.id,
                )
                .limit(1)
                .with_for_update(
                    of=RegistrationApplication,
                    skip_locked=True,
                )
                .execution_options(populate_existing=True)
            )
            if application is None:
                return 0
            self.expire_application(application)
            self.db.execute(
                update(RegistrationSession)
                .where(
                    RegistrationSession.application_id == application.id,
                    RegistrationSession.revoked_at.is_(None),
                )
                .values(revoked_at=now)
                .execution_options(synchronize_session=False)
            )
            self.db.flush()
        return 1
