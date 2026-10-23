"""Add asset inventory, assignment history, and asset permissions.

Revision ID: 0032_asset_management
Revises: 0031_enrollment_individual_schedule
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_asset_management"
down_revision: str | Sequence[str] | None = "0031_enrollment_individual_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PERMISSIONS = (
    ("20000000-0000-4000-8000-000000000056", "assets.read_self", "查看本人资产", False),
    ("20000000-0000-4000-8000-000000000057", "assets.read", "查看资产台账", False),
    ("20000000-0000-4000-8000-000000000058", "assets.manage", "管理资产台账", True),
    ("20000000-0000-4000-8000-000000000059", "assets.assign", "分配与归还资产", True),
    ("20000000-0000-4000-8000-000000000060", "assets.export", "导出资产台账", True),
)


def _grant(role_key: str, permission_key: str, scope_kind: str) -> None:
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "SELECT roles.id, permissions.id, :scope_kind "
            "FROM roles CROSS JOIN permissions "
            "WHERE roles.key = :role_key AND permissions.key = :permission_key "
            "ON CONFLICT DO NOTHING"
        ).bindparams(
            role_key=role_key,
            permission_key=permission_key,
            scope_kind=scope_kind,
        )
    )


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("brand", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("serial_number", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="available", nullable=False),
        sa.Column("location", sa.String(length=160), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('available', 'assigned', 'maintenance', 'retired')",
            name="ck_assets_status",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_assets_name",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_code"),
    )
    op.create_index("ix_assets_status_updated", "assets", ["status", "updated_at", "id"])
    op.create_index("ix_assets_name", "assets", ["name"])
    op.create_table(
        "asset_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_return_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("condition_on_assign", sa.String(length=500), nullable=True),
        sa.Column("condition_on_return", sa.String(length=500), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("returned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "returned_at IS NULL OR returned_at >= assigned_at",
            name="ck_asset_assignments_return_range",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["returned_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_asset_assignments_person_active",
        "asset_assignments",
        ["person_id", "returned_at", "id"],
    )
    op.create_index(
        "uq_asset_assignments_active_asset",
        "asset_assignments",
        ["asset_id"],
        unique=True,
        postgresql_where=sa.text("returned_at IS NULL"),
    )

    bind = op.get_bind()
    for permission_id, key, name, sensitive in PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, key, name, sensitive) VALUES "
                "(CAST(:permission_id AS uuid), :key, :name, :sensitive) "
                "ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, sensitive = EXCLUDED.sensitive"
            ).bindparams(
                permission_id=permission_id,
                key=key,
                name=name,
                sensitive=sensitive,
            )
        )

    for role_key in (
        "admin",
        "coach",
        "customer_service",
        "life_teacher",
        "staff",
        "guardian",
        "student",
        "registered_user",
        "super_admin",
        "administration",
    ):
        _grant(role_key, "assets.read_self", "self")
    _grant("guardian", "assets.read_self", "family")
    for permission_key in ("assets.read", "assets.manage", "assets.assign", "assets.export"):
        _grant("super_admin", permission_key, "global")
        _grant("admin", permission_key, "global")
        _grant("administration", permission_key, "global")


def downgrade() -> None:
    permission_keys = [key for _, key, _, _ in PERMISSIONS]
    op.execute(
        sa.text(
            "DELETE FROM role_permissions USING permissions "
            "WHERE role_permissions.permission_id = permissions.id "
            "AND permissions.key = ANY(:keys)"
        ).bindparams(keys=permission_keys)
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE key = ANY(:keys)").bindparams(keys=permission_keys)
    )
    op.drop_index("uq_asset_assignments_active_asset", table_name="asset_assignments")
    op.drop_index("ix_asset_assignments_person_active", table_name="asset_assignments")
    op.drop_table("asset_assignments")
    op.drop_index("ix_assets_name", table_name="assets")
    op.drop_index("ix_assets_status_updated", table_name="assets")
    op.drop_table("assets")

