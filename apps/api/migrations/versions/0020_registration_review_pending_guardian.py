"""Allow approved registration reviews to wait for guardian confirmation.

Revision ID: 0020_registration_review_pending_guardian
Revises: 0019_guardian_invitation_tokens
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_registration_review_pending_guardian"
down_revision: str | Sequence[str] | None = "0019_guardian_invitation_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_REVIEW_STATE_CHECK = (
    "(review_decision IS NULL AND reviewed_by_user_id IS NULL AND "
    "review_reason IS NULL AND reviewed_at IS NULL) OR "
    "(review_decision IS NOT NULL AND reviewed_by_user_id IS NOT NULL "
    "AND review_reason IS NOT NULL AND reviewed_at IS NOT NULL AND ("
    "(review_decision = 'approved' AND "
    "state IN ('ready_to_finalize', 'finalized', 'conflict')) OR "
    "(review_decision = 'rejected' AND state = 'rejected')))"
)
_NEW_REVIEW_STATE_CHECK = (
    "(review_decision IS NULL AND reviewed_by_user_id IS NULL AND "
    "review_reason IS NULL AND reviewed_at IS NULL) OR "
    "(review_decision IS NOT NULL AND reviewed_by_user_id IS NOT NULL "
    "AND review_reason IS NOT NULL AND reviewed_at IS NOT NULL AND ("
    "(review_decision = 'approved' AND "
    "state IN ('pending_guardian', 'ready_to_finalize', 'finalized', "
    "'conflict')) OR "
    "(review_decision = 'rejected' AND state = 'rejected')))"
)


def _replace_review_state_constraint(condition: str) -> None:
    op.drop_constraint(
        "ck_registration_applications_review_state",
        "registration_applications",
        type_="check",
    )
    op.create_check_constraint(
        "ck_registration_applications_review_state",
        "registration_applications",
        condition,
    )


def _preflight_downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE registration_applications IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    has_approved_pending_guardian = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM registration_applications WHERE "
            "state = 'pending_guardian' AND review_decision = 'approved')"
        )
    )
    if has_approved_pending_guardian:
        raise RuntimeError(
            "cannot downgrade registration review guardian gate with "
            "approved pending guardian applications"
        )


def upgrade() -> None:
    _replace_review_state_constraint(_NEW_REVIEW_STATE_CHECK)


def downgrade() -> None:
    _preflight_downgrade()
    _replace_review_state_constraint(_OLD_REVIEW_STATE_CHECK)
