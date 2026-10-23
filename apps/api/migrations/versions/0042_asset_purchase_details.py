"""Add purchase source and warranty details to assets."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_asset_purchase_details"
down_revision: str | Sequence[str] | None = "0041_procurement_reserve_funds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("purchase_date", sa.Date(), nullable=True))
    op.add_column("assets", sa.Column("warranty_expires_on", sa.Date(), nullable=True))
    op.add_column(
        "assets",
        sa.Column("source_purchase_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_assets_source_purchase_id_daily_purchase_records",
        "assets",
        "daily_purchase_records",
        ["source_purchase_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_assets_warranty_range",
        "assets",
        "purchase_date IS NULL OR warranty_expires_on IS NULL "
        "OR warranty_expires_on >= purchase_date",
    )
    op.create_index(
        "ix_assets_source_purchase",
        "assets",
        ["source_purchase_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assets_source_purchase", table_name="assets")
    op.drop_constraint("ck_assets_warranty_range", "assets", type_="check")
    op.drop_constraint(
        "fk_assets_source_purchase_id_daily_purchase_records",
        "assets",
        type_="foreignkey",
    )
    op.drop_column("assets", "source_purchase_id")
    op.drop_column("assets", "warranty_expires_on")
    op.drop_column("assets", "purchase_date")
