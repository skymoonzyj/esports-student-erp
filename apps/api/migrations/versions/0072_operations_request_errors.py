"""Retain minimal per-request HTTP error attribution for 30 days."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072_operations_request_errors"
down_revision: str | Sequence[str] | None = "0071_housing_room_care"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operations_request_errors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.Uuid()),
        sa.Column("source_ip", sa.String(45)),
        sa.Column("request_id", sa.String(128)),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("route", sa.String(300), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.CheckConstraint(
            "status_code BETWEEN 400 AND 599", name="ck_operations_request_errors_status"
        ),
    )
    op.create_index(
        "ix_operations_request_errors_occurred", "operations_request_errors", ["occurred_at"]
    )
    op.create_index(
        "ix_operations_request_errors_account_time", "operations_request_errors",
        ["account_id", "occurred_at"],
    )
    op.create_index(
        "ix_operations_request_errors_source_time", "operations_request_errors",
        ["source_ip", "occurred_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE operations_request_errors IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM operations_request_errors)")):
        raise RuntimeError("cannot downgrade request errors with retained error details")
    op.drop_table("operations_request_errors")
