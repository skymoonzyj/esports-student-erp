"""Add the system-evaluation processing workflow.

Revision ID: 0012_system_evaluation_processing
Revises: 0011_housing_real_name_reporting
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_system_evaluation_processing"
down_revision: str | Sequence[str] | None = "0011_housing_real_name_reporting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM_EVALUATION_PERMISSIONS = {
    "evaluation_jobs.review": (
        UUID("20000000-0000-4000-8000-000000000029"),
        "审核系统评价",
        True,
    ),
    "evaluation_jobs.match": (
        UUID("20000000-0000-4000-8000-000000000044"),
        "确认系统评价目标",
        True,
    ),
    "evaluation_jobs.manual_input": (
        UUID("20000000-0000-4000-8000-000000000045"),
        "人工录入系统评价",
        True,
    ),
    "evaluation_adapters.certify": (
        UUID("20000000-0000-4000-8000-000000000046"),
        "认证评价适配器",
        True,
    ),
}

SYSTEM_EVALUATION_TABLES = (
    "system_evaluation_upload_session_media",
    "system_evaluation_task_media",
    "system_evaluation_attempts",
    "system_evaluation_events",
    "system_evaluation_result_versions",
    "system_evaluation_tasks",
    "system_evaluation_upload_sessions",
    "evaluation_adapter_certifications",
    "evaluation_idempotency_keys",
)

TASK_STATUSES = (
    "'uploaded', 'identifying', 'pending_match', 'queued', 'processing', "
    "'failed', 'manual_input', 'needs_review', 'rejected', "
    "'correction_pending', 'archived', 'published', 'revision_draft'"
)


def _role_permission_grants(bind: sa.Connection) -> set[tuple[str, str, str]]:
    return set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id"
            )
        ).tuples()
    )


def _preflight_upgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE permissions, role_permissions, roles, "
            "project_analysis_profiles, enrollments, teaching_classes "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    permission_ids = [value[0] for value in SYSTEM_EVALUATION_PERMISSIONS.values()]
    permission_keys = list(SYSTEM_EVALUATION_PERMISSIONS)
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM permissions "
            "WHERE id = ANY(CAST(:permission_ids AS uuid[])) "
            "OR key = ANY(CAST(:permission_keys AS text[])))"
        ),
        {"permission_ids": permission_ids, "permission_keys": permission_keys},
    ):
        raise RuntimeError(
            "cannot upgrade system evaluation with preexisting permission"
        )
    if bind.scalar(sa.text("SELECT id FROM roles WHERE key = 'admin'")) is None:
        raise RuntimeError("cannot upgrade system evaluation without admin role")


def _add_profile_and_target_contracts() -> None:
    op.add_column(
        "project_analysis_profiles",
        sa.Column(
            "media_slots",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "project_analysis_profiles",
        sa.Column(
            "result_schema_version",
            sa.String(length=40),
            server_default=sa.text("'1'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_project_analysis_profiles_media_slots",
        "project_analysis_profiles",
        "jsonb_typeof(media_slots) = 'array'",
    )
    op.create_check_constraint(
        "ck_project_analysis_profiles_result_schema_version",
        "project_analysis_profiles",
        "btrim(result_schema_version) <> ''",
    )
    op.drop_constraint(
        "ck_project_analysis_profiles_manual_only",
        "project_analysis_profiles",
        type_="check",
    )
    op.create_check_constraint(
        "ck_project_analysis_profiles_manual_only",
        "project_analysis_profiles",
        "mode <> 'manual_only' OR "
        "(adapter_id IS NULL AND adapter_version IS NULL "
        "AND scoring_rule_version IS NULL "
        "AND confidence_thresholds = '{}'::jsonb "
        "AND auto_publish_warning_levels = '[]'::jsonb "
        "AND publish_mode = 'review_required')",
    )
    op.create_unique_constraint(
        "uq_project_analysis_profiles_system_evaluation_identity",
        "project_analysis_profiles",
        ["id", "project_id", "version"],
    )
    op.create_unique_constraint(
        "uq_enrollments_system_evaluation_target",
        "enrollments",
        ["id", "student_person_id", "cohort_id", "class_id"],
    )
    op.create_unique_constraint(
        "uq_teaching_classes_system_evaluation_target",
        "teaching_classes",
        ["id", "cohort_id", "project_id"],
    )


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def _create_upload_tables() -> None:
    op.create_table(
        "system_evaluation_upload_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("student_person_id", sa.UUID(), nullable=True),
        sa.Column("enrollment_id", sa.UUID(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="open", nullable=False
        ),
        sa.Column("delegated_upload_reason", sa.String(length=300), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('open', 'committed', 'expired', 'cancelled')",
            name="ck_system_evaluation_upload_sessions_status",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_system_evaluation_upload_sessions_expiry",
        ),
        sa.CheckConstraint(
            "delegated_upload_reason IS NULL OR "
            "btrim(delegated_upload_reason) <> ''",
            name="ck_system_evaluation_upload_sessions_delegated_reason",
        ),
        sa.CheckConstraint(
            "(student_person_id IS NULL AND enrollment_id IS NULL) OR "
            "(student_person_id IS NOT NULL AND enrollment_id IS NOT NULL)",
            name="ck_system_evaluation_upload_sessions_target",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_upload_sessions_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            name="fk_system_evaluation_upload_sessions_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_person_id"],
            ["student_profiles.person_id"],
            name="fk_system_evaluation_upload_sessions_student",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id"],
            ["enrollments.id"],
            name="fk_system_evaluation_upload_sessions_enrollment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_evaluation_upload_sessions_expiry",
        "system_evaluation_upload_sessions",
        ["status", "expires_at", "id"],
    )

    op.create_table(
        "system_evaluation_upload_session_media",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("slot_key", sa.String(length=80), nullable=False),
        sa.Column("quarantine_storage_key", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=32), nullable=False),
        sa.Column("canonical_extension", sa.String(length=8), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width_pixels", sa.Integer(), nullable=False),
        sa.Column("height_pixels", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 3",
            name="ck_system_evaluation_upload_media_position",
        ),
        sa.CheckConstraint(
            "btrim(slot_key) <> ''",
            name="ck_system_evaluation_upload_media_slot",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_system_evaluation_upload_media_sha256",
        ),
        sa.CheckConstraint(
            "btrim(quarantine_storage_key) <> '' "
            "AND left(quarantine_storage_key, 1) <> '/' "
            "AND position('..' IN quarantine_storage_key) = 0",
            name="ck_system_evaluation_upload_media_storage_key",
        ),
        sa.CheckConstraint(
            "(mime_type = 'image/jpeg' AND canonical_extension = 'jpg') OR "
            "(mime_type = 'image/png' AND canonical_extension = 'png') OR "
            "(mime_type = 'image/webp' AND canonical_extension = 'webp')",
            name="ck_system_evaluation_upload_media_mime_extension",
        ),
        sa.CheckConstraint(
            "size_bytes BETWEEN 1 AND 20971520",
            name="ck_system_evaluation_upload_media_size",
        ),
        sa.CheckConstraint(
            "width_pixels > 0 AND height_pixels > 0 "
            "AND width_pixels::bigint * height_pixels::bigint <= 50000000",
            name="ck_system_evaluation_upload_media_dimensions",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["system_evaluation_upload_sessions.id"],
            name="fk_system_evaluation_upload_media_session",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "position",
            name="uq_system_evaluation_upload_media_position",
        ),
        sa.UniqueConstraint(
            "session_id",
            "slot_key",
            name="uq_system_evaluation_upload_media_slot",
        ),
    )


def _create_task_table() -> None:
    op.create_table(
        "system_evaluation_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("public_number", sa.String(length=40), nullable=False),
        sa.Column("upload_session_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("student_person_id", sa.UUID(), nullable=True),
        sa.Column("enrollment_id", sa.UUID(), nullable=True),
        sa.Column("cohort_id", sa.UUID(), nullable=True),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=True),
        sa.Column("analysis_profile_id", sa.UUID(), nullable=False),
        sa.Column("analysis_profile_version", sa.Integer(), nullable=False),
        sa.Column(
            "analysis_config_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("upload_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("upload_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("current_attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "automatic_attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("manual_queue_reason", sa.String(length=80), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_result_version_id", sa.UUID(), nullable=True),
        sa.Column("published_result_version_id", sa.UUID(), nullable=True),
        sa.Column("revision_of_task_id", sa.UUID(), nullable=True),
        sa.Column("revision_of_version_id", sa.UUID(), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_by_user_id", sa.UUID(), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_user_id", sa.UUID(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by_user_id", sa.UUID(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            f"status IN ({TASK_STATUSES})",
            name="ck_system_evaluation_tasks_status",
        ),
        sa.CheckConstraint(
            "lock_version >= 1",
            name="ck_system_evaluation_tasks_lock_version",
        ),
        sa.CheckConstraint(
            "current_attempt BETWEEN 0 AND 3 "
            "AND automatic_attempts BETWEEN 0 AND 3 "
            "AND automatic_attempts <= current_attempt",
            name="ck_system_evaluation_tasks_attempts",
        ),
        sa.CheckConstraint(
            "upload_window_end >= upload_window_start",
            name="ck_system_evaluation_tasks_window",
        ),
        sa.CheckConstraint(
            "(student_person_id IS NULL AND enrollment_id IS NULL "
            "AND cohort_id IS NULL AND class_id IS NULL "
            "AND status IN ('uploaded', 'identifying', 'pending_match')) OR "
            "(student_person_id IS NOT NULL AND enrollment_id IS NOT NULL "
            "AND cohort_id IS NOT NULL AND class_id IS NOT NULL)",
            name="ck_system_evaluation_tasks_target",
        ),
        sa.CheckConstraint(
            "analysis_profile_version >= 1",
            name="ck_system_evaluation_tasks_profile_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(analysis_config_snapshot) = 'object'",
            name="ck_system_evaluation_tasks_config_snapshot",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,79}$'",
            name="ck_system_evaluation_tasks_failure_code",
        ),
        sa.CheckConstraint(
            "revision_of_task_id IS NULL OR revision_of_task_id <> id",
            name="ck_system_evaluation_tasks_revision_not_self",
        ),
        sa.CheckConstraint(
            "(revision_of_task_id IS NULL AND revision_of_version_id IS NULL) OR "
            "(revision_of_task_id IS NOT NULL AND revision_of_version_id IS NOT NULL)",
            name="ck_system_evaluation_tasks_revision_pair",
        ),
        sa.ForeignKeyConstraint(
            ["upload_session_id"],
            ["system_evaluation_upload_sessions.id"],
            name="fk_system_evaluation_tasks_upload_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_tasks_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_person_id"],
            ["student_profiles.person_id"],
            name="fk_system_evaluation_tasks_student",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            name="fk_system_evaluation_tasks_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_profile_id"],
            ["project_analysis_profiles.id"],
            name="fk_system_evaluation_tasks_profile",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_profile_id", "project_id", "analysis_profile_version"],
            [
                "project_analysis_profiles.id",
                "project_analysis_profiles.project_id",
                "project_analysis_profiles.version",
            ],
            name="fk_system_evaluation_tasks_profile_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revision_of_task_id"],
            ["system_evaluation_tasks.id"],
            name="fk_system_evaluation_tasks_revision_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["matched_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_tasks_matcher",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_tasks_reviewer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_tasks_publisher",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["archived_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_tasks_archiver",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["class_id", "cohort_id", "project_id"],
            [
                "teaching_classes.id",
                "teaching_classes.cohort_id",
                "teaching_classes.project_id",
            ],
            name="fk_system_evaluation_tasks_class_target",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "public_number", name="uq_system_evaluation_tasks_public_number"
        ),
        sa.UniqueConstraint(
            "upload_session_id", name="uq_system_evaluation_tasks_upload_session"
        ),
    )
    op.create_index(
        "ix_system_evaluation_tasks_queue",
        "system_evaluation_tasks",
        ["status", "available_at", "id"],
    )
    op.create_index(
        "ix_system_evaluation_tasks_student_history",
        "system_evaluation_tasks",
        ["student_person_id", "created_at", "id"],
    )


def _create_task_media_and_attempt_tables() -> None:
    op.create_table(
        "system_evaluation_task_media",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("media_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("slot_key", sa.String(length=80), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 3",
            name="ck_system_evaluation_task_media_position",
        ),
        sa.CheckConstraint(
            "btrim(slot_key) <> ''",
            name="ck_system_evaluation_task_media_slot",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["system_evaluation_tasks.id"],
            name="fk_system_evaluation_task_media_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["evaluation_media_assets.id"],
            name="fk_system_evaluation_task_media_asset",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "position",
            name="uq_system_evaluation_task_media_position",
        ),
        sa.UniqueConstraint(
            "task_id", "slot_key", name="uq_system_evaluation_task_media_slot"
        ),
        sa.UniqueConstraint(
            "task_id", "media_id", name="uq_system_evaluation_task_media_asset"
        ),
    )

    op.create_table(
        "system_evaluation_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("task_lock_version", sa.Integer(), nullable=False),
        sa.Column("adapter_id", sa.String(length=80), nullable=False),
        sa.Column("adapter_version", sa.String(length=40), nullable=False),
        sa.Column("scoring_rule_version", sa.String(length=40), nullable=False),
        sa.Column("result_schema_version", sa.String(length=40), nullable=False),
        sa.Column("worker_identity", sa.String(length=120), nullable=False),
        sa.Column("lease_token_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_class", sa.String(length=80), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("safe_error_summary", sa.String(length=300), nullable=True),
        sa.Column("result_idempotency_digest", sa.CHAR(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "attempt BETWEEN 1 AND 3",
            name="ck_system_evaluation_attempts_number",
        ),
        sa.CheckConstraint(
            "task_lock_version >= 1",
            name="ck_system_evaluation_attempts_task_version",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'succeeded', 'failed', 'expired')",
            name="ck_system_evaluation_attempts_status",
        ),
        sa.CheckConstraint(
            "lease_token_digest ~ '^[0-9a-f]{64}$' "
            "AND lease_expires_at > claimed_at",
            name="ck_system_evaluation_attempts_lease",
        ),
        sa.CheckConstraint(
            "result_idempotency_digest IS NULL OR "
            "result_idempotency_digest ~ '^[0-9a-f]{64}$'",
            name="ck_system_evaluation_attempts_result_idempotency",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND completed_at IS NULL) OR "
            "(status <> 'active' AND completed_at IS NOT NULL)",
            name="ck_system_evaluation_attempts_completion",
        ),
        sa.CheckConstraint(
            "error_class IS NULL OR error_class ~ '^[a-z][a-z0-9_]{0,79}$'",
            name="ck_system_evaluation_attempts_error_class",
        ),
        sa.CheckConstraint(
            "btrim(adapter_id) <> '' AND btrim(adapter_version) <> '' "
            "AND btrim(scoring_rule_version) <> '' "
            "AND btrim(result_schema_version) <> '' "
            "AND btrim(worker_identity) <> ''",
            name="ck_system_evaluation_attempts_versions",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["system_evaluation_tasks.id"],
            name="fk_system_evaluation_attempts_task",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "attempt", name="uq_system_evaluation_attempts_task_attempt"
        ),
    )
    op.create_index(
        "ix_system_evaluation_attempts_active_lease",
        "system_evaluation_attempts",
        ["status", "lease_expires_at"],
    )


def _create_result_and_event_tables() -> None:
    op.create_table(
        "system_evaluation_result_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "confidences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "missing_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("grade", sa.String(length=24), nullable=False),
        sa.Column("advice", sa.Text(), nullable=False),
        sa.Column("adapter_id", sa.String(length=80), nullable=True),
        sa.Column("adapter_version", sa.String(length=40), nullable=True),
        sa.Column("scoring_rule_version", sa.String(length=40), nullable=True),
        sa.Column("result_schema_version", sa.String(length=40), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "review_decision",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reviewed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previous_version_id", sa.UUID(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "version >= 1", name="ck_system_evaluation_results_version"
        ),
        sa.CheckConstraint(
            "source IN ('automatic', 'manual', 'correction', 'revision')",
            name="ck_system_evaluation_results_source",
        ),
        sa.CheckConstraint(
            "score BETWEEN 0 AND 100",
            name="ck_system_evaluation_results_score",
        ),
        sa.CheckConstraint(
            "btrim(grade) <> '' AND btrim(advice) <> ''",
            name="ck_system_evaluation_results_display_text",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(fields) = 'object' "
            "AND jsonb_typeof(confidences) = 'object' "
            "AND jsonb_typeof(warnings) = 'array' "
            "AND jsonb_typeof(components) = 'array' "
            "AND jsonb_typeof(missing_fields) = 'array'",
            name="ck_system_evaluation_results_structured",
        ),
        sa.CheckConstraint(
            "NOT jsonb_path_exists(warnings, "
            "'$[*] ? (@.type() != \"object\" || !exists (@.severity) || "
            "!(@.severity == \"info\" || @.severity == \"warning\" || "
            "@.severity == \"blocking\"))')",
            name="ck_system_evaluation_results_warning_severity",
        ),
        sa.CheckConstraint(
            "review_decision IN ('pending', 'approved', 'rejected') "
            "AND ((review_decision = 'pending' AND reviewed_by_user_id IS NULL "
            "AND reviewed_at IS NULL AND rejection_reason IS NULL "
            "AND published_at IS NULL) OR "
            "(review_decision = 'approved' AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND rejection_reason IS NULL "
            "AND published_at IS NOT NULL) OR "
            "(review_decision = 'rejected' AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND rejection_reason IS NOT NULL "
            "AND btrim(rejection_reason) <> '' AND published_at IS NULL))",
            name="ck_system_evaluation_results_review",
        ),
        sa.CheckConstraint(
            "(source = 'automatic' AND created_by_user_id IS NULL) OR "
            "(source <> 'automatic' AND created_by_user_id IS NOT NULL)",
            name="ck_system_evaluation_results_author",
        ),
        sa.CheckConstraint(
            "(adapter_id IS NULL AND adapter_version IS NULL "
            "AND scoring_rule_version IS NULL) OR "
            "(adapter_id IS NOT NULL AND adapter_version IS NOT NULL "
            "AND scoring_rule_version IS NOT NULL)",
            name="ck_system_evaluation_results_adapter",
        ),
        sa.CheckConstraint(
            "btrim(result_schema_version) <> ''",
            name="ck_system_evaluation_results_schema_version",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["system_evaluation_tasks.id"],
            name="fk_system_evaluation_results_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_results_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_results_reviewer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_version_id", "task_id"],
            [
                "system_evaluation_result_versions.id",
                "system_evaluation_result_versions.task_id",
            ],
            name="fk_system_evaluation_results_previous",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "task_id",
            name="uq_system_evaluation_results_id_task",
        ),
        sa.UniqueConstraint(
            "task_id", "version", name="uq_system_evaluation_results_task_version"
        ),
    )
    op.create_index(
        "ix_system_evaluation_results_task_created",
        "system_evaluation_result_versions",
        ["task_id", "created_at", "id"],
    )

    for constraint_name, local_columns in (
        (
            "fk_system_evaluation_tasks_current_result",
            ["current_result_version_id", "id"],
        ),
        (
            "fk_system_evaluation_tasks_published_result",
            ["published_result_version_id", "id"],
        ),
        (
            "fk_system_evaluation_tasks_revision_result",
            ["revision_of_version_id", "revision_of_task_id"],
        ),
    ):
        op.create_foreign_key(
            constraint_name,
            "system_evaluation_tasks",
            "system_evaluation_result_versions",
            local_columns,
            ["id", "task_id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "system_evaluation_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("result_version_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence >= 1", name="ck_system_evaluation_events_sequence"
        ),
        sa.CheckConstraint(
            "btrim(event_type) <> ''",
            name="ck_system_evaluation_events_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(event_metadata) = 'object'",
            name="ck_system_evaluation_events_metadata",
        ),
        sa.CheckConstraint(
            "(from_status IS NULL AND to_status IS NULL) OR "
            "(from_status IS NOT NULL AND to_status IS NOT NULL "
            f"AND from_status IN ({TASK_STATUSES}) "
            f"AND to_status IN ({TASK_STATUSES}) "
            "AND from_status <> to_status)",
            name="ck_system_evaluation_events_status",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["system_evaluation_tasks.id"],
            name="fk_system_evaluation_events_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_version_id", "task_id"],
            [
                "system_evaluation_result_versions.id",
                "system_evaluation_result_versions.task_id",
            ],
            name="fk_system_evaluation_events_result",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_events_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "sequence", name="uq_system_evaluation_events_task_sequence"
        ),
    )
    op.create_index(
        "ix_system_evaluation_events_task_time",
        "system_evaluation_events",
        ["task_id", "occurred_at", "id"],
    )


def _create_certification_and_idempotency_tables() -> None:
    op.create_table(
        "evaluation_adapter_certifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("adapter_id", sa.String(length=80), nullable=False),
        sa.Column("adapter_version", sa.String(length=40), nullable=False),
        sa.Column("scoring_rule_version", sa.String(length=40), nullable=False),
        sa.Column("result_schema_version", sa.String(length=40), nullable=False),
        sa.Column("manifest_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("certified_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "certified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_adapter_certifications_manifest",
        ),
        sa.CheckConstraint(
            "btrim(adapter_id) <> '' AND btrim(adapter_version) <> '' "
            "AND btrim(scoring_rule_version) <> '' "
            "AND btrim(result_schema_version) <> ''",
            name="ck_evaluation_adapter_certifications_versions",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            name="fk_evaluation_adapter_certifications_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["certified_by_user_id"],
            ["user_accounts.id"],
            name="fk_evaluation_adapter_certifications_certifier",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "adapter_id",
            "adapter_version",
            "scoring_rule_version",
            "result_schema_version",
            name="uq_evaluation_adapter_certifications_version",
        ),
    )
    op.create_index(
        "ix_evaluation_adapter_certifications_project_time",
        "evaluation_adapter_certifications",
        ["project_id", "certified_at", "id"],
    )

    op.create_table(
        "evaluation_idempotency_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("actor_key", sa.String(length=120), nullable=False),
        sa.Column("key_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("payload_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "key_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_idempotency_key_digest",
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_idempotency_payload_digest",
        ),
        sa.CheckConstraint(
            "btrim(scope) <> '' AND btrim(actor_key) <> ''",
            name="ck_evaluation_idempotency_scope_actor",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_evaluation_idempotency_expiry",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope",
            "actor_key",
            "key_digest",
            name="uq_evaluation_idempotency_scope_actor_key",
        ),
    )
    op.create_index(
        "ix_evaluation_idempotency_expiry",
        "evaluation_idempotency_keys",
        ["expires_at", "id"],
    )


def _create_immutability_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_system_evaluation_task_delete()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'system evaluation tasks cannot be deleted'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER system_evaluation_tasks_no_delete
        BEFORE DELETE ON system_evaluation_tasks
        FOR EACH ROW EXECUTE FUNCTION prevent_system_evaluation_task_delete()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_system_evaluation_task_target_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.public_number IS DISTINCT FROM NEW.public_number
               OR OLD.upload_session_id IS DISTINCT FROM NEW.upload_session_id
               OR OLD.created_by_user_id IS DISTINCT FROM NEW.created_by_user_id
               OR OLD.analysis_profile_id IS DISTINCT FROM NEW.analysis_profile_id
               OR OLD.analysis_profile_version IS DISTINCT FROM NEW.analysis_profile_version
               OR OLD.analysis_config_snapshot IS DISTINCT FROM NEW.analysis_config_snapshot
               OR OLD.upload_window_start IS DISTINCT FROM NEW.upload_window_start
               OR OLD.upload_window_end IS DISTINCT FROM NEW.upload_window_end
               OR OLD.revision_of_task_id IS DISTINCT FROM NEW.revision_of_task_id
               OR OLD.revision_of_version_id IS DISTINCT FROM NEW.revision_of_version_id THEN
                RAISE EXCEPTION 'system evaluation task identity is immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.project_id IS DISTINCT FROM NEW.project_id THEN
                RAISE EXCEPTION 'system evaluation task project is immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.student_person_id IS DISTINCT FROM NEW.student_person_id
               OR OLD.enrollment_id IS DISTINCT FROM NEW.enrollment_id
               OR OLD.cohort_id IS DISTINCT FROM NEW.cohort_id
               OR OLD.class_id IS DISTINCT FROM NEW.class_id THEN
                IF OLD.student_person_id IS NULL
                   AND OLD.enrollment_id IS NULL
                   AND OLD.cohort_id IS NULL
                   AND OLD.class_id IS NULL
                   AND NEW.student_person_id IS NOT NULL
                   AND NEW.enrollment_id IS NOT NULL
                   AND NEW.cohort_id IS NOT NULL
                   AND NEW.class_id IS NOT NULL THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'system evaluation task target is immutable after confirmation'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER system_evaluation_tasks_target_immutable
        BEFORE UPDATE ON system_evaluation_tasks
        FOR EACH ROW EXECUTE FUNCTION enforce_system_evaluation_task_target_immutable()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_system_evaluation_result_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'system evaluation result versions cannot be deleted'
                    USING ERRCODE = '55000';
            END IF;
            RAISE EXCEPTION 'system evaluation result versions are immutable'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER system_evaluation_results_immutable
        BEFORE UPDATE OR DELETE ON system_evaluation_result_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_system_evaluation_result_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_system_evaluation_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'system evaluation events are immutable'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER system_evaluation_events_immutable
        BEFORE UPDATE OR DELETE ON system_evaluation_events
        FOR EACH ROW EXECUTE FUNCTION prevent_system_evaluation_event_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_evaluation_adapter_certification_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evaluation adapter certifications are immutable'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluation_adapter_certifications_immutable
        BEFORE UPDATE OR DELETE ON evaluation_adapter_certifications
        FOR EACH ROW EXECUTE FUNCTION prevent_evaluation_adapter_certification_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_system_evaluation_task_media_target()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM system_evaluation_task_media AS link
                JOIN system_evaluation_tasks AS task ON task.id = link.task_id
                JOIN evaluation_media_assets AS media ON media.id = link.media_id
                WHERE ((TG_ARGV[0] = 'association' AND link.id = NEW.id)
                    OR (TG_ARGV[0] = 'task' AND link.task_id = NEW.id)
                    OR (TG_ARGV[0] = 'media' AND link.media_id = NEW.id))
                  AND (media.project_id IS DISTINCT FROM task.project_id
                    OR media.student_person_id IS DISTINCT FROM task.student_person_id
                    OR media.enrollment_id IS DISTINCT FROM task.enrollment_id
                    OR media.class_id IS DISTINCT FROM task.class_id)
            ) THEN
                RAISE EXCEPTION 'system evaluation task media target mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER system_evaluation_task_media_target_guard
        AFTER INSERT OR UPDATE ON system_evaluation_task_media
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_system_evaluation_task_media_target(
            'association'
        )
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER system_evaluation_task_media_task_target_guard
        AFTER UPDATE OF project_id, student_person_id, enrollment_id, class_id
        ON system_evaluation_tasks
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_system_evaluation_task_media_target('task')
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER system_evaluation_task_media_asset_target_guard
        AFTER UPDATE OF project_id, student_person_id, enrollment_id, class_id
        ON evaluation_media_assets
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_system_evaluation_task_media_target('media')
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_system_evaluation_task_media_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'system evaluation task media associations are immutable'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER system_evaluation_task_media_immutable
        BEFORE UPDATE OR DELETE ON system_evaluation_task_media
        FOR EACH ROW EXECUTE FUNCTION prevent_system_evaluation_task_media_mutation()
        """
    )


def _seed_permissions(bind: sa.Connection) -> None:
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    admin_role_id = bind.scalar(sa.text("SELECT id FROM roles WHERE key = 'admin'"))
    if admin_role_id is None:
        raise RuntimeError("cannot seed system evaluation without admin role")
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    for key, (permission_id, name, sensitive) in SYSTEM_EVALUATION_PERMISSIONS.items():
        bind.execute(
            permissions.insert().values(
                id=permission_id,
                key=key,
                name=name,
                sensitive=sensitive,
            )
        )
        bind.execute(
            role_permissions.insert().values(
                role_id=admin_role_id,
                permission_id=permission_id,
                scope_kind="global",
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight_upgrade(bind)
    _add_profile_and_target_contracts()
    _create_upload_tables()
    _create_task_table()
    _create_task_media_and_attempt_tables()
    _create_result_and_event_tables()
    _create_certification_and_idempotency_tables()
    _create_immutability_triggers()
    _seed_permissions(bind)


def _profiles_are_pristine(bind: sa.Connection) -> bool:
    return not bool(
        bind.scalar(
            sa.text(
                "SELECT "
                "(SELECT count(*) FROM project_analysis_profiles) "
                "<> (SELECT count(*) FROM game_projects) "
                "OR EXISTS ("
                "SELECT 1 FROM project_analysis_profiles "
                "WHERE version <> 1 OR mode <> 'manual_only' "
                "OR adapter_id IS NOT NULL OR adapter_version IS NOT NULL "
                "OR scoring_rule_version IS NOT NULL "
                "OR required_fields <> '[]'::jsonb "
                "OR media_slots <> '[]'::jsonb "
                "OR result_schema_version <> '1' "
                "OR confidence_thresholds <> '{}'::jsonb "
                "OR auto_publish_warning_levels <> '[]'::jsonb "
                "OR publish_mode <> 'review_required' "
                "OR configured_by_user_id IS NOT NULL "
                "OR change_reason <> 'migration backfill')"
            )
        )
    )


def _preflight_downgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE "
            + ", ".join(SYSTEM_EVALUATION_TABLES)
            + ", project_analysis_profiles, permissions, role_permissions, roles, "
            "user_permission_overrides, cohort_staff_assignment_permissions "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    business_checks = " OR ".join(
        f"EXISTS (SELECT 1 FROM {table_name})"
        for table_name in SYSTEM_EVALUATION_TABLES
    )
    if bind.scalar(sa.text(f"SELECT {business_checks}")):
        raise RuntimeError("cannot downgrade system evaluation with business data")
    if not _profiles_are_pristine(bind):
        raise RuntimeError(
            "cannot downgrade system evaluation with changed analysis profiles"
        )

    permission_ids = [value[0] for value in SYSTEM_EVALUATION_PERMISSIONS.values()]
    permission_keys = list(SYSTEM_EVALUATION_PERMISSIONS)
    permission_rows = set(
        bind.execute(
            sa.text(
                "SELECT id, key, name, sensitive FROM permissions "
                "WHERE id = ANY(CAST(:permission_ids AS uuid[])) "
                "OR key = ANY(CAST(:permission_keys AS text[]))"
            ),
            {"permission_ids": permission_ids, "permission_keys": permission_keys},
        ).tuples()
    )
    expected_permission_rows = {
        (permission_id, key, name, sensitive)
        for key, (permission_id, name, sensitive) in (
            SYSTEM_EVALUATION_PERMISSIONS.items()
        )
    }
    if permission_rows != expected_permission_rows:
        raise RuntimeError(
            "cannot downgrade system evaluation with customized permission"
        )

    grants = {
        grant
        for grant in _role_permission_grants(bind)
        if grant[1] in SYSTEM_EVALUATION_PERMISSIONS
    }
    expected_grants = {
        ("admin", permission_key, "global")
        for permission_key in SYSTEM_EVALUATION_PERMISSIONS
    }
    if grants != expected_grants:
        raise RuntimeError("cannot downgrade system evaluation with customized grant")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_permission_overrides "
            "WHERE permission_id = ANY(CAST(:permission_ids AS uuid[])))"
        ),
        {"permission_ids": permission_ids},
    ):
        raise RuntimeError("cannot downgrade system evaluation with custom overrides")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM cohort_staff_assignment_permissions "
            "WHERE permission_id = ANY(CAST(:permission_ids AS uuid[])))"
        ),
        {"permission_ids": permission_ids},
    ):
        raise RuntimeError("cannot downgrade system evaluation with custom assignments")


def _drop_immutability_triggers() -> None:
    op.execute(
        "DROP TRIGGER system_evaluation_task_media_asset_target_guard "
        "ON evaluation_media_assets"
    )
    op.execute(
        "DROP TRIGGER system_evaluation_task_media_task_target_guard "
        "ON system_evaluation_tasks"
    )
    op.execute(
        "DROP TRIGGER system_evaluation_task_media_target_guard "
        "ON system_evaluation_task_media"
    )
    op.execute("DROP FUNCTION enforce_system_evaluation_task_media_target()")
    op.execute(
        "DROP TRIGGER system_evaluation_task_media_immutable "
        "ON system_evaluation_task_media"
    )
    op.execute("DROP FUNCTION prevent_system_evaluation_task_media_mutation()")
    op.execute(
        "DROP TRIGGER evaluation_adapter_certifications_immutable "
        "ON evaluation_adapter_certifications"
    )
    op.execute("DROP FUNCTION prevent_evaluation_adapter_certification_mutation()")
    op.execute(
        "DROP TRIGGER system_evaluation_events_immutable ON system_evaluation_events"
    )
    op.execute("DROP FUNCTION prevent_system_evaluation_event_mutation()")
    op.execute(
        "DROP TRIGGER system_evaluation_results_immutable "
        "ON system_evaluation_result_versions"
    )
    op.execute("DROP FUNCTION prevent_system_evaluation_result_mutation()")
    op.execute(
        "DROP TRIGGER system_evaluation_tasks_target_immutable "
        "ON system_evaluation_tasks"
    )
    op.execute("DROP FUNCTION enforce_system_evaluation_task_target_immutable()")
    op.execute(
        "DROP TRIGGER system_evaluation_tasks_no_delete ON system_evaluation_tasks"
    )
    op.execute("DROP FUNCTION prevent_system_evaluation_task_delete()")


def _drop_tables() -> None:
    _drop_immutability_triggers()
    for constraint_name in (
        "fk_system_evaluation_tasks_current_result",
        "fk_system_evaluation_tasks_published_result",
        "fk_system_evaluation_tasks_revision_result",
    ):
        op.drop_constraint(
            constraint_name,
            "system_evaluation_tasks",
            type_="foreignkey",
        )
    op.drop_table("system_evaluation_events")
    op.drop_table("system_evaluation_attempts")
    op.drop_table("system_evaluation_task_media")
    op.drop_table("system_evaluation_result_versions")
    op.drop_table("system_evaluation_tasks")
    op.drop_table("system_evaluation_upload_session_media")
    op.drop_table("system_evaluation_upload_sessions")
    op.drop_table("evaluation_adapter_certifications")
    op.drop_table("evaluation_idempotency_keys")


def _remove_profile_and_target_contracts() -> None:
    op.drop_constraint(
        "uq_project_analysis_profiles_system_evaluation_identity",
        "project_analysis_profiles",
        type_="unique",
    )
    op.drop_constraint(
        "uq_enrollments_system_evaluation_target",
        "enrollments",
        type_="unique",
    )
    op.drop_constraint(
        "uq_teaching_classes_system_evaluation_target",
        "teaching_classes",
        type_="unique",
    )
    op.drop_constraint(
        "ck_project_analysis_profiles_manual_only",
        "project_analysis_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_project_analysis_profiles_result_schema_version",
        "project_analysis_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_project_analysis_profiles_media_slots",
        "project_analysis_profiles",
        type_="check",
    )
    op.drop_column("project_analysis_profiles", "result_schema_version")
    op.drop_column("project_analysis_profiles", "media_slots")
    op.create_check_constraint(
        "ck_project_analysis_profiles_manual_only",
        "project_analysis_profiles",
        "mode <> 'manual_only' OR "
        "(adapter_id IS NULL AND adapter_version IS NULL "
        "AND scoring_rule_version IS NULL AND required_fields = '[]'::jsonb "
        "AND confidence_thresholds = '{}'::jsonb "
        "AND auto_publish_warning_levels = '[]'::jsonb "
        "AND publish_mode = 'review_required')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    _preflight_downgrade(bind)
    permission_ids = [value[0] for value in SYSTEM_EVALUATION_PERMISSIONS.values()]
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE permission_id = ANY(CAST(:permission_ids AS uuid[]))"
        ),
        {"permission_ids": permission_ids},
    )
    bind.execute(
        sa.text(
            "DELETE FROM permissions "
            "WHERE id = ANY(CAST(:permission_ids AS uuid[]))"
        ),
        {"permission_ids": permission_ids},
    )
    _drop_tables()
    _remove_profile_and_target_contracts()
