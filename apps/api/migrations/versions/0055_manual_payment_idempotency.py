"""Deduplicate manual collection/refund retries without changing historical money."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055_manual_payment_idempotency"
down_revision = "0054_admission_order_numbers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, unique_name, check_name in (
        ("payments", "uq_payments_manual_operation", "ck_payments_operation_fingerprint"),
        (
            "payment_refunds",
            "uq_payment_refunds_manual_operation",
            "ck_payment_refunds_operation_fingerprint",
        ),
    ):
        op.add_column(table, sa.Column("idempotency_key", postgresql.UUID(as_uuid=True)))
        op.add_column(table, sa.Column("request_fingerprint", sa.String(64)))
        op.create_unique_constraint(unique_name, table, ["created_by_user_id", "idempotency_key"])
        op.create_check_constraint(
            check_name,
            table,
            "(idempotency_key IS NULL AND request_fingerprint IS NULL) OR "
            "(idempotency_key IS NOT NULL AND request_fingerprint IS NOT NULL "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$')",
        )


def downgrade() -> None:
    for table, unique_name, check_name in (
        ("payment_refunds", "uq_payment_refunds_manual_operation", "ck_payment_refunds_operation_fingerprint"),
        ("payments", "uq_payments_manual_operation", "ck_payments_operation_fingerprint"),
    ):
        op.drop_constraint(check_name, table, type_="check")
        op.drop_constraint(unique_name, table, type_="unique")
        op.drop_column(table, "request_fingerprint")
        op.drop_column(table, "idempotency_key")
