"""Deduplicate procurement retries without modifying historical entries."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057_procurement_idempotency"
down_revision = "0056_evaluation_upload_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, kind in (
        ("daily_purchase_records", "purchase"),
        ("procurement_reserve_fund_entries", "reserve"),
    ):
        op.add_column(table, sa.Column("operation_key", postgresql.UUID(as_uuid=True)))
        op.add_column(table, sa.Column("request_fingerprint", sa.String(64)))
        op.create_unique_constraint(
            f"uq_procurement_{kind}_operation", table, ["created_by_user_id", "operation_key"]
        )
        op.create_check_constraint(
            f"ck_procurement_{kind}_operation",
            table,
            "(operation_key IS NULL AND request_fingerprint IS NULL) OR "
            "(operation_key IS NOT NULL AND request_fingerprint IS NOT NULL "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$')",
        )


def downgrade() -> None:
    for table, kind in (
        ("procurement_reserve_fund_entries", "reserve"),
        ("daily_purchase_records", "purchase"),
    ):
        op.drop_constraint(f"ck_procurement_{kind}_operation", table, type_="check")
        op.drop_constraint(f"uq_procurement_{kind}_operation", table, type_="unique")
        op.drop_column(table, "request_fingerprint")
        op.drop_column(table, "operation_key")
