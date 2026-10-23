"""Add reviewed daily and weekly teaching reports.

Revision ID: 0014_teaching_reports
Revises: 0013_system_evaluation_manual_review
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_teaching_reports"
down_revision: str | Sequence[str] | None = "0013_system_evaluation_manual_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEACHING_REPORT_PERMISSIONS = {
    "student_reports.write": (
        UUID("20000000-0000-4000-8000-000000000032"),
        "填写学员日报和周报",
        True,
    ),
    "student_reports.review": (
        UUID("20000000-0000-4000-8000-000000000033"),
        "审核学员日报和周报",
        True,
    ),
}

TEACHING_REPORT_TABLES = (
    "student_report_drafts",
    "student_report_reviews",
    "student_report_window_grants",
    "student_report_versions",
    "student_reports",
)


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
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


def _preflight_upgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE permissions, role_permissions, roles "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    permission_ids = [value[0] for value in TEACHING_REPORT_PERMISSIONS.values()]
    permission_keys = list(TEACHING_REPORT_PERMISSIONS)
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM permissions "
            "WHERE id = ANY(CAST(:permission_ids AS uuid[])) "
            "OR key = ANY(CAST(:permission_keys AS text[])))"
        ),
        {"permission_ids": permission_ids, "permission_keys": permission_keys},
    ):
        raise RuntimeError(
            "cannot upgrade teaching reports with preexisting permission"
        )
    if bind.scalar(sa.text("SELECT id FROM roles WHERE key = 'admin'")) is None:
        raise RuntimeError("cannot upgrade teaching reports without admin role")


def _create_report_root() -> None:
    op.create_table(
        "student_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("student_person_id", sa.UUID(), nullable=False),
        sa.Column("cohort_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("author_person_id", sa.UUID(), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=False),
        sa.Column("author_source", sa.String(length=24), nullable=False),
        sa.Column("coach_assignment_id", sa.UUID(), nullable=True),
        sa.Column("staff_assignment_id", sa.UUID(), nullable=True),
        sa.Column("role_key_snapshot", sa.String(length=40), nullable=False),
        sa.Column("role_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("role_category_snapshot", sa.String(length=24), nullable=False),
        sa.Column("cohort_name_snapshot", sa.String(length=120), nullable=False),
        sa.Column("project_name_snapshot", sa.String(length=120), nullable=False),
        sa.Column("class_name_snapshot", sa.String(length=120), nullable=False),
        sa.Column("report_kind", sa.String(length=16), nullable=False),
        sa.Column("period_sequence", sa.Integer(), nullable=False),
        sa.Column("period_start_date", sa.Date(), nullable=False),
        sa.Column("period_end_date", sa.Date(), nullable=False),
        sa.Column("submission_opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "initial_submission_deadline",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("current_version_id", sa.UUID(), nullable=True),
        sa.Column("published_version_id", sa.UUID(), nullable=True),
        sa.Column("current_window_grant_id", sa.UUID(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correction_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "report_kind IN ('daily', 'weekly')",
            name="ck_student_reports_kind",
        ),
        sa.CheckConstraint(
            "period_sequence >= 1 AND period_end_date >= period_start_date "
            "AND ((report_kind = 'daily' "
            "AND period_end_date = period_start_date) "
            "OR (report_kind = 'weekly' "
            "AND period_end_date - period_start_date BETWEEN 0 AND 6))",
            name="ck_student_reports_period",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'rejected', "
            "'correction_draft', 'published', 'revision_pending_review')",
            name="ck_student_reports_status",
        ),
        sa.CheckConstraint(
            "lock_version >= 1",
            name="ck_student_reports_lock_version",
        ),
        sa.CheckConstraint(
            "btrim(role_key_snapshot) <> '' "
            "AND btrim(role_name_snapshot) <> '' "
            "AND role_category_snapshot IN "
            "('teaching', 'life_service', 'operations') "
            "AND btrim(cohort_name_snapshot) <> '' "
            "AND btrim(project_name_snapshot) <> '' "
            "AND btrim(class_name_snapshot) <> ''",
            name="ck_student_reports_snapshots",
        ),
        sa.CheckConstraint(
            "(author_source = 'coach_assignment' "
            "AND coach_assignment_id IS NOT NULL "
            "AND staff_assignment_id IS NULL) OR "
            "(author_source = 'staff_assignment' "
            "AND coach_assignment_id IS NULL "
            "AND staff_assignment_id IS NOT NULL)",
            name="ck_student_reports_assignment_source",
        ),
        sa.CheckConstraint(
            "submission_opens_at <= initial_submission_deadline "
            "AND (correction_due_at IS NULL "
            "OR correction_due_at >= submission_opens_at)",
            name="ck_student_reports_windows",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "((status = 'draft' AND submitted_at IS NULL) "
            "OR (status <> 'draft' AND submitted_at IS NOT NULL)) "
            "AND ((published_version_id IS NULL AND published_at IS NULL) "
            "OR (published_version_id IS NOT NULL AND published_at IS NOT NULL))",
            name="ck_student_reports_state_times",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["class_id", "cohort_id", "project_id"],
            [
                "teaching_classes.id",
                "teaching_classes.cohort_id",
                "teaching_classes.project_id",
            ],
            name="fk_student_reports_class_target",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            name="fk_student_reports_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["author_person_id"],
            ["people.id"],
            name="fk_student_reports_author_person",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["user_accounts.id"],
            name="fk_student_reports_author_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["coach_assignment_id"],
            ["coach_assignments.id"],
            name="fk_student_reports_coach_assignment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["staff_assignment_id"],
            ["cohort_staff_assignments.id"],
            name="fk_student_reports_staff_assignment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enrollment_id",
            "report_kind",
            "period_start_date",
            "author_person_id",
            name="uq_student_reports_author_period",
        ),
        sa.UniqueConstraint(
            "id",
            "enrollment_id",
            name="uq_student_reports_id_enrollment",
        ),
    )
    op.create_index(
        "ix_student_reports_author_work",
        "student_reports",
        ["author_person_id", "status", "period_start_date", "id"],
    )
    op.create_index(
        "ix_student_reports_review_queue",
        "student_reports",
        ["status", "cohort_id", "project_id", "class_id", "submitted_at", "id"],
    )
    op.create_index(
        "ix_student_reports_family_history",
        "student_reports",
        ["student_person_id", "published_at", "id"],
    )


def _create_report_drafts() -> None:
    op.create_table(
        "student_report_drafts",
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "dimension_values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("draft_kind", sa.String(length=16), nullable=False),
        sa.Column("draft_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_edited_by_user_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(btrim(body)) BETWEEN 1 AND 5000",
            name="ck_student_report_drafts_body",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(dimension_values) = 'object'",
            name="ck_student_report_drafts_dimensions",
        ),
        sa.CheckConstraint(
            "draft_kind IN ('initial', 'correction', 'revision')",
            name="ck_student_report_drafts_kind",
        ),
        sa.CheckConstraint(
            "draft_revision >= 1",
            name="ck_student_report_drafts_revision",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["student_reports.id"],
            name="fk_student_report_drafts_report",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_edited_by_user_id"],
            ["user_accounts.id"],
            name="fk_student_report_drafts_editor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("report_id"),
    )
    op.create_index(
        "ix_student_report_drafts_updated",
        "student_report_drafts",
        ["updated_at", "report_id"],
    )


def _create_report_versions() -> None:
    op.create_table(
        "student_report_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "dimension_values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("submitted_by_user_id", sa.UUID(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_version_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_student_report_versions_version",
        ),
        sa.CheckConstraint(
            "source IN ('initial', 'correction', 'revision')",
            name="ck_student_report_versions_source",
        ),
        sa.CheckConstraint(
            "char_length(btrim(body)) BETWEEN 1 AND 5000",
            name="ck_student_report_versions_body",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(dimension_values) = 'object'",
            name="ck_student_report_versions_dimensions",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["student_reports.id"],
            name="fk_student_report_versions_report",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["user_accounts.id"],
            name="fk_student_report_versions_submitter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_version_id", "report_id"],
            ["student_report_versions.id", "student_report_versions.report_id"],
            name="fk_student_report_versions_previous",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_id",
            "version",
            name="uq_student_report_versions_report_version",
        ),
        sa.UniqueConstraint(
            "id",
            "report_id",
            name="uq_student_report_versions_id_report",
        ),
    )
    op.create_index(
        "ix_student_report_versions_history",
        "student_report_versions",
        ["report_id", "version", "id"],
    )


def _create_report_reviews() -> None:
    op.create_table(
        "student_report_reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.UUID(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(decision = 'approved' AND reason IS NULL) OR "
            "(decision = 'rejected' AND reason IS NOT NULL "
            "AND char_length(btrim(reason)) BETWEEN 1 AND 2000)",
            name="ck_student_report_reviews_decision",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["student_reports.id"],
            name="fk_student_report_reviews_report",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "report_id"],
            ["student_report_versions.id", "student_report_versions.report_id"],
            name="fk_student_report_reviews_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["user_accounts.id"],
            name="fk_student_report_reviews_reviewer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "version_id",
            name="uq_student_report_reviews_version",
        ),
    )
    op.create_index(
        "ix_student_report_reviews_report_time",
        "student_report_reviews",
        ["report_id", "reviewed_at", "id"],
    )


def _create_report_window_grants() -> None:
    op.create_table(
        "student_report_window_grants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("grant_kind", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "grant_kind IN ('correction', 'revision')",
            name="ck_student_report_window_grants_kind",
        ),
        sa.CheckConstraint(
            "char_length(btrim(reason)) BETWEEN 1 AND 500",
            name="ck_student_report_window_grants_reason",
        ),
        sa.CheckConstraint(
            "expires_at > opens_at",
            name="ck_student_report_window_grants_window",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["student_reports.id"],
            name="fk_student_report_window_grants_report",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["user_accounts.id"],
            name="fk_student_report_window_grants_granter",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "report_id",
            name="uq_student_report_window_grants_id_report",
        ),
    )
    op.create_index(
        "ix_student_report_window_grants_report_time",
        "student_report_window_grants",
        ["report_id", "opens_at", "id"],
    )


def _add_report_pointer_foreign_keys() -> None:
    op.create_foreign_key(
        "fk_student_reports_current_version",
        "student_reports",
        "student_report_versions",
        ["current_version_id", "id"],
        ["id", "report_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_student_reports_published_version",
        "student_reports",
        "student_report_versions",
        ["published_version_id", "id"],
        ["id", "report_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_student_reports_window_grant",
        "student_reports",
        "student_report_window_grants",
        ["current_window_grant_id", "id"],
        ["id", "report_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def _create_immutability_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_teaching_report_history_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'teaching report history is immutable'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name, trigger_name in (
        ("student_report_versions", "student_report_versions_immutable"),
        ("student_report_reviews", "student_report_reviews_immutable"),
        (
            "student_report_window_grants",
            "student_report_window_grants_immutable",
        ),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} "
            f"BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_teaching_report_history_mutation()"
        )


def _seed_permissions(bind: sa.Connection) -> None:
    for key, (permission_id, name, sensitive) in TEACHING_REPORT_PERMISSIONS.items():
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, key, name, sensitive) "
                "VALUES (:id, :key, :name, :sensitive)"
            ),
            {
                "id": permission_id,
                "key": key,
                "name": name,
                "sensitive": sensitive,
            },
        )
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions "
                "(role_id, permission_id, scope_kind) "
                "SELECT id, :permission_id, 'global' FROM roles WHERE key = 'admin'"
            ),
            {"permission_id": permission_id},
        )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight_upgrade(bind)
    _create_report_root()
    _create_report_drafts()
    _create_report_versions()
    _create_report_reviews()
    _create_report_window_grants()
    _add_report_pointer_foreign_keys()
    _create_immutability_triggers()
    _seed_permissions(bind)


def _permission_rows_are_pristine(bind: sa.Connection) -> bool:
    expected = {
        (permission_id, key, name, sensitive)
        for key, (permission_id, name, sensitive) in TEACHING_REPORT_PERMISSIONS.items()
    }
    actual = set(
        bind.execute(
            sa.text(
                "SELECT id, key, name, sensitive FROM permissions "
                "WHERE id = ANY(CAST(:ids AS uuid[])) "
                "OR key = ANY(CAST(:keys AS text[]))"
            ),
            {
                "ids": [value[0] for value in TEACHING_REPORT_PERMISSIONS.values()],
                "keys": list(TEACHING_REPORT_PERMISSIONS),
            },
        ).tuples()
    )
    return actual == expected


def _grants_are_pristine(bind: sa.Connection) -> bool:
    actual = set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id "
                "WHERE permissions.key = ANY(CAST(:keys AS text[]))"
            ),
            {"keys": list(TEACHING_REPORT_PERMISSIONS)},
        ).tuples()
    )
    expected = {
        ("admin", permission_key, "global")
        for permission_key in TEACHING_REPORT_PERMISSIONS
    }
    return actual == expected


def _downgrade_preflight(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE student_report_drafts, student_report_reviews, "
            "student_report_window_grants, student_report_versions, "
            "student_reports, permissions, role_permissions, roles, "
            "user_permission_overrides, cohort_staff_assignment_permissions "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    if any(
        bind.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table_name})"))
        for table_name in TEACHING_REPORT_TABLES
    ):
        raise RuntimeError("cannot downgrade teaching reports with business data")
    if not _permission_rows_are_pristine(bind) or not _grants_are_pristine(bind):
        raise RuntimeError(
            "cannot downgrade teaching reports with customized permissions or grants"
        )
    permission_ids = [value[0] for value in TEACHING_REPORT_PERMISSIONS.values()]
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_permission_overrides "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[])) "
            "UNION ALL SELECT 1 FROM cohort_staff_assignment_permissions "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[])))"
        ),
        {"ids": permission_ids},
    ):
        raise RuntimeError(
            "cannot downgrade teaching reports with customized permissions or grants"
        )


def downgrade() -> None:
    bind = op.get_bind()
    _downgrade_preflight(bind)
    for constraint_name in (
        "fk_student_reports_window_grant",
        "fk_student_reports_published_version",
        "fk_student_reports_current_version",
    ):
        op.drop_constraint(constraint_name, "student_reports", type_="foreignkey")
    for table_name, trigger_name in (
        ("student_report_versions", "student_report_versions_immutable"),
        ("student_report_reviews", "student_report_reviews_immutable"),
        (
            "student_report_window_grants",
            "student_report_window_grants_immutable",
        ),
    ):
        op.execute(f"DROP TRIGGER {trigger_name} ON {table_name}")
    op.execute("DROP FUNCTION prevent_teaching_report_history_mutation()")
    op.drop_table("student_report_reviews")
    op.drop_table("student_report_window_grants")
    op.drop_table("student_report_versions")
    op.drop_table("student_report_drafts")
    op.drop_table("student_reports")
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": [value[0] for value in TEACHING_REPORT_PERMISSIONS.values()]},
    )
    bind.execute(
        sa.text(
            "DELETE FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": [value[0] for value in TEACHING_REPORT_PERMISSIONS.values()]},
    )
