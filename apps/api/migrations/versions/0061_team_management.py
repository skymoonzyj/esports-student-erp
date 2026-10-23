"""Add non-destructive team divisions, roster history, and least-privilege grants."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061_team_management"
down_revision: str | Sequence[str] | None = "0060_esports_assessments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_PROJECT_ID = "30000000-0000-4000-8000-000000000005"
# Reserved migration identities; a renamed legacy seed must retain its ID.
DIVISION_SEEDS = (
    (NEW_PROJECT_ID, "30000000-0000-4000-8000-000000000008", "王者荣耀", 50),
    (
        "30000000-0000-4000-8000-000000000002",
        "30000000-0000-4000-8000-000000000006",
        "和平精英",
        20,
    ),
    (
        "30000000-0000-4000-8000-000000000003",
        "30000000-0000-4000-8000-000000000007",
        "三角洲行动",
        30,
    ),
)
ROLE_IDS = {
    "team_member": "10000000-0000-4000-8000-000000000012",
    "team_manager": "10000000-0000-4000-8000-000000000013",
}
PERMISSION_IDS = {
    "team_memberships.read": "20000000-0000-4000-8000-000000000082",
    "team_memberships.manage": "20000000-0000-4000-8000-000000000083",
    "team_memberships.transfer": "20000000-0000-4000-8000-000000000084",
    "team_manager_assignments.manage": "20000000-0000-4000-8000-000000000085",
}
GRANTS = (
    ("team_member", "team_memberships.read", "self"),
    ("team_manager", "team_memberships.read", "assigned_team"),
    ("team_manager", "team_memberships.manage", "assigned_team"),
    ("super_admin", "team_memberships.read", "global"),
    ("super_admin", "team_memberships.manage", "global"),
    ("super_admin", "team_memberships.transfer", "global"),
    ("super_admin", "team_manager_assignments.manage", "global"),
)
ROLE_SCOPE_CHECK = (
    "scope_kind IN ('global', 'self', 'family', 'person', 'assigned_class', "
    "'assigned_team', 'building', 'floor')"
)
OVERRIDE_SCOPE_CHECK = (
    "scope_kind IN ('global', 'self', 'family', 'person', 'assigned_class', "
    "'assigned_team', 'cohort', 'project', 'enrollment', 'building', 'floor')"
)
OVERRIDE_SCOPE_ID_CHECK = (
    "(scope_kind IN ('global', 'self', 'family') AND scope_id IS NULL) OR "
    "(scope_kind IN ('person', 'assigned_class', 'assigned_team', 'cohort', "
    "'project', 'enrollment', 'building', 'floor') AND scope_id IS NOT NULL)"
)
OLD_ROLE_SCOPE_CHECK = (
    "scope_kind IN ('global', 'self', 'family', 'person', 'assigned_class', 'building', 'floor')"
)
OLD_OVERRIDE_SCOPE_CHECK = (
    "scope_kind IN ('global', 'self', 'family', 'person', 'assigned_class', "
    "'cohort', 'project', 'enrollment', 'building', 'floor')"
)
OLD_OVERRIDE_SCOPE_ID_CHECK = (
    "(scope_kind IN ('global', 'self', 'family') AND scope_id IS NULL) OR "
    "(scope_kind IN ('person', 'assigned_class', 'cohort', 'project', "
    "'enrollment', 'building', 'floor') AND scope_id IS NOT NULL)"
)
NEW_PROJECT_CREATOR_CHECK = (
    "created_by_user_id IS NOT NULL OR id IN ("
    + ", ".join(f"'30000000-0000-4000-8000-{i:012d}'::uuid" for i in range(1, 9))
    + ")"
)
OLD_PROJECT_CREATOR_CHECK = (
    "created_by_user_id IS NOT NULL OR id IN ("
    + ", ".join(f"'30000000-0000-4000-8000-{i:012d}'::uuid" for i in range(1, 5))
    + ")"
)


def _replace_scope_checks(*, old: bool) -> None:
    role = OLD_ROLE_SCOPE_CHECK if old else ROLE_SCOPE_CHECK
    override = OLD_OVERRIDE_SCOPE_CHECK if old else OVERRIDE_SCOPE_CHECK
    scope_id = OLD_OVERRIDE_SCOPE_ID_CHECK if old else OVERRIDE_SCOPE_ID_CHECK
    op.drop_constraint("ck_role_permissions_scope_kind", "role_permissions", type_="check")
    op.create_check_constraint("ck_role_permissions_scope_kind", "role_permissions", role)
    op.drop_constraint(
        "ck_permission_overrides_scope_kind", "user_permission_overrides", type_="check"
    )
    op.create_check_constraint(
        "ck_permission_overrides_scope_kind", "user_permission_overrides", override
    )
    op.drop_constraint(
        "ck_permission_overrides_scope_id", "user_permission_overrides", type_="check"
    )
    op.create_check_constraint(
        "ck_permission_overrides_scope_id", "user_permission_overrides", scope_id
    )


def _validate_fixed_rbac_ids() -> None:
    bind = op.get_bind()
    # Keep the identity check and following inserts in one transaction without a
    # concurrent role/permission writer changing a key or ID between them.
    bind.execute(sa.text("LOCK TABLE roles, permissions IN SHARE ROW EXCLUSIVE MODE"))
    for table, expected in (("roles", ROLE_IDS), ("permissions", PERMISSION_IDS)):
        rows = bind.execute(
            sa.text(
                f"SELECT id, key FROM {table} "
                "WHERE key = ANY(CAST(:keys AS text[])) "
                "OR id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"keys": list(expected), "ids": list(expected.values())},
        )
        for identifier, key in rows:
            if expected.get(key) != str(identifier):
                raise RuntimeError("固定 RBAC ID 映射冲突，拒绝迁移；未修改原有授权")


def _seed_rbac() -> None:
    bind = op.get_bind()
    for key, identifier, name in (
        ("team_member", ROLE_IDS["team_member"], "队员"),
        ("team_manager", ROLE_IDS["team_manager"], "队伍管理"),
    ):
        bind.execute(
            sa.text(
                "INSERT INTO roles (id, key, name) VALUES (CAST(:id AS uuid), :key, :name) "
                "ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name"
            ),
            {"id": identifier, "key": key, "name": name},
        )
    for key, identifier, name in (
        ("team_memberships.read", PERMISSION_IDS["team_memberships.read"], "查看队伍归属与名单"),
        ("team_memberships.manage", PERMISSION_IDS["team_memberships.manage"], "管理队伍成员"),
        ("team_memberships.transfer", PERMISSION_IDS["team_memberships.transfer"], "调动队伍成员"),
        (
            "team_manager_assignments.manage",
            PERMISSION_IDS["team_manager_assignments.manage"],
            "分配队伍管理人员",
        ),
    ):
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, key, name, sensitive) "
                "VALUES (CAST(:id AS uuid), :key, :name, true) "
                "ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, sensitive = true"
            ),
            {"id": identifier, "key": key, "name": name},
        )
    for role_key, permission_key, scope_kind in GRANTS:
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT r.id, p.id, :scope FROM roles r CROSS JOIN permissions p "
                "WHERE r.key = :role_key AND p.key = :permission_key "
                "ON CONFLICT DO NOTHING"
            ),
            {"role_key": role_key, "permission_key": permission_key, "scope": scope_kind},
        )


def _division_inserts() -> list[tuple[str, str, int]]:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE game_projects IN SHARE ROW EXCLUSIVE MODE"))
    rows = list(bind.execute(sa.text("SELECT id, name FROM game_projects")))
    ids = {str(row.id) for row in rows}
    names = {row.name for row in rows}
    inserts = []
    for primary, fallback, name, sort_order in DIVISION_SEEDS:
        if name in names:
            continue
        identifier = primary if primary not in ids else fallback
        if identifier in ids:
            raise RuntimeError("分部初始化 ID 冲突，原 ID 与备用 ID 均被占用；未修改原项目")
        inserts.append((identifier, name, sort_order))
    return inserts


def upgrade() -> None:
    _validate_fixed_rbac_ids()
    division_inserts = _division_inserts()
    op.add_column(
        "game_projects",
        sa.Column("is_team_division", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_constraint("ck_game_projects_creator", "game_projects", type_="check")
    op.create_check_constraint(
        "ck_game_projects_creator", "game_projects", NEW_PROJECT_CREATOR_CHECK
    )

    # Only the migration marks the initial divisions; normal startup seeds never re-enable them.
    for identifier, name, sort_order in division_inserts:
        op.execute(
            sa.text(
                "INSERT INTO game_projects "
                "(id, name, sort_order, is_active, is_team_division, created_by_user_id) "
                "SELECT CAST(:id AS uuid), :name, :sort_order, true, true, NULL "
                "WHERE NOT EXISTS (SELECT 1 FROM game_projects WHERE name = :name) "
                "ON CONFLICT (name) DO NOTHING"
            ).bindparams(id=identifier, name=name, sort_order=sort_order)
        )
    op.execute(
        sa.text(
            "UPDATE game_projects SET is_team_division = true "
            "WHERE name IN ('王者荣耀', '和平精英', '三角洲行动')"
        )
    )

    op.create_table(
        "team_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("result_ids", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "actor_account_id", "idempotency_key", name="uq_team_operations_actor_key"
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_team_operations_fingerprint"
        ),
    )
    op.create_table(
        "team_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("professional_teams.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("joined_on", sa.Date(), nullable=False),
        sa.Column("left_on", sa.Date()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ended_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "open_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_operations.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "close_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_operations.id", ondelete="RESTRICT"),
        ),
        sa.Column("end_kind", sa.String(24)),
        sa.Column("transfer_reason", sa.String(200)),
        sa.CheckConstraint(
            "(left_on IS NULL AND ended_at IS NULL AND ended_by_user_id IS NULL "
            "AND close_operation_id IS NULL AND end_kind IS NULL AND transfer_reason IS NULL) "
            "OR (left_on IS NOT NULL AND ended_at IS NOT NULL AND end_kind IS NOT NULL "
            "AND left_on >= joined_on AND ended_at >= created_at)",
            name="ck_team_memberships_end_pair",
        ),
        sa.CheckConstraint(
            "end_kind IS NULL OR end_kind IN ('left', 'transferred', 'account_deleted')",
            name="ck_team_memberships_end_kind",
        ),
        sa.CheckConstraint(
            "(end_kind = 'transferred' AND transfer_reason IS NOT NULL "
            "AND length(btrim(transfer_reason)) BETWEEN 1 AND 200) "
            "OR (end_kind IS DISTINCT FROM 'transferred' AND transfer_reason IS NULL)",
            name="ck_team_memberships_transfer_reason",
        ),
    )
    op.create_index(
        "uq_team_memberships_open_person",
        "team_memberships",
        ["person_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_team_memberships_person_history",
        "team_memberships",
        ["person_id", "joined_on", "created_at", "id"],
    )
    op.create_index(
        "ix_team_memberships_team_history",
        "team_memberships",
        ["team_id", "joined_on", "created_at", "id"],
    )

    op.create_table(
        "team_manager_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("professional_teams.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ended_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "open_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_operations.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "close_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_operations.id", ondelete="RESTRICT"),
        ),
        sa.Column("end_kind", sa.String(24)),
        sa.CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL "
            "AND close_operation_id IS NULL AND end_kind IS NULL) "
            "OR (ended_at IS NOT NULL AND end_kind IS NOT NULL AND ended_at >= created_at)",
            name="ck_team_manager_assignments_end_pair",
        ),
        sa.CheckConstraint(
            "end_kind IS NULL OR end_kind IN ('unassigned', 'role_removed', 'account_deleted')",
            name="ck_team_manager_assignments_end_kind",
        ),
    )
    op.create_index(
        "uq_team_manager_assignments_open_account_team",
        "team_manager_assignments",
        ["account_id", "team_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_team_manager_assignments_account_history",
        "team_manager_assignments",
        ["account_id", "created_at", "id"],
    )
    op.create_index(
        "ix_team_manager_assignments_team_history",
        "team_manager_assignments",
        ["team_id", "created_at", "id"],
    )

    _replace_scope_checks(old=False)
    _seed_rbac()


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE team_memberships, team_manager_assignments, team_operations, "
            "roles, permissions, role_permissions, user_roles, user_permission_overrides, "
            "game_projects IN ACCESS EXCLUSIVE MODE"
        )
    )
    if bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM team_memberships) "
            "OR EXISTS(SELECT 1 FROM team_manager_assignments) "
            "OR EXISTS(SELECT 1 FROM team_operations)"
        )
    ):
        raise RuntimeError("存在队伍业务数据，拒绝降级；未删除任何记录")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
            "WHERE r.key IN ('team_member', 'team_manager')) "
            "OR EXISTS(SELECT 1 FROM user_permission_overrides upo "
            "JOIN permissions p ON p.id = upo.permission_id "
            "WHERE p.key = ANY(CAST(:keys AS text[]))) "
            "OR EXISTS(SELECT 1 FROM user_permission_overrides WHERE scope_kind = 'assigned_team')"
        ),
        {"keys": list(PERMISSION_IDS)},
    ):
        raise RuntimeError("存在队伍用户角色或授权，拒绝降级；未删除任何授权")
    current_grants = set(
        bind.execute(
            sa.text(
                "SELECT r.key, p.key, rp.scope_kind FROM role_permissions rp "
                "JOIN roles r ON r.id = rp.role_id JOIN permissions p ON p.id = rp.permission_id "
                "WHERE p.key = ANY(CAST(:keys AS text[])) OR rp.scope_kind = 'assigned_team'"
            ),
            {"keys": list(PERMISSION_IDS)},
        ).tuples()
    )
    if current_grants - set(GRANTS):
        raise RuntimeError("存在队伍非默认角色授权，拒绝降级；未删除任何授权")
    created_project_ids = []
    for primary, fallback, name, sort_order in DIVISION_SEEDS:
        for identifier in [primary, fallback] if primary == NEW_PROJECT_ID else [fallback]:
            # Before 0061 these IDs required a real creator. Preserve any such
            # pre-existing row; NULL creator identifies migration-created rows.
            project_row = bind.execute(
                sa.text(
                    "SELECT name, sort_order, is_active FROM game_projects "
                    "WHERE id = CAST(:id AS uuid) AND created_by_user_id IS NULL"
                ),
                {"id": identifier},
            ).one_or_none()
            if project_row is None:
                continue
            if project_row != (name, sort_order, True):
                raise RuntimeError("分部已被修改，拒绝降级；未删除项目")
            created_project_ids.append(identifier)

    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE key = ANY(CAST(:keys AS text[])))"
        ),
        {"keys": list(PERMISSION_IDS)},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE key = ANY(CAST(:keys AS text[]))"),
        {"keys": list(PERMISSION_IDS)},
    )
    bind.execute(
        sa.text("DELETE FROM roles WHERE key = ANY(CAST(:keys AS text[]))"),
        {"keys": list(ROLE_IDS)},
    )
    _replace_scope_checks(old=True)

    op.drop_index("ix_team_manager_assignments_team_history", table_name="team_manager_assignments")
    op.drop_index(
        "ix_team_manager_assignments_account_history", table_name="team_manager_assignments"
    )
    op.drop_index(
        "uq_team_manager_assignments_open_account_team", table_name="team_manager_assignments"
    )
    op.drop_table("team_manager_assignments")
    op.drop_index("ix_team_memberships_team_history", table_name="team_memberships")
    op.drop_index("ix_team_memberships_person_history", table_name="team_memberships")
    op.drop_index("uq_team_memberships_open_person", table_name="team_memberships")
    op.drop_table("team_memberships")
    op.drop_table("team_operations")

    for identifier in created_project_ids:
        # RESTRICT foreign keys deliberately block downgrade of referenced rows.
        bind.execute(
            sa.text("DELETE FROM game_projects WHERE id = CAST(:id AS uuid)"), {"id": identifier}
        )
    op.drop_constraint("ck_game_projects_creator", "game_projects", type_="check")
    op.create_check_constraint(
        "ck_game_projects_creator", "game_projects", OLD_PROJECT_CREATOR_CHECK
    )
    op.drop_column("game_projects", "is_team_division")
