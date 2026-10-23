"""Add independent reimbursement status to daily purchases."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_procurement_reimbursement_status"
down_revision: str | Sequence[str] | None = "0039_procurement_product_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_purchase_records",
        sa.Column(
            "is_reimbursed",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_daily_purchase_records_reimbursed_date",
        "daily_purchase_records",
        ["is_reimbursed", "purchase_date", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_daily_purchase_records_reimbursed_date",
        table_name="daily_purchase_records",
    )
    op.drop_column("daily_purchase_records", "is_reimbursed")
