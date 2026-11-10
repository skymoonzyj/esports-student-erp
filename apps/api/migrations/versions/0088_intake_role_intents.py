"""Separate direct personnel creation from pending account role authorization."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0088_intake_role_intents"
down_revision = "0087_intake_identity_teams"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("intake_role_intents",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column("person_id", pg.UUID(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("origin_approval_id", pg.UUID(), nullable=False),
        sa.Column("created_by_user_id", pg.UUID(), sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role_keys", pg.ARRAY(sa.String(64)), nullable=False),
        sa.Column("position_evidence", pg.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("intent_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["origin_approval_id", "person_id"], ["assisted_approvals.id", "assisted_approvals.person_id"], ondelete="RESTRICT", name="fk_role_intent_approval_person"),
        sa.UniqueConstraint("person_id", name="uq_role_intent_person"),
        sa.CheckConstraint("version = 1", name="ck_role_intent_version"),
        sa.CheckConstraint("cardinality(role_keys) > 0 AND NOT (role_keys && ARRAY['admin','super_admin','finance']::varchar[])", name="ck_role_intent_roles"),
        sa.CheckConstraint("intent_digest ~ '^[0-9a-f]{64}$'", name="ck_role_intent_digest"),
    )
    op.create_table("intake_role_decisions",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column("intent_id", pg.UUID(), sa.ForeignKey("intake_role_intents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_by_user_id", pg.UUID(), sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role_plan_id", pg.UUID(), sa.ForeignKey("intake_activation_role_plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_key", pg.UUID(), nullable=False),
        sa.Column("operation_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("intent_id", name="uq_role_decision_intent"),
        sa.UniqueConstraint("approved_by_user_id", "operation_key", name="uq_role_decision_operation"),
        sa.CheckConstraint("operation_digest ~ '^[0-9a-f]{64}$'", name="ck_role_decision_digest"),
    )
    for table in ("intake_role_intents", "intake_role_decisions"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_intake_identity_evidence_mutation()")
    # Add only intake authority; existing user overrides (including deny) stay untouched.
    op.execute("""INSERT INTO role_permissions(role_id,permission_id,scope_kind)
        SELECT r.id,p.id,'global' FROM roles r CROSS JOIN permissions p
        WHERE r.key='administration' AND p.key='personnel_intake.review'
        ON CONFLICT DO NOTHING""")


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("LOCK TABLE intake_role_intents,intake_role_decisions IN ACCESS EXCLUSIVE MODE"))
    if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM intake_role_intents)")):
        raise RuntimeError("待授权角色已有历史，拒绝降级删除")
    # Permission provenance is unknown: never remove pre-existing or custom grants.
    op.drop_table("intake_role_decisions")
    op.drop_table("intake_role_intents")
