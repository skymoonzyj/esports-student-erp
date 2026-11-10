"""Independent, historical read-only team staff assignments."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0085_team_staff_assignments"
down_revision = "0084_team_staff_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_staff_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("professional_teams.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("staff_role", sa.String(24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column(
            "ended_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        ),
        sa.Column("end_kind", sa.String(24)),
        sa.Column(
            "open_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_operations.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "close_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_operations.id", ondelete="RESTRICT"),
        ),
        sa.CheckConstraint(
            "staff_role IN ('manager', 'coach', 'assistant_coach')",
            name="ck_team_staff_assignments_role",
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL AND close_operation_id IS NULL AND end_kind IS NULL) OR (ended_at IS NOT NULL AND end_kind IS NOT NULL AND ended_at >= created_at)",
            name="ck_team_staff_assignments_end_pair",
        ),
        sa.CheckConstraint(
            "end_kind IS NULL OR end_kind IN ('unassigned', 'role_removed', 'account_deleted')",
            name="ck_team_staff_assignments_end_kind",
        ),
    )
    op.create_index(
        "uq_team_staff_assignments_open_account_team_role",
        "team_staff_assignments",
        ["account_id", "team_id", "staff_role"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_team_staff_assignments_account_history",
        "team_staff_assignments",
        ["account_id", "created_at", "id"],
    )
    op.create_index(
        "ix_team_staff_assignments_team_history",
        "team_staff_assignments",
        ["team_id", "created_at", "id"],
    )
    # team_operations.action was intentionally unconstrained before this revision;
    # team_staff.assign / team_staff.unassign fit its existing String(32) domain.


def downgrade() -> None:
    connection = op.get_bind()
    # Hold the history and operation tables against concurrent writes through
    # the check and DDL. No ended rows or idempotency history may be discarded.
    connection.execute(
        sa.text("LOCK TABLE team_staff_assignments, team_operations IN ACCESS EXCLUSIVE MODE")
    )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM team_staff_assignments) OR EXISTS (SELECT 1 FROM team_operations WHERE action IN ('team_staff.assign', 'team_staff.unassign'))"
        )
    ):
        raise RuntimeError("存在工作人员岗位历史，拒绝降级以免丢失数据")
    op.drop_table("team_staff_assignments")
