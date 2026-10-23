"""Restrict security-audit access to the super-administrator role.

Revision ID: 0030_super_admin_only_audit
Revises: 0029_staff_approval_optional_reason
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_super_admin_only_audit"
down_revision: str | Sequence[str] | None = "0029_staff_approval_optional_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE roles, permissions, role_permissions, "
            "user_permission_overrides IN SHARE ROW EXCLUSIVE MODE"
        )
    )

    # A permission override must never be able to turn the audit log into a
    # non-super-admin surface.  Super administrators already receive the
    # permission from their role, so removing all overrides is deterministic
    # and also clears stale deny rules that could hide the log from them.
    op.execute(
        sa.text(
            "DELETE FROM user_permission_overrides USING permissions "
            "WHERE user_permission_overrides.permission_id = permissions.id "
            "AND permissions.key = 'audit.read'"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM role_permissions USING roles, permissions "
            "WHERE role_permissions.role_id = roles.id "
            "AND role_permissions.permission_id = permissions.id "
            "AND permissions.key = 'audit.read' "
            "AND roles.key <> 'super_admin'"
        )
    )


def downgrade() -> None:
    # Restore the legacy administrator grant when rolling back to the schema
    # where the admin role was the recovery role.
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "SELECT roles.id, permissions.id, 'global' "
            "FROM roles CROSS JOIN permissions "
            "WHERE roles.key = 'admin' AND permissions.key = 'audit.read' "
            "ON CONFLICT DO NOTHING"
        )
    )
