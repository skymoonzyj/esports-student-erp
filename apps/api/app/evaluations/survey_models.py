"""Persistent models for immutable camp completion surveys."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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

_JSON_ARRAY_DEFAULT = text("'[]'::jsonb")
_JSON_OBJECT_DEFAULT = text("'{}'::jsonb")


class CampSurveyCycle(TimestampMixin, Base):
    """One immutable survey lifecycle rooted at a completed enrollment."""

    __tablename__ = "camp_survey_cycles"
    __table_args__ = (
        CheckConstraint(
            "due_at > opens_at", name="ck_camp_survey_cycles_window"
        ),
        CheckConstraint(
            "current_snapshot_version >= 1",
            name="ck_camp_survey_cycles_snapshot_version",
        ),
        ForeignKeyConstraint(
            ["enrollment_id", "student_person_id", "cohort_id", "class_id"],
            [
                "enrollments.id",
                "enrollments.student_person_id",
                "enrollments.cohort_id",
                "enrollments.class_id",
            ],
            name="fk_camp_survey_cycles_enrollment_target",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "enrollment_id", name="uq_camp_survey_cycles_enrollment"
        ),
        Index(
            "ix_camp_survey_cycles_window",
            "opens_at",
            "due_at",
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
    class_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    opens_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    current_snapshot_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class CampSurveySnapshotVersion(TimestampMixin, Base):
    """A versioned, append-only staff roster for a survey cycle."""

    __tablename__ = "camp_survey_snapshot_versions"
    __table_args__ = (
        CheckConstraint(
            "version >= 1", name="ck_camp_survey_snapshot_versions_version"
        ),
        CheckConstraint(
            "btrim(change_reason) <> ''",
            name="ck_camp_survey_snapshot_versions_reason",
        ),
        ForeignKeyConstraint(
            ["cycle_id"],
            ["camp_survey_cycles.id"],
            name="fk_camp_survey_snapshot_versions_cycle",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            name="fk_camp_survey_snapshot_versions_creator",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "cycle_id",
            "version",
            name="uq_camp_survey_snapshot_versions_cycle_version",
        ),
        UniqueConstraint(
            "id",
            "cycle_id",
            name="uq_camp_survey_snapshot_versions_id_cycle",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    cycle_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )


class CampSurveyStaffSnapshot(TimestampMixin, Base):
    """One staff member/role snapshot within a roster version."""

    __tablename__ = "camp_survey_staff_snapshots"
    __table_args__ = (
        CheckConstraint(
            "btrim(staff_name_snapshot) <> ''",
            name="ck_camp_survey_staff_snapshots_name",
        ),
        CheckConstraint(
            "role_category IN ('teaching', 'life_service', 'operations')",
            name="ck_camp_survey_staff_snapshots_category",
        ),
        CheckConstraint(
            "jsonb_typeof(service_intervals) = 'array'",
            name="ck_camp_survey_staff_snapshots_service_intervals",
        ),
        CheckConstraint(
            "jsonb_typeof(roles_snapshot) = 'array' AND jsonb_array_length(roles_snapshot) >= 1",
            name="ck_camp_survey_staff_snapshots_roles_snapshot",
        ),
        ForeignKeyConstraint(
            ["snapshot_version_id"],
            ["camp_survey_snapshot_versions.id"],
            name="fk_camp_survey_staff_snapshots_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["staff_person_id"],
            ["people.id"],
            name="fk_camp_survey_staff_snapshots_person",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "snapshot_version_id",
            "staff_person_id",
            name="uq_camp_survey_staff_snapshots_staff",
        ),
        UniqueConstraint(
            "id",
            "snapshot_version_id",
            name="uq_camp_survey_staff_snapshots_id_version",
        ),
        Index(
            "ix_camp_survey_staff_snapshots_staff_history",
            "staff_person_id",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    snapshot_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    staff_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    staff_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    role_key_snapshot: Mapped[str] = mapped_column(String(40), nullable=False)
    role_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    role_category: Mapped[str] = mapped_column(String(24), nullable=False)
    roles_snapshot: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_JSON_ARRAY_DEFAULT
    )
    service_intervals: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_JSON_ARRAY_DEFAULT
    )
    include_in_summary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class CampSurveySubmission(TimestampMixin, Base):
    """A respondent's independent submission for a cycle."""

    __tablename__ = "camp_survey_submissions"
    __table_args__ = (
        CheckConstraint(
            "respondent_kind IN ('self', 'guardian')",
            name="ck_camp_survey_submissions_kind",
        ),
        CheckConstraint(
            "revision_count BETWEEN 0 AND 1",
            name="ck_camp_survey_submissions_revision_count",
        ),
        CheckConstraint(
            "(respondent_kind = 'self' AND guardian_relationship_id IS NULL) OR "
            "(respondent_kind = 'guardian' AND guardian_relationship_id IS NOT NULL)",
            name="ck_camp_survey_submissions_relationship",
        ),
        ForeignKeyConstraint(
            ["cycle_id"],
            ["camp_survey_cycles.id"],
            name="fk_camp_survey_submissions_cycle",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["respondent_person_id"],
            ["people.id"],
            name="fk_camp_survey_submissions_respondent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["student_person_id"],
            ["people.id"],
            name="fk_camp_survey_submissions_student",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["guardian_relationship_id"],
            ["guardian_relationships.id"],
            name="fk_camp_survey_submissions_relationship",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "cycle_id",
            "respondent_person_id",
            name="uq_camp_survey_submissions_cycle_respondent",
        ),
        UniqueConstraint(
            "id",
            "cycle_id",
            name="uq_camp_survey_submissions_id_cycle",
        ),
        Index(
            "ix_camp_survey_submissions_student",
            "student_person_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    cycle_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    respondent_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    student_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    respondent_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    guardian_relationship_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    guardian_relationship_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    revision_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    latest_version: Mapped[int | None] = mapped_column(Integer)
    first_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class CampSurveyVersion(TimestampMixin, Base):
    """Complete immutable version 1 or 2 of a submission."""

    __tablename__ = "camp_survey_versions"
    __table_args__ = (
        CheckConstraint(
            "version IN (1, 2)", name="ck_camp_survey_versions_version"
        ),
        CheckConstraint(
            "course_training_score BETWEEN 1 AND 10 "
            "AND life_service_score BETWEEN 1 AND 10 "
            "AND service_management_score BETWEEN 1 AND 10",
            name="ck_camp_survey_versions_scores",
        ),
        CheckConstraint(
            "jsonb_typeof(favorite_cuisines) = 'array' "
            "AND jsonb_array_length(favorite_cuisines) BETWEEN 1 AND 3 "
            "AND jsonb_typeof(favorite_dishes) = 'array' "
            "AND jsonb_array_length(favorite_dishes) BETWEEN 1 AND 3 "
            "AND NOT jsonb_path_exists(favorite_cuisines, "
            "'$[*]?(@.cuisine_option_id.type() != \"string\" || "
            "@.name.type() != \"string\" || "
            "(@.custom_name != null && @.custom_name.type() != \"string\"))') "
            "AND NOT jsonb_path_exists(favorite_dishes, "
            "'$[*]?(@.type() != \"string\" || @ == \"\")')",
            name="ck_camp_survey_versions_food_snapshot",
        ),
        ForeignKeyConstraint(
            ["submission_id", "cycle_id"],
            ["camp_survey_submissions.id", "camp_survey_submissions.cycle_id"],
            name="fk_camp_survey_versions_submission",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_version_id", "cycle_id"],
            [
                "camp_survey_snapshot_versions.id",
                "camp_survey_snapshot_versions.cycle_id",
            ],
            name="fk_camp_survey_versions_snapshot",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "submission_id",
            "version",
            name="uq_camp_survey_versions_submission_version",
        ),
        UniqueConstraint(
            "id",
            "snapshot_version_id",
            name="uq_camp_survey_versions_id_snapshot",
        ),
        Index(
            "ix_camp_survey_versions_submission_history",
            "submission_id",
            "version",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    submission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    snapshot_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    cycle_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    course_training_score: Mapped[int] = mapped_column(Integer, nullable=False)
    course_training_reason: Mapped[str | None] = mapped_column(Text)
    life_service_score: Mapped[int] = mapped_column(Integer, nullable=False)
    life_service_reason: Mapped[str | None] = mapped_column(Text)
    service_management_score: Mapped[int] = mapped_column(Integer, nullable=False)
    service_management_reason: Mapped[str | None] = mapped_column(Text)
    overall_suggestion: Mapped[str | None] = mapped_column(Text)
    favorite_cuisines: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    favorite_dishes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    food_preference_version: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampSurveyStaffRating(TimestampMixin, Base):
    """Immutable per-staff score attached to a complete survey version."""

    __tablename__ = "camp_survey_staff_ratings"
    __table_args__ = (
        CheckConstraint(
            "score BETWEEN 1 AND 10",
            name="ck_camp_survey_staff_ratings_score",
        ),
        CheckConstraint(
            "reason IS NULL OR btrim(reason) <> ''",
            name="ck_camp_survey_staff_ratings_reason",
        ),
        CheckConstraint(
            "suggestion IS NULL OR btrim(suggestion) <> ''",
            name="ck_camp_survey_staff_ratings_suggestion",
        ),
        ForeignKeyConstraint(
            ["version_id", "snapshot_version_id"],
            ["camp_survey_versions.id", "camp_survey_versions.snapshot_version_id"],
            name="fk_camp_survey_staff_ratings_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["staff_snapshot_id", "snapshot_version_id"],
            [
                "camp_survey_staff_snapshots.id",
                "camp_survey_staff_snapshots.snapshot_version_id",
            ],
            name="fk_camp_survey_staff_ratings_staff",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "version_id",
            "staff_snapshot_id",
            name="uq_camp_survey_staff_ratings_version_staff",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    staff_snapshot_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    snapshot_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)


class CampSurveySupplement(TimestampMixin, Base):
    """Append-only task for staff added by a corrected roster snapshot."""

    __tablename__ = "camp_survey_supplements"
    __table_args__ = (
        CheckConstraint(
            "btrim(change_reason) <> ''",
            name="ck_camp_survey_supplements_reason",
        ),
        ForeignKeyConstraint(
            ["submission_id", "cycle_id"],
            ["camp_survey_submissions.id", "camp_survey_submissions.cycle_id"],
            name="fk_camp_survey_supplements_submission",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_version_id", "cycle_id"],
            [
                "camp_survey_snapshot_versions.id",
                "camp_survey_snapshot_versions.cycle_id",
            ],
            name="fk_camp_survey_supplements_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            name="fk_camp_survey_supplements_creator",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "submission_id",
            "snapshot_version_id",
            name="uq_camp_survey_supplements_submission_snapshot",
        ),
        UniqueConstraint(
            "id",
            "snapshot_version_id",
            name="uq_camp_survey_supplements_id_snapshot",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    submission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    snapshot_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    cycle_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampSurveySupplementRating(TimestampMixin, Base):
    """Immutable score for one staff member in a supplement task."""

    __tablename__ = "camp_survey_supplement_ratings"
    __table_args__ = (
        CheckConstraint(
            "score BETWEEN 1 AND 10",
            name="ck_camp_survey_supplement_ratings_score",
        ),
        CheckConstraint(
            "reason IS NULL OR btrim(reason) <> ''",
            name="ck_camp_survey_supplement_ratings_reason",
        ),
        CheckConstraint(
            "suggestion IS NULL OR btrim(suggestion) <> ''",
            name="ck_camp_survey_supplement_ratings_suggestion",
        ),
        ForeignKeyConstraint(
            ["supplement_id", "snapshot_version_id"],
            [
                "camp_survey_supplements.id",
                "camp_survey_supplements.snapshot_version_id",
            ],
            name="fk_camp_survey_supplement_ratings_supplement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["staff_snapshot_id", "snapshot_version_id"],
            [
                "camp_survey_staff_snapshots.id",
                "camp_survey_staff_snapshots.snapshot_version_id",
            ],
            name="fk_camp_survey_supplement_ratings_staff",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "supplement_id",
            "staff_snapshot_id",
            name="uq_camp_survey_supplement_ratings_supplement_staff",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    supplement_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    staff_snapshot_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    snapshot_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
