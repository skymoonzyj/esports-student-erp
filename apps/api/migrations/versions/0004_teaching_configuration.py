"""Add teaching configuration schema and deterministic seeds.

Revision ID: 0004_teaching_configuration
Revises: 0003_guardian_relationship_history
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

revision: str = "0004_teaching_configuration"
down_revision: str | Sequence[str] | None = "0003_guardian_relationship_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PROJECTS = (
    (UUID("30000000-0000-4000-8000-000000000001"), "英雄联盟", 10),
    (UUID("30000000-0000-4000-8000-000000000002"), "和平精英", 20),
    (UUID("30000000-0000-4000-8000-000000000003"), "三角洲行动", 30),
    (UUID("30000000-0000-4000-8000-000000000004"), "无畏契约", 40),
)

PERMISSIONS = {
    "teaching.read": (
        UUID("20000000-0000-4000-8000-000000000010"),
        "查看教学配置",
        False,
    ),
    "projects.manage": (
        UUID("20000000-0000-4000-8000-000000000011"),
        "管理游戏项目",
        False,
    ),
    "professional_teams.manage": (
        UUID("20000000-0000-4000-8000-000000000012"),
        "管理职业战队",
        False,
    ),
    "cohorts.manage": (
        UUID("20000000-0000-4000-8000-000000000013"),
        "管理届次",
        False,
    ),
    "classes.manage": (
        UUID("20000000-0000-4000-8000-000000000014"),
        "管理班级",
        False,
    ),
    "coach_assignments.manage": (
        UUID("20000000-0000-4000-8000-000000000015"),
        "管理教练分配",
        False,
    ),
}

ROLE_IDS = {
    "admin": UUID("10000000-0000-4000-8000-000000000001"),
    "coach": UUID("10000000-0000-4000-8000-000000000002"),
    "customer_service": UUID("10000000-0000-4000-8000-000000000003"),
}

GRANTS = (
    *(("admin", permission_key, "global") for permission_key in PERMISSIONS),
    ("customer_service", "teaching.read", "global"),
    ("coach", "teaching.read", "assigned_class"),
)

_PROJECT_CREATOR_CHECK = (
    "created_by_user_id IS NOT NULL OR id IN ("
    + ", ".join(f"'{project_id}'::uuid" for project_id, _, _ in PROJECTS)
    + ")"
)


def upgrade() -> None:
    op.create_table(
        "game_projects",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_game_projects_sort_order"),
        sa.CheckConstraint(_PROJECT_CREATOR_CHECK, name="ck_game_projects_creator"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_game_projects_name"),
    )
    op.create_table(
        "professional_teams",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "name",
            name="uq_professional_teams_project_name",
        ),
        sa.UniqueConstraint(
            "id",
            "project_id",
            name="uq_professional_teams_id_project",
        ),
    )
    op.create_table(
        "cohorts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("default_check_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("default_check_out_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "auto_start_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "auto_complete_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_cohorts_dates"),
        sa.CheckConstraint(
            "default_check_out_at >= default_check_in_at",
            name="ck_cohorts_default_check_times",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'open', 'upcoming', 'active', 'completed', 'archived')",
            name="ck_cohorts_status",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "cohort_status_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cohort_id", sa.UUID(), nullable=False),
        sa.Column("old_status", sa.String(length=16), nullable=False),
        sa.Column("new_status", sa.String(length=16), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduler', 'request_reconciliation')",
            name="ck_cohort_status_events_trigger",
        ),
        sa.CheckConstraint(
            "(trigger = 'manual' AND actor_user_id IS NOT NULL) OR "
            "(trigger IN ('scheduler', 'request_reconciliation') "
            "AND actor_user_id IS NULL)",
            name="ck_cohort_status_events_actor",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            ["cohorts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "teaching_classes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cohort_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("class_type", sa.String(length=16), nullable=False),
        sa.Column("professional_team_id", sa.UUID(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column(
            "default_tuition_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "class_type IN ('standard', 'elite')",
            name="ck_teaching_classes_type",
        ),
        sa.CheckConstraint(
            "(class_type = 'standard' AND professional_team_id IS NULL) OR "
            "(class_type = 'elite' AND professional_team_id IS NOT NULL)",
            name="ck_teaching_classes_type_team",
        ),
        sa.CheckConstraint("capacity > 0", name="ck_teaching_classes_capacity"),
        sa.CheckConstraint(
            "default_tuition_cents >= 0",
            name="ck_teaching_classes_tuition",
        ),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            ["cohorts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["professional_team_id", "project_id"],
            ["professional_teams.id", "professional_teams.project_id"],
            name="fk_teaching_classes_team_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cohort_id",
            "name",
            name="uq_teaching_classes_cohort_name",
        ),
        sa.UniqueConstraint(
            "id",
            "cohort_id",
            name="uq_teaching_classes_id_cohort",
        ),
    )
    op.create_table(
        "coach_assignments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("coach_person_id", sa.UUID(), nullable=False),
        sa.Column("assignment_type", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by_user_id", sa.UUID(), nullable=True),
        sa.Column("end_reason", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "assignment_type IN ('primary', 'assistant')",
            name="ck_coach_assignments_type",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_coach_assignments_valid_until",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at > valid_from",
            name="ck_coach_assignments_ended_at",
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_by_user_id IS NOT NULL "
            "AND end_reason IS NOT NULL AND btrim(end_reason) <> '')",
            name="ck_coach_assignments_end_fields",
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["teaching_classes.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["coach_person_id"],
            ["people.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ended_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        ALTER TABLE coach_assignments ADD CONSTRAINT excl_coach_assignments_one_primary
        EXCLUDE USING gist (
          class_id WITH =,
          tstzrange(
            valid_from,
            COALESCE(
              LEAST(valid_until, ended_at),
              valid_until,
              ended_at,
              'infinity'::timestamptz
            ),
            '[)'
          ) WITH &&
        ) WHERE (assignment_type = 'primary')
        """
    )
    op.execute(
        """
        ALTER TABLE coach_assignments
        ADD CONSTRAINT excl_coach_assignments_no_duplicate_coach
        EXCLUDE USING gist (
          class_id WITH =,
          coach_person_id WITH =,
          tstzrange(
            valid_from,
            COALESCE(
              LEAST(valid_until, ended_at),
              valid_until,
              ended_at,
              'infinity'::timestamptz
            ),
            '[)'
          ) WITH &&
        )
        """
    )

    bind = op.get_bind()
    projects = sa.table(
        "game_projects",
        sa.column("id", sa.UUID()),
        sa.column("name", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_by_user_id", sa.UUID()),
    )
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.UUID()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.UUID()),
        sa.column("permission_id", sa.UUID()),
        sa.column("scope_kind", sa.String()),
    )
    for project_id, name, sort_order in PROJECTS:
        bind.execute(
            postgresql_insert(projects)
            .values(
                id=project_id,
                name=name,
                sort_order=sort_order,
                is_active=True,
                created_by_user_id=None,
            )
            .on_conflict_do_nothing()
        )
    for key, (permission_id, name, sensitive) in PERMISSIONS.items():
        bind.execute(
            postgresql_insert(permissions)
            .values(
                id=permission_id,
                key=key,
                name=name,
                sensitive=sensitive,
            )
            .on_conflict_do_nothing(index_elements=[permissions.c.key])
        )
    for role_key, permission_key, scope_kind in GRANTS:
        bind.execute(
            postgresql_insert(role_permissions)
            .values(
                role_id=ROLE_IDS[role_key],
                permission_id=PERMISSIONS[permission_key][0],
                scope_kind=scope_kind,
            )
            .on_conflict_do_nothing()
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM game_projects
                WHERE id NOT IN (
                    '30000000-0000-4000-8000-000000000001'::uuid,
                    '30000000-0000-4000-8000-000000000002'::uuid,
                    '30000000-0000-4000-8000-000000000003'::uuid,
                    '30000000-0000-4000-8000-000000000004'::uuid
                )
            ) OR EXISTS (SELECT 1 FROM professional_teams)
              OR EXISTS (SELECT 1 FROM cohorts)
              OR EXISTS (SELECT 1 FROM cohort_status_events)
              OR EXISTS (SELECT 1 FROM teaching_classes)
              OR EXISTS (SELECT 1 FROM coach_assignments) THEN
                RAISE EXCEPTION
                    'cannot downgrade teaching configuration with non-seed teaching data';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        ('30000000-0000-4000-8000-000000000001'::uuid, '英雄联盟', 10),
                        ('30000000-0000-4000-8000-000000000002'::uuid, '和平精英', 20),
                        ('30000000-0000-4000-8000-000000000003'::uuid, '三角洲行动', 30),
                        ('30000000-0000-4000-8000-000000000004'::uuid, '无畏契约', 40)
                ) AS expected(id, name, sort_order)
                LEFT JOIN game_projects AS actual ON actual.id = expected.id
                WHERE actual.id IS NULL
                   OR actual.name IS DISTINCT FROM expected.name
                   OR actual.sort_order IS DISTINCT FROM expected.sort_order
                   OR actual.is_active IS DISTINCT FROM true
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade teaching configuration with modified initial projects';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM user_permission_overrides
                WHERE permission_id IN (
                    '20000000-0000-4000-8000-000000000010'::uuid,
                    '20000000-0000-4000-8000-000000000011'::uuid,
                    '20000000-0000-4000-8000-000000000012'::uuid,
                    '20000000-0000-4000-8000-000000000013'::uuid,
                    '20000000-0000-4000-8000-000000000014'::uuid,
                    '20000000-0000-4000-8000-000000000015'::uuid
                )
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade teaching configuration with account permission overrides';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        ('20000000-0000-4000-8000-000000000010'::uuid,
                         'teaching.read', '查看教学配置', false),
                        ('20000000-0000-4000-8000-000000000011'::uuid,
                         'projects.manage', '管理游戏项目', false),
                        ('20000000-0000-4000-8000-000000000012'::uuid,
                         'professional_teams.manage', '管理职业战队', false),
                        ('20000000-0000-4000-8000-000000000013'::uuid,
                         'cohorts.manage', '管理届次', false),
                        ('20000000-0000-4000-8000-000000000014'::uuid,
                         'classes.manage', '管理班级', false),
                        ('20000000-0000-4000-8000-000000000015'::uuid,
                         'coach_assignments.manage', '管理教练分配', false)
                ) AS expected(id, key, name, sensitive)
                LEFT JOIN permissions AS actual ON actual.id = expected.id
                WHERE actual.id IS NULL
                   OR actual.key IS DISTINCT FROM expected.key
                   OR actual.name IS DISTINCT FROM expected.name
                   OR actual.sensitive IS DISTINCT FROM expected.sensitive
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade teaching configuration with modified permission seeds';
            END IF;

            IF (
                SELECT count(*)
                FROM role_permissions
                WHERE permission_id IN (
                    '20000000-0000-4000-8000-000000000010'::uuid,
                    '20000000-0000-4000-8000-000000000011'::uuid,
                    '20000000-0000-4000-8000-000000000012'::uuid,
                    '20000000-0000-4000-8000-000000000013'::uuid,
                    '20000000-0000-4000-8000-000000000014'::uuid,
                    '20000000-0000-4000-8000-000000000015'::uuid
                )
            ) <> 8 OR EXISTS (
                SELECT 1
                FROM role_permissions
                WHERE permission_id IN (
                    '20000000-0000-4000-8000-000000000010'::uuid,
                    '20000000-0000-4000-8000-000000000011'::uuid,
                    '20000000-0000-4000-8000-000000000012'::uuid,
                    '20000000-0000-4000-8000-000000000013'::uuid,
                    '20000000-0000-4000-8000-000000000014'::uuid,
                    '20000000-0000-4000-8000-000000000015'::uuid
                ) AND NOT (
                    (
                        role_id = '10000000-0000-4000-8000-000000000001'::uuid
                        AND scope_kind = 'global'
                    ) OR (
                        role_id = '10000000-0000-4000-8000-000000000003'::uuid
                        AND permission_id =
                            '20000000-0000-4000-8000-000000000010'::uuid
                        AND scope_kind = 'global'
                    ) OR (
                        role_id = '10000000-0000-4000-8000-000000000002'::uuid
                        AND permission_id =
                            '20000000-0000-4000-8000-000000000010'::uuid
                        AND scope_kind = 'assigned_class'
                    )
                )
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade teaching configuration with customized role grants';
            END IF;
        END $$
        """
    )

    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (
            '20000000-0000-4000-8000-000000000010'::uuid,
            '20000000-0000-4000-8000-000000000011'::uuid,
            '20000000-0000-4000-8000-000000000012'::uuid,
            '20000000-0000-4000-8000-000000000013'::uuid,
            '20000000-0000-4000-8000-000000000014'::uuid,
            '20000000-0000-4000-8000-000000000015'::uuid
        )
        """
    )
    op.execute(
        """
        DELETE FROM permissions
        WHERE id IN (
            '20000000-0000-4000-8000-000000000010'::uuid,
            '20000000-0000-4000-8000-000000000011'::uuid,
            '20000000-0000-4000-8000-000000000012'::uuid,
            '20000000-0000-4000-8000-000000000013'::uuid,
            '20000000-0000-4000-8000-000000000014'::uuid,
            '20000000-0000-4000-8000-000000000015'::uuid
        )
        """
    )
    op.execute(
        """
        DELETE FROM game_projects
        WHERE id IN (
            '30000000-0000-4000-8000-000000000001'::uuid,
            '30000000-0000-4000-8000-000000000002'::uuid,
            '30000000-0000-4000-8000-000000000003'::uuid,
            '30000000-0000-4000-8000-000000000004'::uuid
        )
        """
    )
    op.drop_table("coach_assignments")
    op.drop_table("teaching_classes")
    op.drop_table("cohort_status_events")
    op.drop_table("cohorts")
    op.drop_table("professional_teams")
    op.drop_table("game_projects")
