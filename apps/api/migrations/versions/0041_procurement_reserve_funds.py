"""Add settlement-center reserve funds and purchase funding source."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_procurement_reserve_funds"
down_revision: str | Sequence[str] | None = "0040_procurement_reimbursement_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "procurement_reserve_fund_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("settlement_center_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount_cents > 0",
            name="ck_procurement_reserve_fund_entries_amount",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_center_id"],
            ["procurement_settlement_centers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_procurement_reserve_fund_entries_center_created",
        "procurement_reserve_fund_entries",
        ["settlement_center_id", "created_at", "id"],
    )
    op.add_column(
        "daily_purchase_records",
        sa.Column(
            "uses_reserve_fund",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_daily_purchase_records_reserve_fund_center",
        "daily_purchase_records",
        ["uses_reserve_fund", "settlement_center_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_daily_purchase_records_reserve_fund_center",
        table_name="daily_purchase_records",
    )
    op.drop_column("daily_purchase_records", "uses_reserve_fund")
    op.drop_index(
        "ix_procurement_reserve_fund_entries_center_created",
        table_name="procurement_reserve_fund_entries",
    )
    op.drop_table("procurement_reserve_fund_entries")
