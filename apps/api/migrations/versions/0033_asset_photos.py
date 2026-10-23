"""Add optional asset identification photos."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_asset_photos"
down_revision: str | Sequence[str] | None = "0032_asset_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.String(length=32), nullable=False),
        sa.Column("extension", sa.String(length=8), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_asset_photos_mime_type",
        ),
        sa.CheckConstraint(
            "byte_size BETWEEN 1 AND 10485760",
            name="ck_asset_photos_byte_size",
        ),
        sa.CheckConstraint(
            "char_length(sha256) = 64 AND sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_asset_photos_sha256",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id"),
    )
    op.create_index("ix_asset_photos_updated", "asset_photos", ["updated_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_asset_photos_updated", table_name="asset_photos")
    op.drop_table("asset_photos")
