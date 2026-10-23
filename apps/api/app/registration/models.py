from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin

RegistrationPurpose = Literal["student", "guardian", "staff_applicant", "visitor"]
RegistrationState = Literal[
    "draft",
    "contact_verified",
    "identity_completed",
    "pending_guardian",
    "ready_to_finalize",
    "finalized",
    "conflict",
    "rejected",
    "expired",
]
RegistrationFinalizationMatchKind = Literal[
    "new_person",
    "claim_existing",
    "existing_account",
]
RegistrationReviewDecision = Literal["approved", "rejected"]
IdentifierKind = Literal["phone", "email"]
ChallengePurpose = Literal[
    "registration",
    "password_reset",
    "guardian_invitation",
    "add_login_identifier",
    "assisted_activation",
    "profile_claim",
]

_ACTIVE_APPLICATION_STATES = (
    "'draft', 'contact_verified', 'identity_completed', 'pending_guardian', "
    "'ready_to_finalize', 'conflict'"
)
_VERIFIED_IDENTIFIER_STATES = (
    "'contact_verified', 'identity_completed', 'pending_guardian', "
    "'ready_to_finalize', 'conflict'"
)


class RegistrationApplication(TimestampMixin, Base):
    __tablename__ = "registration_applications"
    __table_args__ = (
        CheckConstraint(
            "primary_purpose IN "
            "('student', 'guardian', 'staff_applicant', 'visitor')",
            name="ck_registration_applications_primary_purpose",
        ),
        CheckConstraint(
            "state IN ('draft', 'contact_verified', 'identity_completed', "
            "'pending_guardian', 'ready_to_finalize', 'finalized', 'conflict', "
            "'rejected', 'expired')",
            name="ck_registration_applications_state",
        ),
        CheckConstraint(
            "verified_identifier_kind IS NULL OR "
            "verified_identifier_kind IN ('phone', 'email')",
            name="ck_registration_applications_identifier_kind",
        ),
        CheckConstraint(
            "sex IS NULL OR sex IN ('male', 'female')",
            name="ck_registration_applications_sex",
        ),
        CheckConstraint(
            "requested_staff_type IS NULL OR requested_staff_type IN "
            "('coach', 'life_teacher', 'customer_service', 'administration', 'other')",
            name="ck_registration_applications_requested_staff_type",
        ),
        CheckConstraint(
            "staff_application_reason IS NULL OR "
            "(regexp_replace(staff_application_reason, "
            "'^[[:space:]]+|[[:space:]]+$', '', 'g') = staff_application_reason AND "
            "regexp_replace(staff_application_reason, '[[:space:]]', '', 'g') <> '' AND "
            "char_length(staff_application_reason) BETWEEN 2 AND 500)",
            name="ck_registration_applications_staff_application_reason",
        ),
        CheckConstraint(
            "visit_reason IS NULL OR "
            "(regexp_replace(visit_reason, "
            "'^[[:space:]]+|[[:space:]]+$', '', 'g') = visit_reason AND "
            "regexp_replace(visit_reason, '[[:space:]]', '', 'g') <> '' AND "
            "char_length(visit_reason) BETWEEN 2 AND 200)",
            name="ck_registration_applications_visit_reason",
        ),
        CheckConstraint(
            "(requested_staff_type IS NULL AND staff_application_reason IS NULL "
            "AND visit_reason IS NULL AND "
            "(primary_purpose IN ('student', 'guardian') OR "
            "state IN ('draft', 'contact_verified', 'finalized', 'rejected', "
            "'expired'))) OR "
            "(primary_purpose = 'staff_applicant' AND "
            "requested_staff_type IS NOT NULL AND "
            "staff_application_reason IS NOT NULL AND visit_reason IS NULL) OR "
            "(primary_purpose = 'visitor' AND requested_staff_type IS NULL AND "
            "staff_application_reason IS NULL AND visit_reason IS NOT NULL)",
            name="ck_registration_applications_purpose_fields",
        ),
        CheckConstraint(
            "(verified_identifier_digest IS NULL) = "
            "(verified_identifier_kind IS NULL)",
            name="ck_registration_applications_identifier_pair",
        ),
        CheckConstraint(
            "finalized_at IS NULL OR state = 'finalized'",
            name="ck_registration_applications_finalized_at",
        ),
        CheckConstraint(
            "(state = 'finalized' AND finalization_match_kind IS NOT NULL AND "
            "finalization_match_kind IN "
            "('new_person', 'claim_existing', 'existing_account')) OR "
            "(state <> 'finalized' AND finalization_match_kind IS NULL)",
            name="ck_registration_applications_finalization_match_kind",
        ),
        CheckConstraint(
            "rejected_at IS NULL OR state = 'rejected'",
            name="ck_registration_applications_rejected_at",
        ),
        CheckConstraint(
            "review_reason IS NULL OR ("
            "regexp_replace(review_reason, "
            "'^[[:space:]]+|[[:space:]]+$', '', 'g') = review_reason AND "
            "regexp_replace(review_reason, '[[:space:]]', '', 'g') <> '' AND "
            "char_length(review_reason) BETWEEN 2 AND 500)",
            name="ck_registration_applications_review_reason",
        ),
        CheckConstraint(
            "(review_decision IS NULL AND reviewed_by_user_id IS NULL AND "
            "review_reason IS NULL AND reviewed_at IS NULL) OR "
            "(review_decision IS NOT NULL AND reviewed_by_user_id IS NOT NULL "
            "AND review_reason IS NOT NULL AND reviewed_at IS NOT NULL AND ("
            "(review_decision = 'approved' AND "
            "state IN ('pending_guardian', 'ready_to_finalize', 'finalized', "
            "'conflict')) OR "
            "(review_decision = 'rejected' AND state = 'rejected')))",
            name="ck_registration_applications_review_state",
        ),
        CheckConstraint(
            "(reviewed_candidate_person_id IS NULL AND ("
            "state <> 'pending_guardian' OR "
            "review_decision IS DISTINCT FROM 'approved')) OR "
            "(reviewed_candidate_person_id IS NOT NULL AND "
            "review_decision IS NOT DISTINCT FROM 'approved' AND "
            "state IN ('pending_guardian', 'ready_to_finalize'))",
            name="ck_registration_applications_review_candidate",
        ),
        Index(
            "uq_registration_applications_active_identifier",
            "verified_identifier_digest",
            unique=True,
            postgresql_where=text(
                "verified_identifier_digest IS NOT NULL AND state IN ("
                f"{_VERIFIED_IDENTIFIER_STATES})"
            ),
        ),
        Index(
            "uq_registration_applications_active_identity",
            "identity_digest",
            unique=True,
            postgresql_where=text(
                "identity_digest IS NOT NULL AND state IN ("
                f"{_ACTIVE_APPLICATION_STATES})"
            ),
        ),
        Index(
            "ix_registration_applications_state_expiry",
            "state",
            "expires_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    primary_purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    verified_identifier_kind: Mapped[str | None] = mapped_column(String(16))
    verified_identifier_digest: Mapped[str | None] = mapped_column(String(64))
    verified_identifier_ciphertext: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(String(80))
    identity_ciphertext: Mapped[str | None] = mapped_column(Text)
    identity_digest: Mapped[str | None] = mapped_column(String(64))
    sex: Mapped[str | None] = mapped_column(String(16))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    requested_staff_type: Mapped[str | None] = mapped_column(String(24))
    staff_application_reason: Mapped[str | None] = mapped_column(String(500))
    visit_reason: Mapped[str | None] = mapped_column(String(200))
    referral_code_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("referral_codes.id", ondelete="RESTRICT"),
    )
    matched_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    finalized_account_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    finalization_match_kind: Mapped[str | None] = mapped_column(String(24))
    conflict_code: Mapped[str | None] = mapped_column(String(64))
    review_decision: Mapped[str | None] = mapped_column(String(16))
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    reviewed_candidate_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    review_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RegistrationChannelSettings(TimestampMixin, Base):
    """Administrator-controlled public self-registration channels.

    There is intentionally no row-level purpose or contact data here.  The
    singleton keeps the public registration policy separate from deployment
    secrets while allowing an administrator to change it without a restart.
    """

    __tablename__ = "registration_channel_settings"
    __table_args__ = (
        UniqueConstraint(
            "singleton_key",
            name="uq_registration_channel_settings_singleton",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    singleton_key: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="default",
        server_default="default",
    )
    phone_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    email_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class RegistrationVerificationChallenge(TimestampMixin, Base):
    __tablename__ = "registration_verification_challenges"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('registration', 'password_reset', "
            "'guardian_invitation', 'add_login_identifier', 'assisted_activation', 'profile_claim')",
            name="ck_registration_verification_challenges_purpose",
        ),
        CheckConstraint(
            "identifier_kind IN ('phone', 'email')",
            name="ck_registration_verification_challenges_identifier_kind",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 5",
            name="ck_registration_verification_challenges_attempts",
        ),
        Index(
            "ix_registration_verification_challenges_identifier",
            "purpose",
            "identifier_digest",
            "created_at",
            "id",
        ),
        Index(
            "ix_registration_verification_challenges_source",
            "request_source_digest",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    application_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("registration_applications.id", ondelete="CASCADE"),
    )
    account_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    identifier_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    identifier_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RegistrationSession(TimestampMixin, Base):
    __tablename__ = "registration_sessions"
    __table_args__ = (
        UniqueConstraint(
            "token_digest",
            name="uq_registration_sessions_token_digest",
        ),
        Index(
            "ix_registration_sessions_application_active",
            "application_id",
            "revoked_at",
            "expires_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    application_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("registration_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GuardianRegistrationInvitation(TimestampMixin, Base):
    __tablename__ = "guardian_registration_invitations"
    __table_args__ = (
        CheckConstraint(
            "(application_id IS NULL) <> (student_person_id IS NULL)",
            name="ck_guardian_registration_invitations_target",
        ),
        CheckConstraint(
            "invited_identifier_kind IN ('phone', 'email')",
            name="ck_guardian_registration_invitations_identifier_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name="ck_guardian_registration_invitations_status",
        ),
        CheckConstraint(
            "status <> 'accepted' OR "
            "(accepted_by_person_id IS NOT NULL AND accepted_at IS NOT NULL)",
            name="ck_guardian_registration_invitations_acceptance",
        ),
        CheckConstraint(
            "(status = 'pending' AND handoff_token_digest IS NOT NULL) OR "
            "(status <> 'pending' AND handoff_token_digest IS NULL)",
            name="ck_guardian_registration_invitations_handoff_token",
        ),
        Index(
            "uq_guardian_registration_invitations_pending_application",
            "application_id",
            "invited_identifier_digest",
            unique=True,
            postgresql_where=text("status = 'pending' AND application_id IS NOT NULL"),
        ),
        Index(
            "uq_guardian_registration_invitations_pending_student",
            "student_person_id",
            "invited_identifier_digest",
            unique=True,
            postgresql_where=text("status = 'pending' AND student_person_id IS NOT NULL"),
        ),
        Index(
            "uq_guardian_registration_invitations_accepted_application",
            "application_id",
            "accepted_by_person_id",
            unique=True,
            postgresql_where=text("status = 'accepted' AND application_id IS NOT NULL"),
        ),
        Index(
            "uq_guardian_registration_invitations_accepted_student",
            "student_person_id",
            "accepted_by_person_id",
            unique=True,
            postgresql_where=text("status = 'accepted' AND student_person_id IS NOT NULL"),
        ),
        Index(
            "ix_guardian_registration_invitations_expiry",
            "status",
            "expires_at",
            "id",
        ),
        Index(
            "uq_guardian_registration_invitations_handoff_token",
            "handoff_token_digest",
            unique=True,
            postgresql_where=text("handoff_token_digest IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    application_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("registration_applications.id", ondelete="CASCADE"),
    )
    student_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    invited_identifier_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    invited_identifier_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    invited_identifier_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    handoff_token_digest: Mapped[str | None] = mapped_column(String(64))
    relationship: Mapped[str] = mapped_column(String(40), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StaffIdentityApplication(TimestampMixin, Base):
    __tablename__ = "staff_identity_applications"
    __table_args__ = (
        CheckConstraint(
            "requested_staff_type IN ('coach', 'life_teacher', "
            "'customer_service', 'administration', 'other')",
            name="ck_staff_identity_applications_requested_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name="ck_staff_identity_applications_status",
        ),
        CheckConstraint(
            "regexp_replace(application_reason, "
            "'^[[:space:]]+|[[:space:]]+$', '', 'g') = application_reason AND "
            "regexp_replace(application_reason, '[[:space:]]', '', 'g') <> '' AND "
            "char_length(application_reason) BETWEEN 2 AND 500",
            name="ck_staff_identity_applications_reason",
        ),
        CheckConstraint(
            "review_reason IS NULL OR ("
            "regexp_replace(review_reason, "
            "'^[[:space:]]+|[[:space:]]+$', '', 'g') = review_reason AND "
            "regexp_replace(review_reason, '[[:space:]]', '', 'g') <> '' AND "
            "char_length(review_reason) BETWEEN 2 AND 500)",
            name="ck_staff_identity_applications_review_reason",
        ),
        CheckConstraint(
            "(status = 'approved' AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL) OR "
            "(status = 'rejected' AND reviewed_by_user_id IS NOT NULL "
            "AND review_reason IS NOT NULL AND reviewed_at IS NOT NULL) OR "
            "(status IN ('pending', 'withdrawn') AND reviewed_by_user_id IS NULL "
            "AND review_reason IS NULL AND reviewed_at IS NULL)",
            name="ck_staff_identity_applications_review_state",
        ),
        Index(
            "uq_staff_identity_applications_open_person_type",
            "person_id",
            "requested_staff_type",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_staff_identity_applications_review_queue",
            "status",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requested_staff_type: Mapped[str] = mapped_column(String(32), nullable=False)
    application_reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    review_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RegistrationConsentAcceptance(Base):
    __tablename__ = "registration_consent_acceptances"
    __table_args__ = (
        CheckConstraint(
            "(registration_application_id IS NULL) <> (account_id IS NULL)",
            name="ck_registration_consent_acceptances_subject",
        ),
        CheckConstraint(
            "btrim(consent_type) <> '' AND btrim(policy_version) <> ''",
            name="ck_registration_consent_acceptances_labels",
        ),
        Index(
            "ix_registration_consent_acceptances_application",
            "registration_application_id",
            "accepted_at",
            "id",
        ),
        Index(
            "ix_registration_consent_acceptances_account",
            "account_id",
            "accepted_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    registration_application_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("registration_applications.id", ondelete="RESTRICT"),
    )
    account_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    consent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
