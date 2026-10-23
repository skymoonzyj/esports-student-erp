"""Seed platform roles and permissions.

Revision ID: 0002_seed_rbac
Revises: 0001_platform_foundation
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

revision: str = "0002_seed_rbac"
down_revision: str | Sequence[str] | None = "0001_platform_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROLES = {
    "admin": (UUID("10000000-0000-4000-8000-000000000001"), "管理员"),
    "coach": (UUID("10000000-0000-4000-8000-000000000002"), "教练"),
    "customer_service": (UUID("10000000-0000-4000-8000-000000000003"), "客服"),
    "life_teacher": (UUID("10000000-0000-4000-8000-000000000004"), "生活老师"),
    "staff": (UUID("10000000-0000-4000-8000-000000000005"), "其他员工"),
    "guardian": (UUID("10000000-0000-4000-8000-000000000006"), "监护人"),
    "student": (UUID("10000000-0000-4000-8000-000000000007"), "学员"),
}

PERMISSIONS = {
    "people.list": (
        UUID("20000000-0000-4000-8000-000000000001"),
        "查看人员列表",
        False,
    ),
    "people.read": (
        UUID("20000000-0000-4000-8000-000000000002"),
        "查看人员档案",
        False,
    ),
    "people.create": (
        UUID("20000000-0000-4000-8000-000000000003"),
        "创建人员",
        False,
    ),
    "people.update": (
        UUID("20000000-0000-4000-8000-000000000004"),
        "更新人员",
        False,
    ),
    "people.phone.reveal": (
        UUID("20000000-0000-4000-8000-000000000005"),
        "查看完整手机号",
        True,
    ),
    "people.identity.reveal": (
        UUID("20000000-0000-4000-8000-000000000006"),
        "查看完整身份证",
        True,
    ),
    "accounts.manage": (
        UUID("20000000-0000-4000-8000-000000000007"),
        "管理账号",
        False,
    ),
    "permissions.manage": (
        UUID("20000000-0000-4000-8000-000000000008"),
        "管理角色与权限",
        True,
    ),
    "audit.read": (
        UUID("20000000-0000-4000-8000-000000000009"),
        "查看安全审计",
        True,
    ),
}

GRANTS = [
    *[("admin", permission, "global") for permission in PERMISSIONS],
    ("customer_service", "people.list", "global"),
    ("customer_service", "people.read", "global"),
    ("guardian", "people.read", "family"),
    ("student", "people.read", "self"),
]


def upgrade() -> None:
    bind = op.get_bind()
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
    )
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    for key, (role_id, name) in ROLES.items():
        bind.execute(
            postgresql_insert(roles)
            .values(id=role_id, key=key, name=name)
            .on_conflict_do_nothing(index_elements=[roles.c.key])
        )
    for key, (permission_id, name, sensitive) in PERMISSIONS.items():
        bind.execute(
            postgresql_insert(permissions)
            .values(id=permission_id, key=key, name=name, sensitive=sensitive)
            .on_conflict_do_nothing(index_elements=[permissions.c.key])
        )
    for role_key, permission_key, scope_kind in GRANTS:
        bind.execute(
            postgresql_insert(role_permissions)
            .values(
                role_id=ROLES[role_key][0],
                permission_id=PERMISSIONS[permission_key][0],
                scope_kind=scope_kind,
            )
            .on_conflict_do_nothing()
        )


def downgrade() -> None:
    bind = op.get_bind()
    role_ids = [value[0] for value in ROLES.values()]
    permission_ids = [value[0] for value in PERMISSIONS.values()]
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id = ANY(:role_ids) "
            "AND permission_id = ANY(:permission_ids)"
        ),
        {"role_ids": role_ids, "permission_ids": permission_ids},
    )
    bind.execute(sa.text("DELETE FROM roles WHERE id = ANY(:ids)"), {"ids": role_ids})
    bind.execute(
        sa.text("DELETE FROM permissions WHERE id = ANY(:ids)"),
        {"ids": permission_ids},
    )
