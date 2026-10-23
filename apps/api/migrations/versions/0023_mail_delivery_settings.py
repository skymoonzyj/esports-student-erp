"""Add administrator-managed SMTP delivery settings.

Revision ID: 0023_mail_delivery_settings
Revises: 0022_multiple_custom_cuisines
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_mail_delivery_settings"
down_revision: str | Sequence[str] | None = "0022_multiple_custom_cuisines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_delivery_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "singleton_key",
            sa.String(length=32),
            nullable=False,
            server_default="default",
        ),
        sa.Column("smtp_host", sa.String(length=255), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("tls_mode", sa.String(length=16), nullable=False),
        sa.Column("username_ciphertext", sa.Text(), nullable=False),
        sa.Column("password_ciphertext", sa.Text(), nullable=False),
        sa.Column("sender_ciphertext", sa.Text(), nullable=False),
        sa.Column("from_name", sa.String(length=120), nullable=False),
        sa.Column("reply_to_ciphertext", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_test_status", sa.String(length=16), nullable=False, server_default="never"),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "smtp_port BETWEEN 1 AND 65535",
            name="ck_mail_delivery_settings_port",
        ),
        sa.CheckConstraint(
            "tls_mode IN ('implicit_tls', 'starttls')",
            name="ck_mail_delivery_settings_tls_mode",
        ),
        sa.CheckConstraint(
            "last_test_status IN ('never', 'passed', 'failed')",
            name="ck_mail_delivery_settings_test_status",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            name="fk_mail_delivery_settings_updated_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_key", name="uq_mail_delivery_settings_singleton"),
    )


def downgrade() -> None:
    op.drop_table("mail_delivery_settings")

