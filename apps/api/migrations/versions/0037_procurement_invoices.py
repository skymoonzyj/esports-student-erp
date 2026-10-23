"""Add procurement invoice uploads and exact purchase matching."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_procurement_invoices"
down_revision: str | Sequence[str] | None = "0036_daily_procurement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_KEY = "procurement.invoices.manage"
PERMISSION_ID = "20000000-0000-4000-8000-000000000070"


def _seed_rbac() -> None:
    op.execute(
        sa.text(
            "INSERT INTO permissions (id, key, name, sensitive) VALUES "
            "(CAST(:id AS uuid), :key, :name, true) "
            "ON CONFLICT (key) DO UPDATE SET "
            "name = EXCLUDED.name, sensitive = EXCLUDED.sensitive"
        ).bindparams(
            id=PERMISSION_ID,
            key=PERMISSION_KEY,
            name="管理采购发票",
        )
    )
    for role_key in ("super_admin", "admin", "administration", "finance"):
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT r.id, p.id, 'global' FROM roles r, permissions p "
                "WHERE r.key = :role_key AND p.key = :permission_key "
                "ON CONFLICT DO NOTHING"
            ).bindparams(role_key=role_key, permission_key=PERMISSION_KEY)
        )


def upgrade() -> None:
    op.create_table(
        "procurement_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("invoice_number", sa.String(length=120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=40), nullable=False),
        sa.Column("extension", sa.String(length=8), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("amount_cents > 0", name="ck_procurement_invoices_amount"),
        sa.CheckConstraint(
            "mime_type IN ('application/pdf', 'image/jpeg', 'image/png', 'image/webp')",
            name="ck_procurement_invoices_mime_type",
        ),
        sa.CheckConstraint(
            "extension IN ('pdf', 'jpg', 'png', 'webp')",
            name="ck_procurement_invoices_extension",
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 20971520",
            name="ck_procurement_invoices_byte_size",
        ),
        sa.CheckConstraint(
            "char_length(sha256) = 64",
            name="ck_procurement_invoices_sha256",
        ),
        sa.CheckConstraint(
            "char_length(btrim(original_filename)) BETWEEN 1 AND 255",
            name="ck_procurement_invoices_filename",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_procurement_invoices_date",
        "procurement_invoices",
        ["invoice_date", "created_at", "id"],
    )
    op.create_index(
        "ix_procurement_invoices_sha256",
        "procurement_invoices",
        ["sha256"],
    )
    op.create_table(
        "procurement_invoice_purchases",
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["procurement_invoices.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_id"],
            ["daily_purchase_records.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("invoice_id", "purchase_id"),
        sa.UniqueConstraint(
            "purchase_id",
            name="uq_procurement_invoice_purchases_purchase",
        ),
    )
    op.create_index(
        "ix_procurement_invoice_purchases_invoice",
        "procurement_invoice_purchases",
        ["invoice_id", "purchase_id"],
    )
    _seed_rbac()


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE key = :permission_key)"
        ).bindparams(permission_key=PERMISSION_KEY)
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE key = :permission_key").bindparams(
            permission_key=PERMISSION_KEY
        )
    )
    op.drop_index(
        "ix_procurement_invoice_purchases_invoice",
        table_name="procurement_invoice_purchases",
    )
    op.drop_table("procurement_invoice_purchases")
    op.drop_index("ix_procurement_invoices_sha256", table_name="procurement_invoices")
    op.drop_index("ix_procurement_invoices_date", table_name="procurement_invoices")
    op.drop_table("procurement_invoices")
