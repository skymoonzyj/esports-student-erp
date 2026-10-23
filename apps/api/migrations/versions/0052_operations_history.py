"""Persist minute request metrics and operations alert incidents."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052_operations_history"
down_revision: str | Sequence[str] | None = "0051_settlement_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operations_metric_batches",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_operations_metric_batches_created_at", "operations_metric_batches", ["created_at"])
    op.create_table(
        "operations_request_minutes",
        sa.Column("starts_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("method", sa.String(16), primary_key=True),
        sa.Column("route", sa.String(300), primary_key=True),
        sa.Column("status_code", sa.Integer(), primary_key=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("latency_sum_ms", sa.Float(), nullable=False),
        sa.Column("max_latency_ms", sa.Float(), nullable=False),
        sa.Column("latency_histogram", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "operations_alerts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("detail", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.UUID(), sa.ForeignKey("user_accounts.id", ondelete="SET NULL")),
        sa.CheckConstraint("status IN ('active', 'resolved')", name="ck_operations_alert_status"),
        sa.CheckConstraint("severity IN ('warning', 'critical')", name="ck_operations_alert_severity"),
    )
    op.create_index("ix_operations_alert_first_seen", "operations_alerts", ["first_seen_at"])
    op.create_index(
        "uq_operations_alert_active_key", "operations_alerts", ["key"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("operations_alerts")
    op.drop_table("operations_request_minutes")
    op.drop_table("operations_metric_batches")
