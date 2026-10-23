"""Persist talent test sessions, results, reports, and least-privilege grants."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058_talent_tests"
down_revision: str | Sequence[str] | None = "0057_procurement_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAKE_PERMISSION_ID = "20000000-0000-4000-8000-000000000078"
READ_PERMISSION_ID = "20000000-0000-4000-8000-000000000079"
PERMISSION_KEYS = ("talent_tests.take", "talent_tests.read")
GRANTS = (
    ("super_admin", "talent_tests.take", "self"),
    ("super_admin", "talent_tests.read", "global"),
    ("student", "talent_tests.take", "self"),
    ("student", "talent_tests.read", "self"),
    ("guardian", "talent_tests.read", "family"),
    ("coach", "talent_tests.read", "assigned_class"),
)


def _seed_permissions() -> None:
    op.execute(
        sa.text(
            "INSERT INTO permissions (id, key, name, sensitive) VALUES "
            "(CAST(:take_id AS uuid), 'talent_tests.take', "
            "'参加天赋训练测试', false), "
            "(CAST(:read_id AS uuid), 'talent_tests.read', "
            "'查看天赋训练测试报告', true) "
            "ON CONFLICT (key) DO UPDATE SET "
            "name = EXCLUDED.name, sensitive = EXCLUDED.sensitive"
        ).bindparams(take_id=TAKE_PERMISSION_ID, read_id=READ_PERMISSION_ID)
    )
    for role_key, permission_key, scope_kind in GRANTS:
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
        "talent_test_sessions",
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
        sa.Column("creation_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creation_request_digest", sa.String(64), nullable=False),
        sa.Column("abandon_request_id", postgresql.UUID(as_uuid=True)),
        sa.Column("abandon_request_digest", sa.String(64)),
        sa.Column("catalog_version", sa.String(64), nullable=False),
        sa.Column("scoring_version", sa.String(64), nullable=False),
        sa.Column("report_version", sa.String(64), nullable=False),
        sa.Column("rules_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="in_progress",
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
            "person_id",
            "creation_key",
            name="uq_talent_sessions_person_creation",
        ),
        sa.CheckConstraint(
            "(status = 'in_progress' AND completed_at IS NULL AND abandoned_at IS NULL) "
            "OR (status = 'completed' AND completed_at IS NOT NULL "
            "AND abandoned_at IS NULL) "
            "OR (status = 'abandoned' AND completed_at IS NULL "
            "AND abandoned_at IS NOT NULL)",
            name="ck_talent_sessions_state_times",
        ),
        sa.CheckConstraint(
            "(status = 'abandoned' AND abandon_request_id IS NOT NULL "
            "AND abandon_request_digest IS NOT NULL "
            "AND abandon_request_digest ~ '^[0-9a-f]{64}$') "
            "OR (status IN ('in_progress', 'completed') "
            "AND abandon_request_id IS NULL AND abandon_request_digest IS NULL)",
            name="ck_talent_sessions_abandon_pair",
        ),
        sa.CheckConstraint(
            "creation_request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_talent_sessions_creation_digest",
        ),
    )
    op.create_index(
        "uq_talent_sessions_active_person",
        "talent_test_sessions",
        ["person_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.create_index(
        "ix_talent_sessions_person_created",
        "talent_test_sessions",
        ["person_id", "created_at", "id"],
    )

    op.create_table(
        "talent_test_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("talent_test_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("test_id", sa.String(32), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=False),
        sa.Column("locale", sa.String(16), nullable=False),
        sa.Column("raw_result", postgresql.JSONB(), nullable=False),
        sa.Column("device_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_result", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("background_interrupted", sa.Boolean(), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "session_id",
            "test_id",
            name="uq_talent_results_session_test",
        ),
        sa.UniqueConstraint(
            "session_id",
            "submission_id",
            name="uq_talent_results_session_submission",
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_talent_results_score",
        ),
        sa.CheckConstraint(
            "test_id IN ('reactiontime', 'aim', 'sequence', 'number-memory', "
            "'verbal-memory', 'chimp', 'memory')",
            name="ck_talent_results_test_id",
        ),
        sa.CheckConstraint(
            "submission_digest ~ '^[0-9a-f]{64}$'",
            name="ck_talent_results_submission_digest",
        ),
    )

    op.create_table(
        "talent_test_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("talent_test_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("report_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("session_id", name="uq_talent_reports_session"),
    )
    op.create_index(
        "ix_talent_reports_created",
        "talent_test_reports",
        ["created_at", "id"],
    )

    _seed_permissions()


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM user_permission_overrides WHERE permission_id IN "
            "(SELECT id FROM permissions "
            "WHERE key = ANY(CAST(:permission_keys AS text[])))"
        ).bindparams(permission_keys=list(PERMISSION_KEYS))
    )
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions "
            "WHERE key = ANY(CAST(:permission_keys AS text[])))"
        ).bindparams(permission_keys=list(PERMISSION_KEYS))
    )
    op.execute(
        sa.text(
            "DELETE FROM permissions WHERE key = ANY(CAST(:permission_keys AS text[]))"
        ).bindparams(permission_keys=list(PERMISSION_KEYS))
    )

    op.drop_index("ix_talent_reports_created", table_name="talent_test_reports")
    op.drop_table("talent_test_reports")
    op.drop_table("talent_test_results")
    op.drop_index("ix_talent_sessions_person_created", table_name="talent_test_sessions")
    op.drop_index("uq_talent_sessions_active_person", table_name="talent_test_sessions")
    op.drop_table("talent_test_sessions")
