"""Allow a dedicated password-reset mail template.

Revision ID: 0025_password_reset_mail_template
Revises: 0024_mail_templates
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_password_reset_mail_template"
down_revision: str | Sequence[str] | None = "0024_mail_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_mail_templates_event_key", "mail_templates", type_="check")
    op.create_check_constraint(
        "ck_mail_templates_event_key",
        "mail_templates",
        "event_key IN ('verification_code', 'password_reset')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM mail_templates WHERE event_key = 'password_reset'")
    op.drop_constraint("ck_mail_templates_event_key", "mail_templates", type_="check")
    op.create_check_constraint(
        "ck_mail_templates_event_key",
        "mail_templates",
        "event_key IN ('verification_code')",
    )
