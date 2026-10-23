from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin

_INITIAL_PROJECT_UUIDS = (
    "30000000-0000-4000-8000-000000000001",
    "30000000-0000-4000-8000-000000000002",
    "30000000-0000-4000-8000-000000000003",
    "30000000-0000-4000-8000-000000000004",
)
_PROJECT_CREATOR_CHECK = (
    "created_by_user_id IS NOT NULL OR id IN ("
    + ", ".join(f"'{project_id}'::uuid" for project_id in _INITIAL_PROJECT_UUIDS)
    + ")"
)
_COHORT_STATUS_CHECK = "status IN ('draft', 'open', 'upcoming', 'active', 'completed', 'archived')"


class GameProject(TimestampMixin, Base):
    __tablename__ = "game_projects"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_game_projects_sort_order"),
        CheckConstraint(_PROJECT_CREATOR_CHECK, name="ck_game_projects_creator"),
        UniqueConstraint("name", name="uq_game_projects_name"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class ProfessionalTeam(TimestampMixin, Base):
    __tablename__ = "professional_teams"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "name",
            name="uq_professional_teams_project_name",
        ),
        UniqueConstraint(
            "id",
            "project_id",
            name="uq_professional_teams_id_project",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class Cohort(TimestampMixin, Base):
    __tablename__ = "cohorts"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_cohorts_dates"),
        CheckConstraint(
            "default_check_out_at >= default_check_in_at",
            name="ck_cohorts_default_check_times",
        ),
        CheckConstraint(_COHORT_STATUS_CHECK, name="ck_cohorts_status"),
        Index("ix_cohorts_portal_next", "status", "starts_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    default_check_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    default_check_out_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="draft",
        server_default="draft",
    )
    auto_start_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    auto_complete_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class CohortStatusEvent(Base):
    __tablename__ = "cohort_status_events"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('manual', 'scheduler', 'request_reconciliation')",
            name="ck_cohort_status_events_trigger",
        ),
        CheckConstraint(
            "(trigger = 'manual' AND actor_user_id IS NOT NULL) OR "
            "(trigger IN ('scheduler', 'request_reconciliation') "
            "AND actor_user_id IS NULL)",
            name="ck_cohort_status_events_actor",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    cohort_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    old_status: Mapped[str] = mapped_column(String(16), nullable=False)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TeachingClass(TimestampMixin, Base):
    __tablename__ = "teaching_classes"
    __table_args__ = (
        CheckConstraint(
            "class_type IN ('standard', 'elite')",
            name="ck_teaching_classes_type",
        ),
        CheckConstraint(
            "(class_type = 'standard' AND professional_team_id IS NULL) OR "
            "(class_type = 'elite' AND professional_team_id IS NOT NULL)",
            name="ck_teaching_classes_type_team",
        ),
        CheckConstraint("capacity > 0", name="ck_teaching_classes_capacity"),
        CheckConstraint(
            "default_tuition_cents >= 0",
            name="ck_teaching_classes_tuition",
        ),
        UniqueConstraint(
            "cohort_id",
            "name",
            name="uq_teaching_classes_cohort_name",
        ),
        UniqueConstraint(
            "id",
            "cohort_id",
            name="uq_teaching_classes_id_cohort",
        ),
        ForeignKeyConstraint(
            ["professional_team_id", "project_id"],
            ["professional_teams.id", "professional_teams.project_id"],
            name="fk_teaching_classes_team_project",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    cohort_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    class_type: Mapped[str] = mapped_column(String(16), nullable=False)
    professional_team_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
    )
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    default_tuition_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class CoachAssignment(TimestampMixin, Base):
    __tablename__ = "coach_assignments"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    class_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teaching_classes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    coach_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assignment_type: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    end_reason: Mapped[str | None] = mapped_column(String(200))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )

    __table_args__ = (
        CheckConstraint(
            "assignment_type IN ('primary', 'assistant')",
            name="ck_coach_assignments_type",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_coach_assignments_valid_until",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at > valid_from",
            name="ck_coach_assignments_ended_at",
        ),
        CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_by_user_id IS NOT NULL "
            "AND end_reason IS NOT NULL AND btrim(end_reason) <> '')",
            name="ck_coach_assignments_end_fields",
        ),
        ExcludeConstraint(
            ("class_id", "="),
            (
                text(
                    "tstzrange(valid_from, "
                    "COALESCE(LEAST(valid_until, ended_at), valid_until, ended_at, "
                    "'infinity'::timestamptz), '[)')"
                ),
                "&&",
            ),
            name="excl_coach_assignments_one_primary",
            using="gist",
            where=text("assignment_type = 'primary'"),
        ),
        ExcludeConstraint(
            ("class_id", "="),
            ("coach_person_id", "="),
            (
                text(
                    "tstzrange(valid_from, "
                    "COALESCE(LEAST(valid_until, ended_at), valid_until, ended_at, "
                    "'infinity'::timestamptz), '[)')"
                ),
                "&&",
            ),
            name="excl_coach_assignments_no_duplicate_coach",
            using="gist",
        ),
        Index(
            "ix_coach_assignments_portal_current",
            "coach_person_id",
            "valid_from",
            "valid_until",
            "class_id",
            postgresql_where=text("ended_at IS NULL"),
        ),
    )
