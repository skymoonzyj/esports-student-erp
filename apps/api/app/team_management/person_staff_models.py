"""Real personnel/team positions, separate from realized account read scopes."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class PersonTeamStaffPosition(Base):
    """Immutable source facts; only version, realization and initial end may change.

    The 0087 database history trigger also preserves the completed end audit.
    Moving teams/roles requires ending this row and appending another position.
    """

    __tablename__ = "person_team_staff_positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["origin_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            ondelete="RESTRICT",
            name="fk_person_team_staff_approval_person",
        ),
        CheckConstraint(
            "staff_role IN ('manager','coach','assistant_coach')", name="ck_person_team_staff_role"
        ),
        CheckConstraint("version >= 1", name="ck_person_team_staff_version"),
        CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL AND end_reason IS NULL) OR (ended_at IS NOT NULL AND ended_by_user_id IS NOT NULL AND ended_at >= started_at AND end_reason IS NOT NULL AND length(btrim(end_reason)) BETWEEN 1 AND 500)",
            name="ck_person_team_staff_end",
        ),
        Index(
            "uq_person_team_staff_current",
            "person_id",
            "team_id",
            "staff_role",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index(
            "uq_person_team_staff_account_assignment",
            "account_assignment_id",
            unique=True,
            postgresql_where=text("account_assignment_id IS NOT NULL"),
        ),
        Index("ix_person_team_staff_person_current", "person_id", "ended_at"),
        Index("ix_person_team_staff_team_current", "team_id", "ended_at"),
        Index("ix_person_team_staff_origin", "origin_approval_id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    team_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("professional_teams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    staff_role: Mapped[str] = mapped_column(String(24), nullable=False)
    origin_approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    end_reason: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    account_assignment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("team_staff_assignments.id", ondelete="RESTRICT")
    )


class PersonTeamStaffAuthorization(Base):
    __tablename__ = "person_team_staff_authorizations"
    __table_args__ = (
        UniqueConstraint(
            "position_id", "version", name="uq_person_team_staff_authorization_version"
        ),
        CheckConstraint("version >= 1", name="ck_person_team_staff_authorization_version"),
        CheckConstraint(
            "authorized_role_key IN ('team_manager','coach','assistant_coach')",
            name="ck_person_team_staff_authorization_role",
        ),
        CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'", name="ck_person_team_staff_authorization_digest"
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    position_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("person_team_staff_positions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    authorizer_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    authorized_role_key: Mapped[str] = mapped_column(String(64), nullable=False)
    role_plan_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("intake_activation_role_plans.id", ondelete="RESTRICT")
    )
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
