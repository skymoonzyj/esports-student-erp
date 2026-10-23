"""Persist independent esports assessment sessions, attempts, results, reports, and practice."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060_esports_assessments"
down_revision: str | Sequence[str] | None = "0059_talent_dynamic_vision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAKE_PERMISSION_ID = "20000000-0000-4000-8000-000000000080"
READ_PERMISSION_ID = "20000000-0000-4000-8000-000000000081"
PERMISSION_KEYS = ("esports_assessments.take", "esports_assessments.read")
DEFAULT_GRANTS = (
    ("super_admin", "esports_assessments.take", "self"),
    ("super_admin", "esports_assessments.read", "global"),
    ("student", "esports_assessments.take", "self"),
    ("student", "esports_assessments.read", "self"),
    ("guardian", "esports_assessments.read", "family"),
    ("coach", "esports_assessments.read", "assigned_class"),
)
BUSINESS_TABLES = (
    "esports_assessment_sessions",
    "esports_assessment_attempts",
    "esports_assessment_results",
    "esports_assessment_reports",
    "esports_assessment_practice_runs",
)

_SUITE_CHECK = "suite_id IN ('pc-fps', 'mobile-fps', 'pc-moba', 'mobile-moba')"
_RUN_STATE_CHECK = (
    "state IN ('running', 'awaiting_submission', 'interrupted', 'expired', 'submitted')"
)


def _seed_permissions() -> None:
    op.execute(
        sa.text(
            "INSERT INTO permissions (id, key, name, sensitive) VALUES "
            "(CAST(:take_id AS uuid), 'esports_assessments.take', "
            "'参加天赋评测 2', false), "
            "(CAST(:read_id AS uuid), 'esports_assessments.read', "
            "'查看天赋评测 2 报告', true)"
        ).bindparams(take_id=TAKE_PERMISSION_ID, read_id=READ_PERMISSION_ID)
    )
    for role_key, permission_key, scope_kind in DEFAULT_GRANTS:
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT roles.id, permissions.id, :scope_kind "
                "FROM roles CROSS JOIN permissions "
                "WHERE roles.key = :role_key AND permissions.key = :permission_key "
                "ON CONFLICT DO NOTHING"
            ).bindparams(
                role_key=role_key,
                permission_key=permission_key,
                scope_kind=scope_kind,
            )
        )


def upgrade() -> None:
    op.create_table(
        "esports_assessment_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("suite_id", sa.String(32), nullable=False),
        sa.Column("creation_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creation_request_digest", sa.String(64), nullable=False),
        sa.Column("abandon_key", postgresql.UUID(as_uuid=True)),
        sa.Column("abandon_request_digest", sa.String(64)),
        sa.Column("catalog_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("scoring_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("report_rules_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="in_progress"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("abandoned_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "created_by_user_id",
            "creation_key",
            name="uq_ea_session_creator_creation",
        ),
        sa.UniqueConstraint(
            "created_by_user_id",
            "abandon_key",
            name="uq_ea_session_creator_abandon",
        ),
        sa.CheckConstraint(_SUITE_CHECK, name="ck_ea_session_suite"),
        sa.CheckConstraint(
            "(status = 'in_progress' AND completed_at IS NULL AND abandoned_at IS NULL) "
            "OR (status = 'completed' AND completed_at IS NOT NULL "
            "AND abandoned_at IS NULL) "
            "OR (status = 'abandoned' AND completed_at IS NULL "
            "AND abandoned_at IS NOT NULL)",
            name="ck_ea_session_state_times",
        ),
        sa.CheckConstraint(
            "creation_request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ea_session_creation_digest",
        ),
        sa.CheckConstraint(
            "(abandon_key IS NULL AND abandon_request_digest IS NULL) OR "
            "(abandon_key IS NOT NULL "
            "AND abandon_request_digest ~ '^[0-9a-f]{64}$')",
            name="ck_ea_session_abandon_pair",
        ),
    )
    op.create_index(
        "uq_ea_open_person_suite",
        "esports_assessment_sessions",
        ["person_id", "suite_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.create_index(
        "ix_ea_session_person_suite_status_created",
        "esports_assessment_sessions",
        ["person_id", "suite_id", "status", "created_at", "id"],
    )

    op.create_table(
        "esports_assessment_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("esports_assessment_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("test_id", sa.String(32), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("settings_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("environment_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="running"),
        sa.Column("creation_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creation_request_digest", sa.String(64), nullable=False),
        sa.Column("interrupt_key", postgresql.UUID(as_uuid=True)),
        sa.Column("run_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submit_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("interrupted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "id", "session_id", "test_id", name="uq_ea_attempt_identity"
        ),
        sa.UniqueConstraint(
            "created_by_user_id",
            "creation_key",
            name="uq_ea_attempt_creator_creation",
        ),
        sa.UniqueConstraint(
            "created_by_user_id",
            "interrupt_key",
            name="uq_ea_attempt_creator_interrupt",
        ),
        sa.UniqueConstraint(
            "session_id",
            "test_id",
            "sequence_no",
            name="uq_ea_attempt_sequence",
        ),
        sa.CheckConstraint(_RUN_STATE_CHECK, name="ck_ea_attempt_state"),
        sa.CheckConstraint(
            "(state = 'submitted' AND submitted_at IS NOT NULL "
            "AND interrupted_at IS NULL) "
            "OR (state = 'interrupted' AND submitted_at IS NULL "
            "AND interrupted_at IS NOT NULL) "
            "OR (state IN ('running', 'awaiting_submission', 'expired') "
            "AND submitted_at IS NULL AND interrupted_at IS NULL)",
            name="ck_ea_attempt_state_times",
        ),
        sa.CheckConstraint(
            "run_until > created_at AND submit_until > run_until",
            name="ck_ea_attempt_deadlines",
        ),
        sa.CheckConstraint(
            "seed BETWEEN 0 AND 4294967295", name="ck_ea_attempt_seed"
        ),
        sa.CheckConstraint(
            "creation_request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ea_attempt_creation_digest",
        ),
    )
    op.create_index(
        "uq_ea_attempt_submittable_session_test",
        "esports_assessment_attempts",
        ["session_id", "test_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('running', 'awaiting_submission')"),
    )
    op.create_index(
        "ix_ea_attempt_creator_state_deadline",
        "esports_assessment_attempts",
        ["created_by_user_id", "state", "run_until", "id"],
    )

    op.create_table(
        "esports_assessment_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_id", sa.String(32), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("submission_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_request_digest", sa.String(64), nullable=False),
        sa.Column("raw_result", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("grade", sa.String(16), nullable=False),
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False),
        sa.Column("comparison_key", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "session_id", "test_id"],
            [
                "esports_assessment_attempts.id",
                "esports_assessment_attempts.session_id",
                "esports_assessment_attempts.test_id",
            ],
            name="fk_ea_result_attempt_owner",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "session_id", "test_id", name="uq_ea_result_session_test"
        ),
        sa.UniqueConstraint("attempt_id", name="uq_ea_result_attempt"),
        sa.UniqueConstraint(
            "created_by_user_id",
            "submission_key",
            name="uq_ea_result_creator_submission",
        ),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name="ck_ea_result_score"),
        sa.CheckConstraint(
            "grade IN ('low', 'medium', 'high', 'exceptional')",
            name="ck_ea_result_grade",
        ),
        sa.CheckConstraint(
            "submission_request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ea_result_submission_digest",
        ),
        sa.CheckConstraint(
            "comparison_key ~ '^[0-9a-f]{64}$'",
            name="ck_ea_result_comparison_key",
        ),
    )
    op.create_index(
        "ix_ea_result_comparison_created",
        "esports_assessment_results",
        ["test_id", "comparison_key", "created_at", "id"],
    )

    op.create_table(
        "esports_assessment_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("esports_assessment_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("report_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("session_id", name="uq_ea_report_session"),
    )
    op.create_index(
        "ix_ea_report_created",
        "esports_assessment_reports",
        ["created_at", "id"],
    )

    op.create_table(
        "esports_assessment_practice_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("suite_id", sa.String(32), nullable=False),
        sa.Column("test_id", sa.String(32), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("settings_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("environment_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="running"),
        sa.Column("creation_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creation_request_digest", sa.String(64), nullable=False),
        sa.Column("submission_key", postgresql.UUID(as_uuid=True)),
        sa.Column("submission_request_digest", sa.String(64)),
        sa.Column("interrupt_key", postgresql.UUID(as_uuid=True)),
        sa.Column("run_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submit_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_result", postgresql.JSONB()),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("quality_flags", postgresql.JSONB()),
        sa.Column("comparison_key", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("interrupted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "created_by_user_id",
            "creation_key",
            name="uq_ea_practice_creator_creation",
        ),
        sa.UniqueConstraint(
            "created_by_user_id",
            "submission_key",
            name="uq_ea_practice_creator_submission",
        ),
        sa.UniqueConstraint(
            "created_by_user_id",
            "interrupt_key",
            name="uq_ea_practice_creator_interrupt",
        ),
        sa.CheckConstraint(_SUITE_CHECK, name="ck_ea_practice_suite"),
        sa.CheckConstraint(
            "difficulty BETWEEN 1 AND 5", name="ck_ea_practice_difficulty"
        ),
        sa.CheckConstraint(_RUN_STATE_CHECK, name="ck_ea_practice_state"),
        sa.CheckConstraint(
            "(state = 'submitted' AND submitted_at IS NOT NULL "
            "AND interrupted_at IS NULL AND submission_key IS NOT NULL "
            "AND submission_request_digest IS NOT NULL AND raw_result IS NOT NULL "
            "AND metrics IS NOT NULL AND quality_flags IS NOT NULL "
            "AND comparison_key IS NOT NULL) "
            "OR (state = 'interrupted' AND submitted_at IS NULL "
            "AND interrupted_at IS NOT NULL) "
            "OR (state IN ('running', 'awaiting_submission', 'expired') "
            "AND submitted_at IS NULL AND interrupted_at IS NULL)",
            name="ck_ea_practice_state_times",
        ),
        sa.CheckConstraint(
            "run_until > created_at AND submit_until > run_until",
            name="ck_ea_practice_deadlines",
        ),
        sa.CheckConstraint(
            "seed BETWEEN 0 AND 4294967295", name="ck_ea_practice_seed"
        ),
        sa.CheckConstraint(
            "creation_request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ea_practice_creation_digest",
        ),
        sa.CheckConstraint(
            "(submission_key IS NULL AND submission_request_digest IS NULL) OR "
            "(submission_key IS NOT NULL "
            "AND submission_request_digest ~ '^[0-9a-f]{64}$')",
            name="ck_ea_practice_submission_pair",
        ),
        sa.CheckConstraint(
            "comparison_key IS NULL OR comparison_key ~ '^[0-9a-f]{64}$'",
            name="ck_ea_practice_comparison_key",
        ),
    )
    op.create_index(
        "ix_ea_practice_person_suite_state_created",
        "esports_assessment_practice_runs",
        ["person_id", "suite_id", "state", "created_at", "id"],
    )
    op.create_index(
        "ix_ea_practice_creator_state_deadline",
        "esports_assessment_practice_runs",
        ["created_by_user_id", "state", "run_until", "id"],
    )
    op.create_index(
        "ix_ea_practice_comparison_created",
        "esports_assessment_practice_runs",
        [
            "person_id",
            "suite_id",
            "test_id",
            "comparison_key",
            "created_at",
            "id",
        ],
    )

    _seed_permissions()


def downgrade() -> None:
    op.execute(
        sa.text(
            "LOCK TABLE esports_assessment_sessions, esports_assessment_attempts, "
            "esports_assessment_results, esports_assessment_reports, "
            "esports_assessment_practice_runs, permissions, role_permissions, "
            "user_permission_overrides IN ACCESS EXCLUSIVE MODE"
        )
    )
    has_business_data = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM esports_assessment_sessions) "
            "OR EXISTS(SELECT 1 FROM esports_assessment_attempts) "
            "OR EXISTS(SELECT 1 FROM esports_assessment_results) "
            "OR EXISTS(SELECT 1 FROM esports_assessment_reports) "
            "OR EXISTS(SELECT 1 FROM esports_assessment_practice_runs)"
        )
    )
    if has_business_data:
        raise RuntimeError("存在天赋评测 2 业务数据，拒绝降级；未删除任何记录")

    has_overrides = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS("
            "SELECT 1 FROM user_permission_overrides upo "
            "JOIN permissions p ON p.id = upo.permission_id "
            "WHERE p.key = ANY(CAST(:keys AS text[])))"
        ),
        {"keys": list(PERMISSION_KEYS)},
    )
    if has_overrides:
        raise RuntimeError("存在天赋评测 2 用户授权，拒绝降级；未删除任何授权")

    existing_grants = set(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT r.key, p.key, rp.scope_kind FROM role_permissions rp "
                "JOIN roles r ON r.id = rp.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE p.key = ANY(CAST(:keys AS text[]))"
            ),
            {"keys": list(PERMISSION_KEYS)},
        )
        .tuples()
    )
    if existing_grants - set(DEFAULT_GRANTS):
        raise RuntimeError("存在天赋评测 2 非默认角色授权，拒绝降级；未删除任何授权")

    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE key = ANY(CAST(:keys AS text[])))"
        ).bindparams(keys=list(PERMISSION_KEYS))
    )
    op.execute(
        sa.text(
            "DELETE FROM permissions WHERE key = ANY(CAST(:keys AS text[]))"
        ).bindparams(keys=list(PERMISSION_KEYS))
    )

    op.drop_index(
        "ix_ea_practice_comparison_created",
        table_name="esports_assessment_practice_runs",
    )
    op.drop_index(
        "ix_ea_practice_creator_state_deadline",
        table_name="esports_assessment_practice_runs",
    )
    op.drop_index(
        "ix_ea_practice_person_suite_state_created",
        table_name="esports_assessment_practice_runs",
    )
    op.drop_table("esports_assessment_practice_runs")
    op.drop_index("ix_ea_report_created", table_name="esports_assessment_reports")
    op.drop_table("esports_assessment_reports")
    op.drop_index(
        "ix_ea_result_comparison_created", table_name="esports_assessment_results"
    )
    op.drop_table("esports_assessment_results")
    op.drop_index(
        "ix_ea_attempt_creator_state_deadline",
        table_name="esports_assessment_attempts",
    )
    op.drop_index(
        "uq_ea_attempt_submittable_session_test",
        table_name="esports_assessment_attempts",
    )
    op.drop_table("esports_assessment_attempts")
    op.drop_index(
        "ix_ea_session_person_suite_status_created",
        table_name="esports_assessment_sessions",
    )
    op.drop_index(
        "uq_ea_open_person_suite", table_name="esports_assessment_sessions"
    )
    op.drop_table("esports_assessment_sessions")
