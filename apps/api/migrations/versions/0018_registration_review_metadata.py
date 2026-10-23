"""Persist self-service registration review decisions and reasons.

Revision ID: 0018_registration_review_metadata
Revises: 0017_self_service_registration
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_registration_review_metadata"
down_revision: str | Sequence[str] | None = "0017_self_service_registration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight_downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE registration_applications IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    has_review_history = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM registration_applications WHERE "
            "review_decision IS NOT NULL OR reviewed_by_user_id IS NOT NULL OR "
            "review_reason IS NOT NULL OR reviewed_at IS NOT NULL)"
        )
    )
    if has_review_history:
        raise RuntimeError(
            "cannot downgrade registration review metadata with review history"
        )


def upgrade() -> None:
    op.add_column(
        "registration_applications",
        sa.Column("review_decision", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "registration_applications",
        sa.Column("reviewed_by_user_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "registration_applications",
        sa.Column("review_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "registration_applications",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_registration_applications_reviewer",
        "registration_applications",
        "user_accounts",
        ["reviewed_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_registration_applications_review_reason",
        "registration_applications",
        "review_reason IS NULL OR ("
        "regexp_replace(review_reason, "
        "'^[[:space:]]+|[[:space:]]+$', '', 'g') = review_reason AND "
        "regexp_replace(review_reason, '[[:space:]]', '', 'g') <> '' AND "
        "char_length(review_reason) BETWEEN 2 AND 500)",
    )
    op.create_check_constraint(
        "ck_registration_applications_review_state",
        "registration_applications",
        "(review_decision IS NULL AND reviewed_by_user_id IS NULL AND "
        "review_reason IS NULL AND reviewed_at IS NULL) OR "
        "(review_decision IS NOT NULL AND reviewed_by_user_id IS NOT NULL "
        "AND review_reason IS NOT NULL AND reviewed_at IS NOT NULL AND ("
        "(review_decision = 'approved' AND "
        "state IN ('ready_to_finalize', 'finalized', 'conflict')) OR "
        "(review_decision = 'rejected' AND state = 'rejected')))",
    )


def downgrade() -> None:
    _preflight_downgrade()
    op.drop_constraint(
        "ck_registration_applications_review_state",
        "registration_applications",
        type_="check",
    )
    op.drop_constraint(
        "ck_registration_applications_review_reason",
        "registration_applications",
        type_="check",
    )
    op.drop_constraint(
        "fk_registration_applications_reviewer",
        "registration_applications",
        type_="foreignkey",
    )
    op.drop_column("registration_applications", "reviewed_at")
    op.drop_column("registration_applications", "review_reason")
    op.drop_column("registration_applications", "reviewed_by_user_id")
    op.drop_column("registration_applications", "review_decision")
