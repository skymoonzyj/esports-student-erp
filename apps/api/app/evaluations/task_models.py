from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class EvaluationQuarantineCleanup(TimestampMixin, Base):
    __tablename__ = "evaluation_quarantine_cleanup"
    __table_args__ = (
        CheckConstraint("storage_key ~ '^\\.owned-[0-9a-f]{32}$'",
                        name="ck_evaluation_quarantine_cleanup_key"),
        CheckConstraint("attempts >= 0", name="ck_evaluation_quarantine_cleanup_attempts"),
        Index("ix_evaluation_quarantine_cleanup_due", "next_attempt_at", "storage_key"),
    )

    storage_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class SystemEvaluationUploadSession(TimestampMixin, Base):
    __tablename__ = "system_evaluation_upload_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'committed', 'expired', 'cancelled')",
            name="ck_system_evaluation_upload_sessions_status",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_system_evaluation_upload_sessions_expiry",
        ),
        CheckConstraint(
            "delegated_upload_reason IS NULL OR btrim(delegated_upload_reason) <> ''",
            name="ck_system_evaluation_upload_sessions_delegated_reason",
        ),
        CheckConstraint(
            "(student_person_id IS NULL AND enrollment_id IS NULL) OR "
            "(student_person_id IS NOT NULL AND enrollment_id IS NOT NULL)",
            name="ck_system_evaluation_upload_sessions_target",
        ),
        Index(
            "ix_system_evaluation_upload_sessions_expiry",
            "status",
            "expires_at",
            "id",
        ),
        Index("ix_evaluation_upload_account_open", "created_by_user_id", "expires_at",
              postgresql_where=text("status = 'open'")),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    student_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("student_profiles.person_id", ondelete="RESTRICT"),
    )
    enrollment_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default="open"
    )
    delegated_upload_reason: Mapped[str | None] = mapped_column(String(300))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemEvaluationUploadSessionMedia(TimestampMixin, Base):
    __tablename__ = "system_evaluation_upload_session_media"
    __table_args__ = (
        CheckConstraint(
            "position BETWEEN 1 AND 3",
            name="ck_system_evaluation_upload_media_position",
        ),
        CheckConstraint(
            "btrim(slot_key) <> ''",
            name="ck_system_evaluation_upload_media_slot",
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_system_evaluation_upload_media_sha256",
        ),
        CheckConstraint(
            "btrim(quarantine_storage_key) <> '' "
            "AND left(quarantine_storage_key, 1) <> '/' "
            "AND position('..' IN quarantine_storage_key) = 0",
            name="ck_system_evaluation_upload_media_storage_key",
        ),
        CheckConstraint(
            "(mime_type = 'image/jpeg' AND canonical_extension = 'jpg') OR "
            "(mime_type = 'image/png' AND canonical_extension = 'png') OR "
            "(mime_type = 'image/webp' AND canonical_extension = 'webp')",
            name="ck_system_evaluation_upload_media_mime_extension",
        ),
        CheckConstraint(
            "size_bytes BETWEEN 1 AND 20971520",
            name="ck_system_evaluation_upload_media_size",
        ),
        CheckConstraint(
            "width_pixels > 0 AND height_pixels > 0 "
            "AND width_pixels::bigint * height_pixels::bigint <= 50000000",
            name="ck_system_evaluation_upload_media_dimensions",
        ),
        UniqueConstraint(
            "session_id",
            "position",
            name="uq_system_evaluation_upload_media_position",
        ),
        UniqueConstraint(
            "session_id",
            "slot_key",
            name="uq_system_evaluation_upload_media_slot",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("system_evaluation_upload_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_key: Mapped[str] = mapped_column(String(80), nullable=False)
    quarantine_storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_extension: Mapped[str] = mapped_column(String(8), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width_pixels: Mapped[int] = mapped_column(Integer, nullable=False)
    height_pixels: Mapped[int] = mapped_column(Integer, nullable=False)


class SystemEvaluationTask(TimestampMixin, Base):
    __tablename__ = "system_evaluation_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'identifying', 'pending_match', 'queued', "
            "'processing', 'failed', 'manual_input', 'needs_review', 'rejected', "
            "'correction_pending', 'archived', 'published', 'revision_draft')",
            name="ck_system_evaluation_tasks_status",
        ),
        CheckConstraint(
            "lock_version >= 1",
            name="ck_system_evaluation_tasks_lock_version",
        ),
        CheckConstraint(
            "current_attempt BETWEEN 0 AND 3 "
            "AND automatic_attempts BETWEEN 0 AND 3 "
            "AND automatic_attempts <= current_attempt",
            name="ck_system_evaluation_tasks_attempts",
        ),
        CheckConstraint(
            "upload_window_end >= upload_window_start",
            name="ck_system_evaluation_tasks_window",
        ),
        CheckConstraint(
            "(student_person_id IS NULL AND enrollment_id IS NULL "
            "AND cohort_id IS NULL AND class_id IS NULL "
            "AND status IN ('uploaded', 'identifying', 'pending_match')) OR "
            "(student_person_id IS NOT NULL AND enrollment_id IS NOT NULL "
            "AND cohort_id IS NOT NULL AND class_id IS NOT NULL)",
            name="ck_system_evaluation_tasks_target",
        ),
        CheckConstraint(
            "analysis_profile_version >= 1",
            name="ck_system_evaluation_tasks_profile_version",
        ),
        CheckConstraint(
            "jsonb_typeof(analysis_config_snapshot) = 'object'",
            name="ck_system_evaluation_tasks_config_snapshot",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,79}$'",
            name="ck_system_evaluation_tasks_failure_code",
        ),
        CheckConstraint(
            "revision_of_task_id IS NULL OR revision_of_task_id <> id",
            name="ck_system_evaluation_tasks_revision_not_self",
        ),
        CheckConstraint(
            "(revision_of_task_id IS NULL AND revision_of_version_id IS NULL) OR "
            "(revision_of_task_id IS NOT NULL AND revision_of_version_id IS NOT NULL)",
            name="ck_system_evaluation_tasks_revision_pair",
        ),
        ForeignKeyConstraint(
            ["enrollment_id", "student_person_id", "cohort_id", "class_id"],
            [
                "enrollments.id",
                "enrollments.student_person_id",
                "enrollments.cohort_id",
                "enrollments.class_id",
            ],
            name="fk_system_evaluation_tasks_enrollment_target",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["class_id", "cohort_id", "project_id"],
            [
                "teaching_classes.id",
                "teaching_classes.cohort_id",
                "teaching_classes.project_id",
            ],
            name="fk_system_evaluation_tasks_class_target",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["analysis_profile_id", "project_id", "analysis_profile_version"],
            [
                "project_analysis_profiles.id",
                "project_analysis_profiles.project_id",
                "project_analysis_profiles.version",
            ],
            name="fk_system_evaluation_tasks_profile_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_result_version_id", "id"],
            [
                "system_evaluation_result_versions.id",
                "system_evaluation_result_versions.task_id",
            ],
            name="fk_system_evaluation_tasks_current_result",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["published_result_version_id", "id"],
            [
                "system_evaluation_result_versions.id",
                "system_evaluation_result_versions.task_id",
            ],
            name="fk_system_evaluation_tasks_published_result",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["revision_of_version_id", "revision_of_task_id"],
            [
                "system_evaluation_result_versions.id",
                "system_evaluation_result_versions.task_id",
            ],
            name="fk_system_evaluation_tasks_revision_result",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "public_number", name="uq_system_evaluation_tasks_public_number"
        ),
        UniqueConstraint(
            "upload_session_id", name="uq_system_evaluation_tasks_upload_session"
        ),
        Index("ix_system_evaluation_tasks_queue", "status", "available_at", "id"),
        Index(
            "ix_system_evaluation_tasks_student_history",
            "student_person_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    public_number: Mapped[str] = mapped_column(String(40), nullable=False)
    upload_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("system_evaluation_upload_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    student_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("student_profiles.person_id", ondelete="RESTRICT"),
    )
    enrollment_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    cohort_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    class_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    analysis_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("project_analysis_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    analysis_profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    upload_window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    upload_window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    current_attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    automatic_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    manual_queue_reason: Mapped[str | None] = mapped_column(String(80))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_result_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    published_result_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    revision_of_task_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("system_evaluation_tasks.id", ondelete="RESTRICT"),
    )
    revision_of_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matched_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class SystemEvaluationTaskMedia(Base):
    __tablename__ = "system_evaluation_task_media"
    __table_args__ = (
        CheckConstraint(
            "position BETWEEN 1 AND 3",
            name="ck_system_evaluation_task_media_position",
        ),
        CheckConstraint(
            "btrim(slot_key) <> ''",
            name="ck_system_evaluation_task_media_slot",
        ),
        UniqueConstraint(
            "task_id",
            "position",
            name="uq_system_evaluation_task_media_position",
        ),
        UniqueConstraint(
            "task_id", "slot_key", name="uq_system_evaluation_task_media_slot"
        ),
        UniqueConstraint(
            "task_id", "media_id", name="uq_system_evaluation_task_media_asset"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("system_evaluation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    media_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evaluation_media_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_key: Mapped[str] = mapped_column(String(80), nullable=False)


class SystemEvaluationEvent(Base):
    __tablename__ = "system_evaluation_events"
    __table_args__ = (
        CheckConstraint(
            "sequence >= 1", name="ck_system_evaluation_events_sequence"
        ),
        CheckConstraint(
            "btrim(event_type) <> ''",
            name="ck_system_evaluation_events_type",
        ),
        CheckConstraint(
            "jsonb_typeof(event_metadata) = 'object'",
            name="ck_system_evaluation_events_metadata",
        ),
        CheckConstraint(
            "(from_status IS NULL AND to_status IS NULL) OR "
            "(from_status IS NOT NULL AND to_status IS NOT NULL "
            "AND from_status IN ('uploaded', 'identifying', 'pending_match', 'queued', "
            "'processing', 'failed', 'manual_input', 'needs_review', 'rejected', "
            "'correction_pending', 'archived', 'published', 'revision_draft') "
            "AND to_status IN ('uploaded', 'identifying', 'pending_match', 'queued', "
            "'processing', 'failed', 'manual_input', 'needs_review', 'rejected', "
            "'correction_pending', 'archived', 'published', 'revision_draft') "
            "AND from_status <> to_status)",
            name="ck_system_evaluation_events_status",
        ),
        ForeignKeyConstraint(
            ["result_version_id", "task_id"],
            [
                "system_evaluation_result_versions.id",
                "system_evaluation_result_versions.task_id",
            ],
            name="fk_system_evaluation_events_result",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "task_id", "sequence", name="uq_system_evaluation_events_task_sequence"
        ),
        Index("ix_system_evaluation_events_task_time", "task_id", "occurred_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("system_evaluation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    result_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    reason: Mapped[str | None] = mapped_column(Text)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvaluationIdempotencyKey(Base):
    __tablename__ = "evaluation_idempotency_keys"
    __table_args__ = (
        CheckConstraint(
            "key_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_idempotency_key_digest",
        ),
        CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_idempotency_payload_digest",
        ),
        CheckConstraint(
            "btrim(scope) <> '' AND btrim(actor_key) <> ''",
            name="ck_evaluation_idempotency_scope_actor",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_evaluation_idempotency_expiry",
        ),
        UniqueConstraint(
            "scope",
            "actor_key",
            "key_digest",
            name="uq_evaluation_idempotency_scope_actor_key",
        ),
        Index("ix_evaluation_idempotency_expiry", "expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    scope: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_key: Mapped[str] = mapped_column(String(120), nullable=False)
    key_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
