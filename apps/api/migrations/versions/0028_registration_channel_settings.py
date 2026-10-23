"""Add administrator-managed self-registration channel settings.

Revision ID: 0028_registration_channel_settings
Revises: 0027_super_admin_administration_roles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_registration_channel_settings"
down_revision: str | Sequence[str] | None = "0027_super_admin_administration_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registration_channel_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "singleton_key",
            sa.String(length=32),
            nullable=False,
            server_default="default",
        ),
        sa.Column("phone_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            name="fk_registration_channel_settings_updated_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "singleton_key",
            name="uq_registration_channel_settings_singleton",
        ),
    )


def downgrade() -> None:
    op.drop_table("registration_channel_settings")
