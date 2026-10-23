"""Prevent duplicate settlement operations and payment references."""

import hashlib

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0051_settlement_idempotency"
down_revision = "0050_asset_warranty_service"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "revenue_share_settlements",
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "revenue_share_settlements", sa.Column("request_fingerprint", sa.String(64), nullable=True)
    )
    op.add_column(
        "revenue_share_settlements", sa.Column("reference_key", sa.String(64), nullable=True)
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, beneficiary_kind, beneficiary_person_id, beneficiary_organization_id, "
            "reference FROM revenue_share_settlements ORDER BY created_at, id"
        )
    )
    seen: set[str] = set()
    for row in rows.mappings():
        beneficiary_id = row["beneficiary_person_id"] or row["beneficiary_organization_id"]
        normalized = "".join(row["reference"].split()).upper()
        key = hashlib.sha256(
            f"{row['beneficiary_kind']}:{beneficiary_id}:{normalized}".encode()
        ).hexdigest()
        # Preserve old duplicates and amounts, but reserve each reference against new writes.
        if key not in seen:
            connection.execute(
                sa.text("UPDATE revenue_share_settlements SET reference_key = :key WHERE id = :id"),
                {"key": key, "id": row["id"]},
            )
            seen.add(key)
    op.create_unique_constraint(
        "uq_revenue_share_settlements_operation",
        "revenue_share_settlements",
        ["created_by_user_id", "idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_revenue_share_settlements_reference_key", "revenue_share_settlements", ["reference_key"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_revenue_share_settlements_reference_key", "revenue_share_settlements", type_="unique"
    )
    op.drop_constraint(
        "uq_revenue_share_settlements_operation", "revenue_share_settlements", type_="unique"
    )
    op.drop_column("revenue_share_settlements", "reference_key")
    op.drop_column("revenue_share_settlements", "request_fingerprint")
    op.drop_column("revenue_share_settlements", "idempotency_key")
