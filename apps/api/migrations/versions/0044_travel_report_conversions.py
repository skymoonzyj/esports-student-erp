"""Add travel billing workbook conversion history and permissions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_travel_report_conversions"
down_revision: str | Sequence[str] | None = "0043_operations_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = (
    ("20000000-0000-4000-8000-000000000072", "travel_reports.read", "查看订票报表"),
    ("20000000-0000-4000-8000-000000000073", "travel_reports.import", "导入订票账单"),
    ("20000000-0000-4000-8000-000000000074", "travel_reports.export", "下载订票报表"),
)
def upgrade() -> None:
    op.create_table(
        "travel_report_conversions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("converted_filename", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("converted_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_byte_size", sa.BigInteger(), nullable=False),
        sa.Column("converted_byte_size", sa.BigInteger(), nullable=False),
        sa.Column("source_content", sa.LargeBinary(), nullable=False),
        sa.Column("converted_content", sa.LargeBinary(), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("cost_center", sa.String(length=255), nullable=False),
        sa.Column("billing_period_start", sa.Date(), nullable=False),
        sa.Column("billing_period_end", sa.Date(), nullable=False),
        sa.Column("domestic_air_count", sa.Integer(), nullable=False),
        sa.Column("international_air_count", sa.Integer(), nullable=False),
        sa.Column("train_count", sa.Integer(), nullable=False),
        sa.Column("source_total_amount", sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column("converted_total_amount", sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("imported_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_byte_size BETWEEN 1 AND 20971520", name="ck_travel_report_source_size"
        ),
        sa.CheckConstraint(
            "converted_byte_size BETWEEN 1 AND 52428800",
            name="ck_travel_report_converted_size",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_travel_report_source_sha256"
        ),
        sa.CheckConstraint(
            "converted_sha256 ~ '^[0-9a-f]{64}$'", name="ck_travel_report_converted_sha256"
        ),
        sa.CheckConstraint(
            "billing_period_end >= billing_period_start", name="ck_travel_report_billing_period"
        ),
        sa.CheckConstraint(
            "domestic_air_count >= 0 AND international_air_count >= 0 AND train_count >= 0",
            name="ck_travel_report_counts",
        ),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_sha256"),
    )
    op.create_index(
        "ix_travel_report_conversions_created",
        "travel_report_conversions",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_travel_report_conversions_period",
        "travel_report_conversions",
        ["billing_period_start", "billing_period_end", "id"],
    )
    connection = op.get_bind()
    for permission_id, key, name in PERMISSIONS:
        connection.execute(
            sa.text(
                "INSERT INTO permissions (id, key, name, sensitive) VALUES "
                "(CAST(:id AS uuid), :key, :name, true) "
                "ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, sensitive = true"
            ),
            {"id": permission_id, "key": key, "name": name},
        )
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT roles.id, permissions.id, 'global' FROM roles, permissions "
                "WHERE roles.key IN ('super_admin', 'administration', 'finance') "
                "AND permissions.key = :permission_key "
                "ON CONFLICT DO NOTHING"
            ),
            {"permission_key": key},
        )


def downgrade() -> None:
    connection = op.get_bind()
    for _, key, _ in PERMISSIONS:
        connection.execute(
            sa.text(
                "DELETE FROM role_permissions WHERE permission_id IN "
                "(SELECT id FROM permissions WHERE key = :permission_key)"
            ),
            {"permission_key": key},
        )
        connection.execute(
            sa.text("DELETE FROM permissions WHERE key = :permission_key"),
            {"permission_key": key},
        )
    op.drop_index("ix_travel_report_conversions_period", table_name="travel_report_conversions")
    op.drop_index("ix_travel_report_conversions_created", table_name="travel_report_conversions")
    op.drop_table("travel_report_conversions")
