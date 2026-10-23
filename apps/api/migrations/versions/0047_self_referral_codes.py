"""Allow coaches and administrators to generate their own referral code."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_self_referral_codes"
down_revision: str | Sequence[str] | None = "0046_student_portal_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_KEY = "revenue_share.create_self_referral"
PERMISSION_ID = "20000000-0000-4000-8000-000000000076"
ROLE_KEYS = ("super_admin", "admin", "coach")


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO permissions (id, key, name, sensitive) VALUES "
            "(CAST(:id AS uuid), :key, :name, false) "
            "ON CONFLICT (key) DO UPDATE SET "
            "name = EXCLUDED.name, sensitive = EXCLUDED.sensitive"
        ).bindparams(
            id=PERMISSION_ID,
            key=PERMISSION_KEY,
            name="生成本人邀请二维码",
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "SELECT roles.id, permissions.id, 'self' FROM roles, permissions "
            "WHERE roles.key = ANY(CAST(:role_keys AS text[])) "
            "AND permissions.key = :permission_key "
            "ON CONFLICT DO NOTHING"
        ).bindparams(
            role_keys=list(ROLE_KEYS),
            permission_key=PERMISSION_KEY,
        )
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
