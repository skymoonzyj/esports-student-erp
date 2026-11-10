"""Separate supplier leases from asset custody and warranty."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0073_asset_ownership_rentals"
down_revision: str | Sequence[str] | None = "0072_operations_request_errors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FACTS = """COALESCE((
(ownership_type IS NULL AND acquisition_method IS NULL AND acquisition_channel IS NULL
 AND acquisition_note IS NULL AND rental_started_on IS NULL AND rental_expires_on IS NULL
 AND rental_returned_on IS NULL)
OR (ownership_type = 'owned' AND acquisition_method IN ('purchase', 'other')
 AND rental_started_on IS NULL AND rental_expires_on IS NULL AND rental_returned_on IS NULL
 AND (acquisition_channel IS NULL OR acquisition_channel IN ('online', 'offline'))
 AND ((acquisition_method = 'purchase' AND acquisition_channel IS NOT NULL) OR (acquisition_method = 'other' AND acquisition_channel IS NULL
 AND acquisition_note IS NOT NULL AND length(btrim(acquisition_note)) > 0)))
OR (ownership_type = 'rented' AND acquisition_method IS NULL AND acquisition_channel IS NULL
 AND acquisition_note IS NULL AND rental_started_on IS NOT NULL AND rental_expires_on IS NOT NULL
 AND rental_started_on <= rental_expires_on
 AND rental_started_on >= DATE '0001-01-01' AND rental_expires_on <= DATE '9999-12-31'
 AND (rental_returned_on IS NULL OR (rental_returned_on >= rental_started_on AND rental_returned_on <= DATE '9999-12-31')))
), FALSE)"""


def upgrade() -> None:
    for name in ("ownership_type", "acquisition_method", "acquisition_channel"):
        op.add_column("assets", sa.Column(name, sa.String(16)))
    op.add_column("assets", sa.Column("acquisition_note", sa.String(500)))
    for name in ("rental_started_on", "rental_expires_on", "rental_returned_on"):
        op.add_column("assets", sa.Column(name, sa.Date()))
    op.add_column(
        "assets", sa.Column("rental_revision", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute("""UPDATE assets a SET ownership_type='owned', acquisition_method='purchase',
        acquisition_channel=p.purchase_channel FROM daily_purchase_records p
        WHERE a.source_purchase_id=p.id""")
    op.create_check_constraint("ck_assets_rental_facts", "assets", FACTS)
    op.create_check_constraint("ck_assets_rental_revision", "assets", "rental_revision >= 0")
    op.create_index(
        "ix_assets_rental_expiry",
        "assets",
        ["ownership_type", "rental_returned_on", "rental_expires_on", "id"],
    )
    op.create_table(
        "asset_rental_operations",
        sa.Column("operation_key", sa.Uuid(), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "asset_rental_renewals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "operation_key",
            sa.Uuid(),
            sa.ForeignKey("asset_rental_operations.operation_key", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "asset_id", sa.Uuid(), sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("previous_expires_on", sa.Date(), nullable=False),
        sa.Column("rental_expires_on", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "period IN ('week', 'month', 'quarter')", name="ck_asset_rental_renewals_period"
        ),
        sa.CheckConstraint(
            "rental_expires_on > previous_expires_on", name="ck_asset_rental_renewals_range"
        ),
    )
    op.create_index(
        "ix_asset_rental_renewals_asset_time",
        "asset_rental_renewals",
        ["asset_id", "created_at", "id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE assets, asset_rental_operations, asset_rental_renewals IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.scalar(
        sa.text("""SELECT EXISTS (SELECT 1 FROM asset_rental_operations)
        OR EXISTS (SELECT 1 FROM asset_rental_renewals)
        OR EXISTS (SELECT 1 FROM assets WHERE ownership_type IS NOT NULL OR rental_revision <> 0)""")
    ):
        raise RuntimeError("cannot downgrade with retained asset ownership or rental evidence")
    op.drop_table("asset_rental_renewals")
    op.drop_table("asset_rental_operations")
    op.drop_index("ix_assets_rental_expiry", "assets")
    op.drop_constraint("ck_assets_rental_facts", "assets")
    op.drop_constraint("ck_assets_rental_revision", "assets")
    for name in (
        "rental_revision",
        "rental_returned_on",
        "rental_expires_on",
        "rental_started_on",
        "acquisition_note",
        "acquisition_channel",
        "acquisition_method",
        "ownership_type",
    ):
        op.drop_column("assets", name)
