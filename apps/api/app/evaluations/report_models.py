from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class StudentReport(TimestampMixin, Base):
    __tablename__ = "student_reports"
    __table_args__ = (
        CheckConstraint(
            "report_kind IN ('daily', 'weekly')",
            name="ck_student_reports_kind",
        ),
        CheckConstraint(
            "period_sequence >= 1 AND period_end_date >= period_start_date "
            "AND ((report_kind = 'daily' "
            "AND period_end_date = period_start_date) "
            "OR (report_kind = 'weekly' "
            "AND period_end_date - period_start_date BETWEEN 0 AND 6))",
            name="ck_student_reports_period",
        ),
        CheckConstraint(
            "status IN ('draft', 'pending_review', 'rejected', "
            "'correction_draft', 'published', 'revision_pending_review')",
            name="ck_student_reports_status",
        ),
        CheckConstraint(
            "lock_version >= 1",
            name="ck_student_reports_lock_version",
        ),
        CheckConstraint(
            "btrim(role_key_snapshot) <> '' "
            "AND btrim(role_name_snapshot) <> '' "
            "AND role_category_snapshot IN "
            "('teaching', 'life_service', 'operations') "
            "AND btrim(cohort_name_snapshot) <> '' "
            "AND btrim(project_name_snapshot) <> '' "
            "AND btrim(class_name_snapshot) <> ''",
            name="ck_student_reports_snapshots",
        ),
        CheckConstraint(
            "(author_source = 'coach_assignment' "
            "AND coach_assignment_id IS NOT NULL "
            "AND staff_assignment_id IS NULL) OR "
            "(author_source = 'staff_assignment' "
            "AND coach_assignment_id IS NULL "
            "AND staff_assignment_id IS NOT NULL)",
            name="ck_student_reports_assignment_source",
        ),
        CheckConstraint(
            "submission_opens_at <= initial_submission_deadline "
            "AND (correction_due_at IS NULL "
            "OR correction_due_at >= submission_opens_at)",
            name="ck_student_reports_windows",
        ),
        CheckConstraint(
            "(status = 'draft' AND current_version_id IS NULL "
            "AND published_version_id IS NULL) OR "
            "(status = 'pending_review' AND current_version_id IS NOT NULL "
            "AND (published_version_id IS NULL "
            "OR current_version_id <> published_version_id)) OR "
            "(status IN ('rejected', 'correction_draft') "
            "AND current_version_id IS NOT NULL) OR "
            "(status = 'published' AND current_version_id IS NOT NULL "
            "AND current_version_id = published_version_id) OR "
            "(status = 'revision_pending_review' "
            "AND current_version_id IS NOT NULL "
            "AND published_version_id IS NOT NULL "
            "AND current_version_id <> published_version_id)",
            name="ck_student_reports_version_pointers",
        ),
        CheckConstraint(
            "((status = 'draft' AND submitted_at IS NULL) "
            "OR (status <> 'draft' AND submitted_at IS NOT NULL)) "
            "AND ((published_version_id IS NULL AND published_at IS NULL) "
            "OR (published_version_id IS NOT NULL AND published_at IS NOT NULL))",
            name="ck_student_reports_state_times",
        ),
        ForeignKeyConstraint(
            ["enrollment_id", "student_person_id", "cohort_id", "class_id"],
            [
                "enrollments.id",
                "enrollments.student_person_id",
                "enrollments.cohort_id",
                "enrollments.class_id",
            ],
            name="fk_student_reports_enrollment_target",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["class_id", "cohort_id", "project_id"],
            [
                "teaching_classes.id",
                "teaching_classes.cohort_id",
                "teaching_classes.project_id",
            ],
            name="fk_student_reports_class_target",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["student_report_versions.id", "student_report_versions.report_id"],
            name="fk_student_reports_current_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["published_version_id", "id"],
            ["student_report_versions.id", "student_report_versions.report_id"],
            name="fk_student_reports_published_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["current_window_grant_id", "id"],
            [
                "student_report_window_grants.id",
                "student_report_window_grants.report_id",
            ],
            name="fk_student_reports_window_grant",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "enrollment_id",
            "report_kind",
            "period_start_date",
            "author_person_id",
            name="uq_student_reports_author_period",
        ),
        UniqueConstraint(
            "id",
            "enrollment_id",
            name="uq_student_reports_id_enrollment",
        ),
        Index(
            "ix_student_reports_author_work",
            "author_person_id",
            "status",
            "period_start_date",
            "id",
        ),
        Index(
            "ix_student_reports_review_queue",
            "status",
            "cohort_id",
            "project_id",
            "class_id",
            "submitted_at",
            "id",
        ),
        Index(
            "ix_student_reports_family_history",
            "student_person_id",
            "published_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    enrollment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    student_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    cohort_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    class_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    author_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    author_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    author_source: Mapped[str] = mapped_column(String(24), nullable=False)
    coach_assignment_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("coach_assignments.id", ondelete="RESTRICT"),
    )
    staff_assignment_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("cohort_staff_assignments.id", ondelete="RESTRICT"),
    )
    role_key_snapshot: Mapped[str] = mapped_column(String(40), nullable=False)
    role_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    role_category_snapshot: Mapped[str] = mapped_column(String(24), nullable=False)
    cohort_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    project_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    class_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    report_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    period_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    submission_opens_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    initial_submission_deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default="draft"
    )
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    published_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    current_window_grant_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correction_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentReportDraft(TimestampMixin, Base):
    __tablename__ = "student_report_drafts"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(body)) BETWEEN 1 AND 5000",
            name="ck_student_report_drafts_body",
        ),
        CheckConstraint(
            "jsonb_typeof(dimension_values) = 'object'",
            name="ck_student_report_drafts_dimensions",
        ),
        CheckConstraint(
            "draft_kind IN ('initial', 'correction', 'revision')",
            name="ck_student_report_drafts_kind",
        ),
        CheckConstraint(
            "draft_revision >= 1",
            name="ck_student_report_drafts_revision",
        ),
        Index("ix_student_report_drafts_updated", "updated_at", "report_id"),
    )

    report_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("student_reports.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    dimension_values: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    draft_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    draft_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    last_edited_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )


class StudentReportVersion(Base):
    __tablename__ = "student_report_versions"
    __table_args__ = (
        CheckConstraint(
            "version >= 1",
            name="ck_student_report_versions_version",
        ),
        CheckConstraint(
            "source IN ('initial', 'correction', 'revision')",
            name="ck_student_report_versions_source",
        ),
        CheckConstraint(
            "char_length(btrim(body)) BETWEEN 1 AND 5000",
            name="ck_student_report_versions_body",
        ),
        CheckConstraint(
            "jsonb_typeof(dimension_values) = 'object'",
            name="ck_student_report_versions_dimensions",
        ),
        ForeignKeyConstraint(
            ["previous_version_id", "report_id"],
            ["student_report_versions.id", "student_report_versions.report_id"],
            name="fk_student_report_versions_previous",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "report_id",
            "version",
            name="uq_student_report_versions_report_version",
        ),
        UniqueConstraint(
            "id",
            "report_id",
            name="uq_student_report_versions_id_report",
        ),
        Index(
            "ix_student_report_versions_history",
            "report_id",
            "version",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    report_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("student_reports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    dimension_values: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    submitted_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    previous_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StudentReportReview(Base):
    __tablename__ = "student_report_reviews"
    __table_args__ = (
        CheckConstraint(
            "(decision = 'approved' AND reason IS NULL) OR "
            "(decision = 'rejected' AND reason IS NOT NULL "
            "AND char_length(btrim(reason)) BETWEEN 1 AND 2000)",
            name="ck_student_report_reviews_decision",
        ),
        ForeignKeyConstraint(
            ["version_id", "report_id"],
            ["student_report_versions.id", "student_report_versions.report_id"],
            name="fk_student_report_reviews_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "version_id",
            name="uq_student_report_reviews_version",
        ),
        Index(
            "ix_student_report_reviews_report_time",
            "report_id",
            "reviewed_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    report_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("student_reports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class StudentReportWindowGrant(Base):
    __tablename__ = "student_report_window_grants"
    __table_args__ = (
        CheckConstraint(
            "grant_kind IN ('correction', 'revision')",
            name="ck_student_report_window_grants_kind",
        ),
        CheckConstraint(
            "char_length(btrim(reason)) BETWEEN 1 AND 500",
            name="ck_student_report_window_grants_reason",
        ),
        CheckConstraint(
            "expires_at > opens_at",
            name="ck_student_report_window_grants_window",
        ),
        UniqueConstraint(
            "id",
            "report_id",
            name="uq_student_report_window_grants_id_report",
        ),
        Index(
            "ix_student_report_window_grants_report_time",
            "report_id",
            "opens_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    report_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("student_reports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    grant_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    granted_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
