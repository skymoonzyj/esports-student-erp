"""Preserve versioned activation role authorization separately from identity evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0086_intake_activation_roles"
down_revision = "0085_team_staff_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intake_activation_role_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("origin_approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "authorizer_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role_keys", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("operation_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_digest", sa.String(64), nullable=False),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["origin_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            ondelete="RESTRICT",
            name="fk_activation_role_plan_approval_person",
        ),
        sa.UniqueConstraint("person_id", "version", name="uq_activation_role_plan_version"),
        sa.UniqueConstraint(
            "authorizer_user_id", "operation_key", name="uq_activation_role_plan_operation"
        ),
        sa.CheckConstraint("version >= 1", name="ck_activation_role_plan_version"),
        sa.CheckConstraint(
            "operation_digest ~ '^[0-9a-f]{64}$' AND plan_digest ~ '^[0-9a-f]{64}$'",
            name="ck_activation_role_plan_digests",
        ),
        sa.CheckConstraint(
            "NOT (role_keys && ARRAY['admin', 'super_admin']::varchar[])",
            name="ck_activation_role_plan_non_admin",
        ),
    )
    op.create_index(
        "ix_activation_role_plan_authorizer",
        "intake_activation_role_plans",
        ["authorizer_user_id", "person_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE intake_activation_role_plans IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM intake_activation_role_plans)")):
        raise RuntimeError("激活角色授权已有历史，拒绝降级删除")
    op.drop_table("intake_activation_role_plans")
