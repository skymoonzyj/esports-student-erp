"""Allow each enrollment to use the cohort schedule or an individual window.

Revision ID: 0031_enrollment_individual_schedule
Revises: 0030_super_admin_only_audit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_enrollment_individual_schedule"
down_revision: str | Sequence[str] | None = "0030_super_admin_only_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "enrollments",
        sa.Column(
            "schedule_mode",
            sa.String(length=16),
            nullable=False,
            server_default="cohort",
        ),
    )
    op.add_column(
        "enrollments",
        sa.Column("custom_starts_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "enrollments",
        sa.Column("custom_ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_enrollments_schedule_mode",
        "enrollments",
        "schedule_mode IN ('cohort', 'custom')",
    )
    op.create_check_constraint(
        "ck_enrollments_schedule_dates",
        "enrollments",
        "(schedule_mode = 'cohort' AND custom_starts_at IS NULL "
        "AND custom_ends_at IS NULL) OR "
        "(schedule_mode = 'custom' AND custom_starts_at IS NOT NULL "
        "AND custom_ends_at IS NOT NULL AND custom_ends_at > custom_starts_at)",
    )
    op.create_index(
        "ix_enrollments_schedule_window",
        "enrollments",
        ["schedule_mode", "custom_starts_at", "custom_ends_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_enrollments_schedule_window", table_name="enrollments")
    op.drop_constraint("ck_enrollments_schedule_dates", "enrollments", type_="check")
    op.drop_constraint("ck_enrollments_schedule_mode", "enrollments", type_="check")
    op.drop_column("enrollments", "custom_ends_at")
    op.drop_column("enrollments", "custom_starts_at")
    op.drop_column("enrollments", "schedule_mode")
