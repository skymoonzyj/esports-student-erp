"""Add operational service heartbeats and super-admin monitoring permission."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_operations_monitoring"
down_revision: str | Sequence[str] | None = "0042_asset_purchase_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_KEY = "operations.monitor.read"
PERMISSION_ID = "20000000-0000-4000-8000-000000000071"


def upgrade() -> None:
    op.create_table(
        "operations_service_heartbeats",
        sa.Column("service_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("last_result_count", sa.Integer(), nullable=True),
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
            "status IN ('healthy', 'failed')",
            name="ck_operations_service_heartbeats_status",
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_operations_service_heartbeats_failures",
        ),
        sa.CheckConstraint(
            "last_result_count IS NULL OR last_result_count >= 0",
            name="ck_operations_service_heartbeats_result_count",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code ~ '^[A-Za-z][A-Za-z0-9_]{0,79}$'",
            name="ck_operations_service_heartbeats_error_code",
        ),
        sa.PrimaryKeyConstraint("service_key"),
    )
    op.execute(
        sa.text(
            "INSERT INTO permissions (id, key, name, sensitive) VALUES "
            "(CAST(:id AS uuid), :key, :name, true) "
            "ON CONFLICT (key) DO UPDATE SET "
            "name = EXCLUDED.name, sensitive = EXCLUDED.sensitive"
        ).bindparams(id=PERMISSION_ID, key=PERMISSION_KEY, name="查看运维监控")
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "SELECT roles.id, permissions.id, 'global' FROM roles, permissions "
            "WHERE roles.key = 'super_admin' AND permissions.key = :permission_key "
            "ON CONFLICT DO NOTHING"
        ).bindparams(permission_key=PERMISSION_KEY)
    )


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
    op.drop_table("operations_service_heartbeats")
