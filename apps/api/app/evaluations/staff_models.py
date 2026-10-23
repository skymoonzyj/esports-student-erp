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
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin

_LIFE_TEACHER_ROLE_ID = "41000000-0000-4000-8000-000000000001"
_EFFECTIVE_RANGE = (
    "tstzrange(valid_from, "
    "COALESCE(LEAST(valid_until, ended_at), valid_until, ended_at, "
    "'infinity'::timestamptz), '[)')"
)


class EvaluationStaffRole(TimestampMixin, Base):
    __tablename__ = "evaluation_staff_roles"
    __table_args__ = (
        CheckConstraint(
            "key ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'",
            name="ck_evaluation_staff_roles_key",
        ),
        CheckConstraint(
            "btrim(name) <> ''",
            name="ck_evaluation_staff_roles_name",
        ),
        CheckConstraint(
            "category IN ('teaching', 'life_service', 'operations')",
            name="ck_evaluation_staff_roles_category",
        ),
        CheckConstraint(
            "created_by_user_id IS NOT NULL OR "
            f"(id = '{_LIFE_TEACHER_ROLE_ID}'::uuid AND key = 'life_teacher' "
            "AND is_builtin IS TRUE)",
            name="ck_evaluation_staff_roles_creator",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
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


class CohortStaffAssignment(TimestampMixin, Base):
    __tablename__ = "cohort_staff_assignments"
    __table_args__ = (
        CheckConstraint(
            "(project_id IS NULL) <> (class_id IS NULL)",
            name="ck_cohort_staff_assignments_target",
        ),
        CheckConstraint(
            "btrim(role_name_snapshot) <> ''",
            name="ck_cohort_staff_assignments_role_name_snapshot",
        ),
        CheckConstraint(
            "role_category_snapshot IN ('teaching', 'life_service', 'operations')",
            name="ck_cohort_staff_assignments_role_category_snapshot",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_cohort_staff_assignments_valid_until",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at > valid_from",
            name="ck_cohort_staff_assignments_ended_at",
        ),
        CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_by_user_id IS NOT NULL "
            "AND end_reason IS NOT NULL AND btrim(end_reason) <> '')",
            name="ck_cohort_staff_assignments_end_fields",
        ),
        ForeignKeyConstraint(
            ["class_id", "cohort_id"],
            ["teaching_classes.id", "teaching_classes.cohort_id"],
            name="fk_cohort_staff_assignments_class_cohort",
            ondelete="RESTRICT",
        ),
        ExcludeConstraint(
            ("staff_person_id", "="),
            ("role_id", "="),
            ("project_id", "="),
            (text(_EFFECTIVE_RANGE), "&&"),
            name="excl_cohort_staff_assignments_project_overlap",
            using="gist",
            where=text("project_id IS NOT NULL"),
        ),
        ExcludeConstraint(
            ("staff_person_id", "="),
            ("role_id", "="),
            ("class_id", "="),
            (text(_EFFECTIVE_RANGE), "&&"),
            name="excl_cohort_staff_assignments_class_overlap",
            using="gist",
            where=text("class_id IS NOT NULL"),
        ),
        Index(
            "ix_cohort_staff_assignments_project_current",
            "project_id",
            "role_id",
            "staff_person_id",
            "valid_from",
            "valid_until",
            postgresql_where=text("project_id IS NOT NULL AND ended_at IS NULL"),
        ),
        Index(
            "ix_cohort_staff_assignments_class_current",
            "class_id",
            "role_id",
            "staff_person_id",
            "valid_from",
            "valid_until",
            postgresql_where=text("class_id IS NOT NULL AND ended_at IS NULL"),
        ),
        Index(
            "ix_cohort_staff_assignments_staff_history",
            "staff_person_id",
            "role_id",
            "valid_from",
            "id",
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
    role_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evaluation_staff_roles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    staff_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
    )
    class_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    role_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    role_category_snapshot: Mapped[str] = mapped_column(String(24), nullable=False)
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


class CohortStaffAssignmentPermission(Base):
    __tablename__ = "cohort_staff_assignment_permissions"

    assignment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("cohort_staff_assignments.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
