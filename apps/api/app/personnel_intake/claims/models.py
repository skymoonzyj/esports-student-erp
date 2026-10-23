from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class ProfileClaimSession(TimestampMixin, Base):
    __tablename__ = "profile_claim_sessions"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_profile_claim_session_token"),
        UniqueConstraint("challenge_id", name="uq_profile_claim_session_challenge"),
        UniqueConstraint(
            "id", "challenge_id", "email_digest", name="uq_profile_claim_session_evidence"
        ),
        ForeignKeyConstraint(
            ["claim_id", "payload_digest", "email_digest"],
            ["profile_claims.id", "profile_claims.payload_digest", "profile_claims.email_digest"],
            name="fk_profile_claim_session_claim",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint("state IN ('requested','verified')", name="ck_profile_claim_session_state"),
        CheckConstraint(
            "submission_version IN (1,2)",
            name="ck_profile_claim_session_submission_version",
        ),
        CheckConstraint(
            "token_digest ~ '^[0-9a-f]{64}$' AND payload_digest ~ '^[0-9a-f]{64}$' AND identity_digest ~ '^[0-9a-f]{64}$' AND email_digest ~ '^[0-9a-f]{64}$'",
            name="ck_profile_claim_session_digests",
        ),
        CheckConstraint("expires_at > created_at", name="ck_profile_claim_session_expiry"),
        CheckConstraint(
            "(state='requested' AND verified_at IS NULL AND claim_id IS NULL) OR (state='verified' AND verified_at IS NOT NULL AND verified_at < expires_at)",
            name="ck_profile_claim_session_verified",
        ),
        Index("ix_profile_claim_session_expiry", "expires_at", "id"),
        Index("ix_profile_claim_session_identity", "identity_digest", "created_at", "id"),
        Index("ix_profile_claim_session_email", "email_digest", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    challenge_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("registration_verification_challenges.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payload_ciphertext: Mapped[str | None] = mapped_column(Text)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    submission_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    email_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class ProfileClaim(TimestampMixin, Base):
    __tablename__ = "profile_claims"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_profile_claim_session"),
        UniqueConstraint("challenge_id", name="uq_profile_claim_challenge"),
        UniqueConstraint(
            "id", "payload_digest", "email_digest", name="uq_profile_claim_access_input"
        ),
        UniqueConstraint(
            "id", "target_person_id", "email_digest", name="uq_profile_claim_target_email"
        ),
        ForeignKeyConstraint(
            ["session_id", "challenge_id", "email_digest"],
            [
                "profile_claim_sessions.id",
                "profile_claim_sessions.challenge_id",
                "profile_claim_sessions.email_digest",
            ],
            name="fk_profile_claim_session_evidence",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_approval_id", "target_person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            name="fk_profile_claim_target_approval",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["completed_account_id", "target_person_id"],
            ["user_accounts.id", "user_accounts.person_id"],
            name="fk_profile_claim_completed_account_person",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "target_approval_id IS NULL OR target_person_id IS NOT NULL",
            name="ck_profile_claim_target",
        ),
        CheckConstraint(
            "status IN ('pending','rejected','expired','invalidated','approved','revoked','completed')",
            name="ck_profile_claim_status",
        ),
        CheckConstraint(
            "version >= 1 AND submission_version IN (1,2)", name="ck_profile_claim_versions"
        ),
        CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$' AND identity_digest ~ '^[0-9a-f]{64}$' AND email_digest ~ '^[0-9a-f]{64}$'",
            name="ck_profile_claim_digests",
        ),
        CheckConstraint(
            "expires_at = verified_at + interval '7 days' AND challenge_consumed_at = verified_at",
            name="ck_profile_claim_evidence_times",
        ),
        CheckConstraint(
            "(completed_account_id IS NULL AND completed_at IS NULL AND status <> 'completed') OR (completed_account_id IS NOT NULL AND target_person_id IS NOT NULL AND completed_at IS NOT NULL AND status='completed')",
            name="ck_profile_claim_completion",
        ),
        Index(
            "ix_profile_claim_pending_expiry",
            "expires_at",
            "id",
            postgresql_where=text("status='pending'"),
        ),
        Index("ix_profile_claim_status_page", "status", "created_at", "id"),
        Index("ix_profile_claim_email", "email_digest", "status", "id"),
        Index("ix_profile_claim_identity", "identity_digest", "status", "id"),
        Index("ix_profile_claim_target", "target_person_id", "status", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    challenge_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    submission_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    email_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    challenge_consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_person_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT")
    )
    target_approval_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    private_match_reason: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    public_reason: Mapped[str | None] = mapped_column(String(500))
    completed_account_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProfileClaimBinding(Base):
    __tablename__ = "profile_claim_bindings"
    __table_args__ = (
        CheckConstraint("email_binding_origin IN ('introduced','preserved')", name="ck_profile_claim_binding_email_origin"),
        UniqueConstraint("claim_id", name="uq_profile_claim_binding_claim"),
        ForeignKeyConstraint(
            ["claim_id", "person_id", "email_digest"],
            ["profile_claims.id", "profile_claims.target_person_id", "profile_claims.email_digest"],
            name="fk_profile_claim_binding_claim",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            name="fk_profile_claim_binding_approval",
            ondelete="RESTRICT",
        ),
        CheckConstraint("email_digest ~ '^[0-9a-f]{64}$'", name="ck_profile_claim_binding_digest"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_profile_claim_binding_revoked",
        ),
        Index(
            "uq_profile_claim_binding_active_person",
            "person_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_profile_claim_binding_email", "email_digest", "revoked_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    email_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    email_binding_origin: Mapped[str] = mapped_column(String(16), nullable=False, default="introduced", server_default="introduced")
    approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProfileClaimDecision(Base):
    __tablename__ = "profile_claim_decisions"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id", "idempotency_key", name="uq_profile_claim_decision_actor_key"
        ),
        CheckConstraint(
            "action IN ('approve','reject','revoke')", name="ck_profile_claim_decision_action"
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_profile_claim_decision_fingerprint"
        ),
        CheckConstraint(
            "before_version >= 1 AND after_version = before_version + 1",
            name="ck_profile_claim_decision_versions",
        ),
        Index("ix_profile_claim_decision_history", "claim_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("profile_claims.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    before_version: Mapped[int] = mapped_column(Integer, nullable=False)
    after_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_approval_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assisted_approvals.id", ondelete="RESTRICT")
    )
    result_binding_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("profile_claim_bindings.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
