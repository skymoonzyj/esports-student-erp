"""Add purchase channels and optional online product links."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_procurement_product_links"
down_revision: str | Sequence[str] | None = "0038_admin_finance_boundary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_purchase_records",
        sa.Column(
            "purchase_channel",
            sa.String(length=16),
            server_default="offline",
            nullable=False,
        ),
    )
    op.add_column(
        "daily_purchase_records",
        sa.Column("product_url", sa.String(length=2048), nullable=True),
    )
    op.create_check_constraint(
        "ck_daily_purchase_records_purchase_channel",
        "daily_purchase_records",
        "purchase_channel IN ('online', 'offline')",
    )
    op.create_check_constraint(
        "ck_daily_purchase_records_product_url",
        "daily_purchase_records",
        "(purchase_channel = 'offline' AND product_url IS NULL) OR "
        "(purchase_channel = 'online' AND (product_url IS NULL OR "
        "char_length(product_url) BETWEEN 1 AND 2048))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_daily_purchase_records_product_url",
        "daily_purchase_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_daily_purchase_records_purchase_channel",
        "daily_purchase_records",
        type_="check",
    )
    op.drop_column("daily_purchase_records", "product_url")
    op.drop_column("daily_purchase_records", "purchase_channel")
