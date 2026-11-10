"""Atomic quick personnel intake and housing success receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0089_quick_intake_housing"
down_revision = "0088_intake_role_intents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quick_intake_receipts",
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("operation_key", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("stay_id", sa.Uuid(), sa.ForeignKey("housing_stays.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("room_id", sa.Uuid(), sa.ForeignKey("housing_rooms.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("stay_status", sa.String(16), nullable=False),
        sa.Column("role_pending", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_quick_intake_fingerprint"),
        sa.CheckConstraint("stay_status IN ('planned','checked_in')", name="ck_quick_intake_status"),
    )


def downgrade() -> None:
    # Keep the empty guard and DROP indivisible with respect to new receipts.
    op.execute("LOCK TABLE quick_intake_receipts IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM quick_intake_receipts)")):
        raise RuntimeError("已有导入回执，禁止降级删除幂等记录；请先完成专项数据迁移")
    op.drop_table("quick_intake_receipts")
