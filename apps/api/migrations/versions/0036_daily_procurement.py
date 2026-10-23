"""Add daily procurement records, settlement centers, and role permissions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_daily_procurement"
down_revision: str | Sequence[str] | None = "0035_revenue_sharing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "procurement.read": (
        "20000000-0000-4000-8000-000000000065",
        "查看日常采购",
        True,
    ),
    "procurement.manage": (
        "20000000-0000-4000-8000-000000000066",
        "管理日常采购",
        True,
    ),
    "procurement.settle": (
        "20000000-0000-4000-8000-000000000067",
        "管理采购结算",
        True,
    ),
    "procurement.export": (
        "20000000-0000-4000-8000-000000000068",
        "导出日常采购",
        True,
    ),
    "procurement.centers.manage": (
        "20000000-0000-4000-8000-000000000069",
        "管理采购结算中心",
        True,
    ),
}


def _seed_rbac() -> None:
    for key, (permission_id, name, sensitive) in PERMISSIONS.items():
        op.execute(
            sa.text(
                "INSERT INTO permissions (id, key, name, sensitive) VALUES "
                "(CAST(:id AS uuid), :key, :name, :sensitive) "
                "ON CONFLICT (key) DO UPDATE SET "
                "name = EXCLUDED.name, sensitive = EXCLUDED.sensitive"
            ).bindparams(
                id=permission_id,
                key=key,
                name=name,
                sensitive=sensitive,
            )
        )
    grants = {
        *(
            (role, permission, "global")
            for role in ("super_admin", "admin")
            for permission in PERMISSIONS
        ),
        *(("administration", permission, "global") for permission in PERMISSIONS),
        ("finance", "procurement.read", "global"),
        ("finance", "procurement.settle", "global"),
        ("finance", "procurement.export", "global"),
    }
    for role_key, permission_key, scope_kind in sorted(grants):
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT r.id, p.id, :scope_kind FROM roles r, permissions p "
                "WHERE r.key = :role_key AND p.key = :permission_key "
                "ON CONFLICT DO NOTHING"
            ).bindparams(
                role_key=role_key,
                permission_key=permission_key,
                scope_kind=scope_kind,
            )
        )


def upgrade() -> None:
    op.create_table(
        "procurement_settlement_centers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_procurement_settlement_centers_name",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_procurement_settlement_centers_name_ci",
        "procurement_settlement_centers",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_index(
        "ix_procurement_settlement_centers_active_name",
        "procurement_settlement_centers",
        ["is_active", "name", "id"],
    )
    op.create_table(
        "daily_purchase_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("item_name", sa.String(length=120), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("unit_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("total_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("settlement_center_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_settled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("settled_on", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("settled_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.CheckConstraint(
            "char_length(btrim(item_name)) BETWEEN 1 AND 120",
            name="ck_daily_purchase_records_item_name",
        ),
        sa.CheckConstraint(
            "quantity > 0 AND quantity <= 999999999.999",
            name="ck_daily_purchase_records_quantity",
        ),
        sa.CheckConstraint(
            "unit_price_cents > 0",
            name="ck_daily_purchase_records_unit_price",
        ),
        sa.CheckConstraint(
            "total_price_cents = round(quantity * unit_price_cents)",
            name="ck_daily_purchase_records_total_price",
        ),
        sa.CheckConstraint(
            "(is_settled AND settled_on IS NOT NULL AND settled_by_user_id IS NOT NULL) "
            "OR (NOT is_settled AND settled_on IS NULL AND settled_by_user_id IS NULL)",
            name="ck_daily_purchase_records_settlement",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_center_id"],
            ["procurement_settlement_centers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settled_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_daily_purchase_records_date",
        "daily_purchase_records",
        ["purchase_date", "created_at", "id"],
    )
    op.create_index(
        "ix_daily_purchase_records_center_date",
        "daily_purchase_records",
        ["settlement_center_id", "purchase_date", "id"],
    )
    op.create_index(
        "ix_daily_purchase_records_settled_date",
        "daily_purchase_records",
        ["is_settled", "purchase_date", "id"],
    )
    _seed_rbac()


def downgrade() -> None:
    permission_keys = tuple(PERMISSIONS)
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE key = ANY(CAST(:keys AS text[])))"
        ).bindparams(keys=list(permission_keys))
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE key = ANY(CAST(:keys AS text[]))").bindparams(
            keys=list(permission_keys)
        )
    )
    op.drop_index(
        "ix_daily_purchase_records_settled_date",
        table_name="daily_purchase_records",
    )
    op.drop_index(
        "ix_daily_purchase_records_center_date",
        table_name="daily_purchase_records",
    )
    op.drop_index("ix_daily_purchase_records_date", table_name="daily_purchase_records")
    op.drop_table("daily_purchase_records")
    op.drop_index(
        "ix_procurement_settlement_centers_active_name",
        table_name="procurement_settlement_centers",
    )
    op.drop_index(
        "uq_procurement_settlement_centers_name_ci",
        table_name="procurement_settlement_centers",
    )
    op.drop_table("procurement_settlement_centers")
