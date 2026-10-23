"""Bind delayed guardian finalization to the reviewed candidate.

Revision ID: 0021_registration_review_candidate_binding
Revises: 0020_registration_review_pending_guardian
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_registration_review_candidate_binding"
down_revision: str | Sequence[str] | None = (
    "0020_registration_review_pending_guardian"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REVIEW_CANDIDATE_CHECK = (
    "(reviewed_candidate_person_id IS NULL AND ("
    "state <> 'pending_guardian' OR "
    "review_decision IS DISTINCT FROM 'approved')) OR "
    "(reviewed_candidate_person_id IS NOT NULL AND "
    "review_decision IS NOT DISTINCT FROM 'approved' AND "
    "state IN ('pending_guardian', 'ready_to_finalize'))"
)


def _lock_applications() -> None:
    op.get_bind().execute(
        sa.text(
            "LOCK TABLE registration_applications IN SHARE ROW EXCLUSIVE MODE"
        )
    )


def _preflight_upgrade() -> None:
    _lock_applications()
    has_unbound_approved_pending = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM registration_applications WHERE "
            "state = 'pending_guardian' AND review_decision = 'approved')"
        )
    )
    if has_unbound_approved_pending:
        raise RuntimeError(
            "cannot add registration review candidate binding with "
            "unbound approved pending guardian applications"
        )


def _preflight_downgrade() -> None:
    _lock_applications()
    has_active_binding = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM registration_applications WHERE "
            "reviewed_candidate_person_id IS NOT NULL OR "
            "(state = 'pending_guardian' AND review_decision = 'approved'))"
        )
    )
    if has_active_binding:
        raise RuntimeError(
            "cannot downgrade registration review candidate binding with "
            "active candidate bindings"
        )


def upgrade() -> None:
    _preflight_upgrade()
    op.add_column(
        "registration_applications",
        sa.Column("reviewed_candidate_person_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_registration_applications_reviewed_candidate",
        "registration_applications",
        "people",
        ["reviewed_candidate_person_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_registration_applications_review_candidate",
        "registration_applications",
        _REVIEW_CANDIDATE_CHECK,
    )


def downgrade() -> None:
    _preflight_downgrade()
    op.drop_constraint(
        "ck_registration_applications_review_candidate",
        "registration_applications",
        type_="check",
    )
    op.drop_constraint(
        "fk_registration_applications_reviewed_candidate",
        "registration_applications",
        type_="foreignkey",
    )
    op.drop_column(
        "registration_applications",
        "reviewed_candidate_person_id",
    )
