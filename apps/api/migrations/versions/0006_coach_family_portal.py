"""Add coach and family portal grants and query indexes.

Revision ID: 0006_coach_family_portal
Revises: 0005_admissions_enrollment
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0006_coach_family_portal"
down_revision: str | Sequence[str] | None = "0005_admissions_enrollment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PORTAL_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000025")
PORTAL_GRANTS = (
    ("admin", "coach_roster.read", "global"),
    ("coach", "coach_roster.read", "assigned_class"),
    ("coach", "enrollments.read", "assigned_class"),
    ("guardian", "teaching.read", "family"),
    ("guardian", "enrollments.read", "family"),
    ("guardian", "payments.read", "self"),
    ("student", "teaching.read", "self"),
    ("student", "enrollments.read", "self"),
    ("student", "payments.read", "self"),
)


def _role_permission_grants(bind: sa.Connection) -> set[tuple[str, str, str]]:
    return set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id"
            )
        ).tuples()
    )


def upgrade() -> None:
    bind = op.get_bind()
    permission_exists = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM permissions "
            "WHERE id = :permission_id OR key = 'coach_roster.read')"
        ),
        {"permission_id": PORTAL_PERMISSION_ID},
    )
    if permission_exists:
        raise RuntimeError(
            "cannot upgrade portal with preexisting coach roster permission"
        )

    if _role_permission_grants(bind).intersection(PORTAL_GRANTS):
        raise RuntimeError("cannot upgrade portal with preexisting default grants")

    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    bind.execute(
        permissions.insert().values(
            id=PORTAL_PERMISSION_ID,
            key="coach_roster.read",
            name="查看教练下一期名单",
            sensitive=False,
        )
    )

    role_ids: dict[str, UUID] = {
        key: role_id
        for key, role_id in bind.execute(sa.text("SELECT key, id FROM roles")).tuples()
    }
    permission_ids: dict[str, UUID] = {
        key: permission_id
        for key, permission_id in bind.execute(
            sa.text("SELECT key, id FROM permissions")
        ).tuples()
    }
    if permission_ids["coach_roster.read"] != PORTAL_PERMISSION_ID:
        raise RuntimeError("coach_roster.read UUID does not match the fixed seed")

    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    for role_key, permission_key, scope_kind in PORTAL_GRANTS:
        bind.execute(
            role_permissions.insert().values(
                role_id=role_ids[role_key],
                permission_id=permission_ids[permission_key],
                scope_kind=scope_kind,
            )
        )

    op.create_index(
        "ix_cohorts_portal_next",
        "cohorts",
        ["status", "starts_at", "id"],
    )
    op.create_index(
        "ix_coach_assignments_portal_current",
        "coach_assignments",
        ["coach_person_id", "valid_from", "valid_until", "class_id"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_enrollments_student_status_cohort",
        "enrollments",
        ["student_person_id", "status", "cohort_id", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    permission_row = bind.execute(
        sa.text(
            "SELECT id, key, name, sensitive FROM permissions "
            "WHERE id = :permission_id"
        ),
        {"permission_id": PORTAL_PERMISSION_ID},
    ).mappings().one_or_none()
    if permission_row is None or dict(permission_row) != {
        "id": PORTAL_PERMISSION_ID,
        "key": "coach_roster.read",
        "name": "查看教练下一期名单",
        "sensitive": False,
    }:
        raise RuntimeError("cannot downgrade portal with modified permission seed")

    override_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM user_permission_overrides "
            "WHERE permission_id = :permission_id"
        ),
        {"permission_id": PORTAL_PERMISSION_ID},
    )
    if override_count:
        raise RuntimeError("cannot downgrade portal with account permission overrides")

    roster_grants = set(
        bind.execute(
            sa.text(
                "SELECT roles.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "WHERE role_permissions.permission_id = :permission_id"
            ),
            {"permission_id": PORTAL_PERMISSION_ID},
        ).tuples()
    )
    if roster_grants != {
        ("admin", "global"),
        ("coach", "assigned_class"),
    }:
        raise RuntimeError("cannot downgrade portal with customized coach roster grants")

    if not set(PORTAL_GRANTS) <= _role_permission_grants(bind):
        raise RuntimeError("cannot downgrade portal with modified default grants")

    op.drop_index(
        "ix_enrollments_student_status_cohort",
        table_name="enrollments",
    )
    op.drop_index(
        "ix_coach_assignments_portal_current",
        table_name="coach_assignments",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_index("ix_cohorts_portal_next", table_name="cohorts")

    for role_key, permission_key, scope_kind in PORTAL_GRANTS:
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions USING roles, permissions "
                "WHERE role_permissions.role_id = roles.id "
                "AND role_permissions.permission_id = permissions.id "
                "AND roles.key = :role_key "
                "AND permissions.key = :permission_key "
                "AND role_permissions.scope_kind = :scope_kind"
            ),
            {
                "role_key": role_key,
                "permission_key": permission_key,
                "scope_kind": scope_kind,
            },
        )
    bind.execute(
        sa.text(
            "DELETE FROM permissions WHERE id = :permission_id "
            "AND key = 'coach_roster.read'"
        ),
        {"permission_id": PORTAL_PERMISSION_ID},
    )
