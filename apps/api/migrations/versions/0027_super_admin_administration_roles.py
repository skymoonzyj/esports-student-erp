"""Separate super-administrator and administration access.

Revision ID: 0027_super_admin_administration_roles
Revises: 0026_verified_login_identifiers
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_super_admin_administration_roles"
down_revision: str | Sequence[str] | None = "0026_verified_login_identifiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUPER_ADMIN_ROLE_ID = "10000000-0000-4000-8000-000000000009"
ADMINISTRATION_ROLE_ID = "10000000-0000-4000-8000-000000000010"
ACCOUNTS_READ_PERMISSION_ID = "20000000-0000-4000-8000-000000000054"
MAIL_SETTINGS_PERMISSION_ID = "20000000-0000-4000-8000-000000000055"

ADMINISTRATION_PERMISSIONS = (
    "people.list",
    "people.read",
    "accounts.read",
    "housing.read",
    "housing.rooms.manage",
    "housing.stays.manage",
    "housing.transfers.manage",
    "housing.checkouts.manage",
    "housing.issues.manage",
    "housing.reports.export_sensitive",
    "food_preferences.read",
    "food_preferences.export",
)


def _grant(role_key: str, permission_key: str) -> None:
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "SELECT roles.id, permissions.id, 'global' "
            "FROM roles CROSS JOIN permissions "
            "WHERE roles.key = :role_key AND permissions.key = :permission_key "
            "ON CONFLICT DO NOTHING"
        ).bindparams(role_key=role_key, permission_key=permission_key)
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE roles, permissions, role_permissions, user_roles, "
            "user_permission_overrides, user_accounts, staff_profiles "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO roles (id, key, name) VALUES "
            "(CAST(:super_admin_id AS uuid), 'super_admin', '超级管理员'), "
            "(CAST(:administration_id AS uuid), 'administration', '行政')"
        ).bindparams(
            super_admin_id=SUPER_ADMIN_ROLE_ID,
            administration_id=ADMINISTRATION_ROLE_ID,
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO permissions (id, key, name, sensitive) VALUES "
            "(CAST(:accounts_read_id AS uuid), 'accounts.read', '查看账号列表', false), "
            "(CAST(:mail_settings_id AS uuid), "
            "'mail.settings.manage', '管理邮件发送设置', true)"
        ).bindparams(
            accounts_read_id=ACCOUNTS_READ_PERMISSION_ID,
            mail_settings_id=MAIL_SETTINGS_PERMISSION_ID,
        )
    )

    # The super administrator becomes the sole system-recovery role.
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "SELECT CAST(:role_id AS uuid), permissions.id, 'global' FROM permissions"
        ).bindparams(role_id=SUPER_ADMIN_ROLE_ID)
    )
    op.execute(
        sa.text(
            "DELETE FROM role_permissions USING roles, permissions "
            "WHERE role_permissions.role_id = roles.id "
            "AND role_permissions.permission_id = permissions.id "
            "AND roles.key = 'admin' "
            "AND permissions.key IN ('permissions.manage', 'audit.read')"
        )
    )
    _grant("admin", "accounts.read")
    _grant("coach", "housing.read")
    for permission_key in ADMINISTRATION_PERMISSIONS:
        _grant("administration", permission_key)

    # Promote every current administrator without retaining the legacy role.
    op.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT user_roles.user_id, CAST(:super_admin_id AS uuid) "
            "FROM user_roles JOIN roles ON roles.id = user_roles.role_id "
            "WHERE roles.key = 'admin' ON CONFLICT DO NOTHING"
        ).bindparams(super_admin_id=SUPER_ADMIN_ROLE_ID)
    )
    op.execute(
        sa.text(
            "DELETE FROM user_roles USING roles "
            "WHERE user_roles.role_id = roles.id AND roles.key = 'admin'"
        )
    )

    # Administration profiles previously shared the generic staff role.
    op.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT user_accounts.id, CAST(:administration_id AS uuid) "
            "FROM user_accounts "
            "JOIN staff_profiles ON staff_profiles.person_id = user_accounts.person_id "
            "WHERE staff_profiles.staff_type = 'administration' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM user_roles existing "
            "WHERE existing.user_id = user_accounts.id "
            "AND existing.role_id = CAST(:super_admin_id AS uuid)"
            ") ON CONFLICT DO NOTHING"
        ).bindparams(
            administration_id=ADMINISTRATION_ROLE_ID,
            super_admin_id=SUPER_ADMIN_ROLE_ID,
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM user_roles USING roles, user_accounts, staff_profiles "
            "WHERE user_roles.role_id = roles.id AND roles.key = 'staff' "
            "AND user_accounts.id = user_roles.user_id "
            "AND staff_profiles.person_id = user_accounts.person_id "
            "AND staff_profiles.staff_type = 'administration'"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE roles, permissions, role_permissions, user_roles, "
            "user_permission_overrides, user_accounts, staff_profiles "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )

    op.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT user_roles.user_id, legacy.id FROM user_roles "
            "JOIN roles source ON source.id = user_roles.role_id "
            "CROSS JOIN roles legacy "
            "WHERE source.key = 'super_admin' AND legacy.key = 'admin' "
            "ON CONFLICT DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT user_roles.user_id, legacy.id FROM user_roles "
            "JOIN roles source ON source.id = user_roles.role_id "
            "CROSS JOIN roles legacy "
            "WHERE source.key = 'administration' AND legacy.key = 'staff' "
            "ON CONFLICT DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM user_roles USING roles WHERE user_roles.role_id = roles.id "
            "AND roles.key IN ('super_admin', 'administration')"
        )
    )

    _grant("admin", "permissions.manage")
    _grant("admin", "audit.read")
    op.execute(
        sa.text(
            "DELETE FROM role_permissions USING roles, permissions "
            "WHERE role_permissions.role_id = roles.id "
            "AND role_permissions.permission_id = permissions.id "
            "AND ((roles.key = 'coach' AND permissions.key = 'housing.read') "
            "OR (roles.key = 'admin' AND permissions.key = 'accounts.read'))"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM user_permission_overrides WHERE permission_id IN "
            "(CAST(:accounts_read_id AS uuid), CAST(:mail_settings_id AS uuid))"
        ).bindparams(
            accounts_read_id=ACCOUNTS_READ_PERMISSION_ID,
            mail_settings_id=MAIL_SETTINGS_PERMISSION_ID,
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id IN "
            "(CAST(:super_admin_id AS uuid), CAST(:administration_id AS uuid)) "
            "OR permission_id IN "
            "(CAST(:accounts_read_id AS uuid), CAST(:mail_settings_id AS uuid))"
        ).bindparams(
            super_admin_id=SUPER_ADMIN_ROLE_ID,
            administration_id=ADMINISTRATION_ROLE_ID,
            accounts_read_id=ACCOUNTS_READ_PERMISSION_ID,
            mail_settings_id=MAIL_SETTINGS_PERMISSION_ID,
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM permissions WHERE id IN "
            "(CAST(:accounts_read_id AS uuid), CAST(:mail_settings_id AS uuid))"
        ).bindparams(
            accounts_read_id=ACCOUNTS_READ_PERMISSION_ID,
            mail_settings_id=MAIL_SETTINGS_PERMISSION_ID,
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM roles WHERE id IN "
            "(CAST(:super_admin_id AS uuid), CAST(:administration_id AS uuid))"
        ).bindparams(
            super_admin_id=SUPER_ADMIN_ROLE_ID,
            administration_id=ADMINISTRATION_ROLE_ID,
        )
    )
