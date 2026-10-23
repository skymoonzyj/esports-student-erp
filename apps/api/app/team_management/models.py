from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class TeamOperation(Base):
    __tablename__ = "team_operations"
    __table_args__ = (
        UniqueConstraint(
            "actor_account_id", "idempotency_key", name="uq_team_operations_actor_key"
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_team_operations_fingerprint",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TeamMembership(Base):
    __tablename__ = "team_memberships"
    __table_args__ = (
        CheckConstraint(
            "(left_on IS NULL AND ended_at IS NULL AND ended_by_user_id IS NULL "
            "AND close_operation_id IS NULL AND end_kind IS NULL AND transfer_reason IS NULL) "
            "OR (left_on IS NOT NULL AND ended_at IS NOT NULL AND end_kind IS NOT NULL "
            "AND left_on >= joined_on AND ended_at >= created_at)",
            name="ck_team_memberships_end_pair",
        ),
        CheckConstraint(
            "end_kind IS NULL OR end_kind IN ('left', 'transferred', 'account_deleted')",
            name="ck_team_memberships_end_kind",
        ),
        CheckConstraint(
            "(end_kind = 'transferred' AND transfer_reason IS NOT NULL "
            "AND length(btrim(transfer_reason)) BETWEEN 1 AND 200) "
            "OR (end_kind IS DISTINCT FROM 'transferred' AND transfer_reason IS NULL)",
            name="ck_team_memberships_transfer_reason",
        ),
        Index(
            "uq_team_memberships_open_person",
            "person_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_team_memberships_person_history", "person_id", "joined_on", "created_at", "id"),
        Index("ix_team_memberships_team_history", "team_id", "joined_on", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("professional_teams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    joined_on: Mapped[date] = mapped_column(Date, nullable=False)
    left_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ended_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    open_operation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("team_operations.id", ondelete="RESTRICT")
    )
    close_operation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("team_operations.id", ondelete="RESTRICT")
    )
    end_kind: Mapped[str | None] = mapped_column(String(24))
    transfer_reason: Mapped[str | None] = mapped_column(String(200))


class TeamManagerAssignment(Base):
    __tablename__ = "team_manager_assignments"
    __table_args__ = (
        CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL "
            "AND close_operation_id IS NULL AND end_kind IS NULL) "
            "OR (ended_at IS NOT NULL AND end_kind IS NOT NULL AND ended_at >= created_at)",
            name="ck_team_manager_assignments_end_pair",
        ),
        CheckConstraint(
            "end_kind IS NULL OR end_kind IN ('unassigned', 'role_removed', 'account_deleted')",
            name="ck_team_manager_assignments_end_kind",
        ),
        Index(
            "uq_team_manager_assignments_open_account_team",
            "account_id",
            "team_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_team_manager_assignments_account_history", "account_id", "created_at", "id"),
        Index("ix_team_manager_assignments_team_history", "team_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("professional_teams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ended_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    open_operation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("team_operations.id", ondelete="RESTRICT")
    )
    close_operation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("team_operations.id", ondelete="RESTRICT")
    )
    end_kind: Mapped[str | None] = mapped_column(String(24))
