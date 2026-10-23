"""Remove asset and revenue-share access from student-facing roles."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_student_portal_permissions"
down_revision: str | Sequence[str] | None = "0045_enrollment_program_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_KEYS = ("student", "registered_user")
PERMISSION_KEYS = ("assets.read_self", "revenue_share.read_self")


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM role_permissions AS rp "
            "USING roles AS r, permissions AS p "
            "WHERE rp.role_id = r.id "
            "AND rp.permission_id = p.id "
            "AND r.key = ANY(CAST(:role_keys AS text[])) "
            "AND p.key = ANY(CAST(:permission_keys AS text[]))"
        ).bindparams(
            role_keys=list(ROLE_KEYS),
            permission_keys=list(PERMISSION_KEYS),
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "SELECT r.id, p.id, 'self' FROM roles AS r "
            "CROSS JOIN permissions AS p "
            "WHERE r.key = ANY(CAST(:role_keys AS text[])) "
            "AND p.key = ANY(CAST(:permission_keys AS text[])) "
            "ON CONFLICT DO NOTHING"
        ).bindparams(
            role_keys=list(ROLE_KEYS),
            permission_keys=list(PERMISSION_KEYS),
        )
    )
