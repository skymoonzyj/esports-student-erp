"""Add nullable computer hardware and actor-scoped rental batch operations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0076_asset_batch_hardware"
down_revision = "0075_global_appearance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, size in (
        ("cpu_model", 120),
        ("memory_capacity", 80),
        ("gpu_model", 120),
        ("disk_capacity", 120),
    ):
        op.add_column("assets", sa.Column(name, sa.String(size), nullable=True))
    op.create_table(
        "asset_batch_operations",
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("operation_key", UUID(as_uuid=True), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("asset_ids", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_asset_batch_hash"),
        sa.CheckConstraint("quantity BETWEEN 1 AND 100", name="ck_asset_batch_quantity"),
        sa.CheckConstraint(
            "jsonb_typeof(asset_ids) = 'array' AND jsonb_array_length(asset_ids) = quantity",
            name="ck_asset_batch_result",
        ),
    )


def downgrade() -> None:
    # Retain both entered facts and retry evidence. Serializing the guard with
    # writers prevents a late successful batch from being silently discarded.
    op.execute("LOCK TABLE assets, asset_batch_operations IN ACCESS EXCLUSIVE MODE")
    retained = op.get_bind().scalar(
        sa.text("""SELECT
        EXISTS (SELECT 1 FROM asset_batch_operations)
        OR EXISTS (SELECT 1 FROM assets WHERE cpu_model IS NOT NULL
            OR memory_capacity IS NOT NULL OR gpu_model IS NOT NULL OR disk_capacity IS NOT NULL)
    """)
    )
    if retained:
        raise RuntimeError("cannot downgrade with retained asset batch or hardware evidence")
    op.drop_table("asset_batch_operations")
    for name in ("disk_capacity", "gpu_model", "memory_capacity", "cpu_model"):
        op.drop_column("assets", name)
