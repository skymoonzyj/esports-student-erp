"""Allow staff identity approvals without a review reason.

Revision ID: 0029_staff_approval_optional_reason
Revises: 0028_registration_channel_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_staff_approval_optional_reason"
down_revision: str | Sequence[str] | None = "0028_registration_channel_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_staff_identity_applications_review_state",
        "staff_identity_applications",
        type_="check",
    )
    op.create_check_constraint(
        "ck_staff_identity_applications_review_state",
        "staff_identity_applications",
        "(status = 'approved' AND reviewed_by_user_id IS NOT NULL "
        "AND reviewed_at IS NOT NULL) OR "
        "(status = 'rejected' AND reviewed_by_user_id IS NOT NULL "
        "AND review_reason IS NOT NULL AND reviewed_at IS NOT NULL) OR "
        "(status IN ('pending', 'withdrawn') AND reviewed_by_user_id IS NULL "
        "AND review_reason IS NULL AND reviewed_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_staff_identity_applications_review_state",
        "staff_identity_applications",
        type_="check",
    )
    op.execute(
        sa.text(
            "UPDATE staff_identity_applications SET review_reason = '审核通过' "
            "WHERE status = 'approved' AND review_reason IS NULL"
        )
    )
    op.create_check_constraint(
        "ck_staff_identity_applications_review_state",
        "staff_identity_applications",
        "(status IN ('approved', 'rejected') AND "
        "reviewed_by_user_id IS NOT NULL AND review_reason IS NOT NULL "
        "AND reviewed_at IS NOT NULL) OR "
        "(status IN ('pending', 'withdrawn') AND reviewed_by_user_id IS NULL "
        "AND review_reason IS NULL AND reviewed_at IS NULL)",
    )
