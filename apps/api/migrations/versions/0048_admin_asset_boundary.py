"""Restrict global asset operations to administration and super administrators."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_admin_asset_boundary"
down_revision: str | Sequence[str] | None = "0047_self_referral_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESTRICTED_PERMISSION_KEYS = (
    "assets.read",
    "assets.manage",
    "assets.assign",
    "assets.export",
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM role_permissions AS rp "
            "USING roles AS r, permissions AS p "
            "WHERE rp.role_id = r.id "
            "AND rp.permission_id = p.id "
            "AND r.key = 'admin' "
            "AND p.key = ANY(CAST(:permission_keys AS text[]))"
        ).bindparams(permission_keys=list(RESTRICTED_PERMISSION_KEYS))
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "SELECT r.id, p.id, 'global' FROM roles AS r "
            "CROSS JOIN permissions AS p "
            "WHERE r.key = 'admin' "
            "AND p.key = ANY(CAST(:permission_keys AS text[])) "
            "ON CONFLICT DO NOTHING"
        ).bindparams(permission_keys=list(RESTRICTED_PERMISSION_KEYS))
    )
