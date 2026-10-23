"""Add administrator-managed transactional mail templates.

Revision ID: 0024_mail_templates
Revises: 0023_mail_delivery_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_mail_templates"
down_revision: str | Sequence[str] | None = "0023_mail_delivery_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_key", sa.String(length=48), nullable=False),
        sa.Column("subject_template", sa.String(length=200), nullable=False),
        sa.Column("html_template", sa.Text(), nullable=False),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "event_key IN ('verification_code')",
            name="ck_mail_templates_event_key",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            name="fk_mail_templates_updated_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_mail_templates_event_key"),
    )


def downgrade() -> None:
    op.drop_table("mail_templates")
