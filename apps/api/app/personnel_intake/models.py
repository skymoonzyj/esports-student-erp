from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin
from app.personnel_intake.claims import models as profile_claim_models  # noqa: F401


class PersonnelIntakeRequest(TimestampMixin, Base):
    __tablename__ = "personnel_intake_requests"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'returned', 'approved', 'withdrawn')",
            name="ck_personnel_intake_requests_state",
        ),
        CheckConstraint("version >= 1", name="ck_personnel_intake_requests_version"),
        CheckConstraint(
            "kind IN ('student', 'player', 'both', 'staff')", name="ck_personnel_intake_requests_kind"
        ),
        Index(
            "ix_personnel_intake_requests_state_submitter", "state", "submitted_by_user_id", "id"
        ),
        Index("ix_personnel_intake_requests_submitter", "submitted_by_user_id", "created_at", "id"),
        Index("ix_personnel_intake_requests_target_team", "target_team_id", "state", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    submitted_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    person_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT")
    )
    review_reason: Mapped[str | None] = mapped_column(String(500))
    target_coach_person_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    target_team_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("professional_teams.id", ondelete="RESTRICT")
    )


class PersonnelIntakeRevision(Base):
    __tablename__ = "personnel_intake_revisions"
    __table_args__ = (
        UniqueConstraint("request_id", "version", name="uq_personnel_intake_revision_version"),
        CheckConstraint("version >= 1", name="ck_personnel_intake_revisions_version"),
        CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'", name="ck_personnel_intake_revisions_digest"
        ),
        Index("ix_personnel_intake_revisions_email", "email_digest", "request_id", "version"),
        Index("ix_personnel_intake_revisions_identity", "identity_digest", "request_id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("personnel_intake_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    email_digest: Mapped[str | None] = mapped_column(String(64))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PersonnelCredentialReservation(Base):
    __tablename__ = "personnel_credential_reservations"
    __table_args__ = (
        CheckConstraint("kind IN ('identity', 'email')", name="ck_personnel_reservations_kind"),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_personnel_reservations_digest"),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= created_at", name="ck_personnel_reservations_end"
        ),
        Index(
            "uq_personnel_reservations_active",
            "kind",
            "digest",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_personnel_reservations_request", "request_id", "ended_at"),
        Index("ix_personnel_reservations_person", "person_id", "ended_at"),
        Index("ix_personnel_reservations_claim", "profile_claim_id", "ended_at"),
        CheckConstraint(
            "(request_id IS NOT NULL AND profile_claim_id IS NULL) OR "
            "(request_id IS NULL AND profile_claim_id IS NOT NULL AND kind='email' AND person_id IS NULL)",
            name="ck_personnel_reservations_owner",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("personnel_intake_requests.id", ondelete="RESTRICT"),
        nullable=True,
    )
    profile_claim_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("profile_claims.id", ondelete="RESTRICT")
    )
    person_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssistedApproval(Base):
    """Immutable approval; encrypted snapshots freeze all identity/contact/date facts.

    Guardian IDs identify concrete rows. Their snapshot digest and encrypted facts,
    not the ordinal alone, must be compared when deciding later activation policy.
    """

    __tablename__ = "assisted_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id", "revision_version"],
            ["personnel_intake_revisions.request_id", "personnel_intake_revisions.version"],
            name="fk_assisted_approval_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            name="fk_assisted_approval_supersedes_person",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "person_id", name="uq_assisted_approval_person"),
        UniqueConstraint(
            "id", "person_id", "revision_version", name="uq_assisted_approval_person_version"
        ),
        UniqueConstraint("request_id", "revision_version", name="uq_assisted_approval_revision"),
        UniqueConstraint(
            "approved_by_user_id", "operation_key", name="uq_assisted_approval_operation"
        ),
        CheckConstraint(
            "(operation_key IS NULL AND operation_digest IS NULL AND operation_kind IS NULL) OR "
            "(operation_key IS NOT NULL AND operation_digest IS NOT NULL AND "
            "operation_digest ~ '^[0-9a-f]{64}$' AND operation_kind IS NOT NULL AND "
            "operation_kind IN ('direct_create', 'correction', 'claim_bind', 'claim_revoke', 'claim_complete'))",
            name="ck_assisted_approval_operation",
        ),
        CheckConstraint(
            "revision_version >= 1 AND guardian_relationship_version >= 1",
            name="ck_assisted_approval_versions",
        ),
        CheckConstraint(
            "snapshot_digest ~ '^[0-9a-f]{64}$' AND guardian_snapshot_digest ~ '^[0-9a-f]{64}$'",
            name="ck_assisted_approval_digests",
        ),
        CheckConstraint(
            "supersedes_approval_id IS NULL OR supersedes_approval_id <> id",
            name="ck_assisted_approval_not_self",
        ),
        Index(
            "uq_assisted_approval_initial",
            "request_id",
            unique=True,
            postgresql_where=text("supersedes_approval_id IS NULL"),
        ),
        Index(
            "uq_assisted_approval_successor",
            "supersedes_approval_id",
            unique=True,
            postgresql_where=text("supersedes_approval_id IS NOT NULL"),
        ),
        Index("ix_assisted_approvals_person", "person_id", "approved_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    snapshot_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    guardian_relationship_version: Mapped[int] = mapped_column(Integer, nullable=False)
    guardian_relationship_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    guardian_snapshot_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    guardian_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_approval_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    operation_key: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    operation_digest: Mapped[str | None] = mapped_column(String(64))
    operation_kind: Mapped[str | None] = mapped_column(String(24))


class AssistedPersonProfile(TimestampMixin, Base):
    __tablename__ = "assisted_person_profiles"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_assisted_profile_version"),
        CheckConstraint(
            "activation_requirement IN ('reserved_email','reviewed_claim')",
            name="ck_assisted_profile_activation_requirement",
        ),
        ForeignKeyConstraint(
            ["current_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            name="fk_assisted_profile_approval_person",
            ondelete="RESTRICT",
        ),
    )
    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), primary_key=True
    )
    current_approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    activation_requirement: Mapped[Literal["reserved_email", "reviewed_claim"]] = mapped_column(
        String(24), nullable=False, default="reserved_email", server_default="reserved_email"
    )


class CoachPersonResponsibility(Base):
    __tablename__ = "coach_person_responsibilities"
    __table_args__ = (
        CheckConstraint("coach_person_id <> person_id", name="ck_coach_person_distinct"),
        CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_by_user_id IS NOT NULL "
            "AND ended_at >= created_at)",
            name="ck_coach_person_end",
        ),
        Index(
            "uq_coach_person_active",
            "coach_person_id",
            "person_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index(
            "ix_coach_person_active_person",
            "person_id",
            "coach_person_id",
            postgresql_where=text("ended_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    coach_person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
