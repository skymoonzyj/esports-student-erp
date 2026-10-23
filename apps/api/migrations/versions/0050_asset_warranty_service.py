"""Add asset after-sales service information."""

import sqlalchemy as sa
from alembic import op

revision = "0050_asset_warranty_service"
down_revision = "0049_project_coach_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("warranty_channel", sa.String(20), nullable=True))
    op.add_column("assets", sa.Column("warranty_service_location", sa.String(200), nullable=True))
    op.add_column("assets", sa.Column("warranty_contact_name", sa.String(80), nullable=True))
    op.add_column("assets", sa.Column("warranty_contact_phone", sa.String(40), nullable=True))
    op.add_column("assets", sa.Column("warranty_product_url", sa.String(2000), nullable=True))
    op.add_column("assets", sa.Column("warranty_order_number", sa.String(120), nullable=True))
    op.create_check_constraint(
        "ck_assets_warranty_channel",
        "assets",
        "warranty_channel IS NULL OR warranty_channel IN ('online', 'offline')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_assets_warranty_channel", "assets", type_="check")
    op.drop_column("assets", "warranty_order_number")
    op.drop_column("assets", "warranty_product_url")
    op.drop_column("assets", "warranty_contact_phone")
    op.drop_column("assets", "warranty_contact_name")
    op.drop_column("assets", "warranty_service_location")
    op.drop_column("assets", "warranty_channel")
