"""Add project coach configuration and enrollment coach selection."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_project_coach_selection"
down_revision: str | Sequence[str] | None = "0048_admin_asset_boundary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_KEY = "project_coaches.manage"
PERMISSION_ID = "20000000-0000-4000-8000-000000000077"

OLD_SELF_SERVICE_CHECK = (
    "(application_source IS NULL AND selected_project_id IS NULL "
    "AND selected_program_id IS NULL AND project_name_snapshot IS NULL "
    "AND program_name_snapshot IS NULL AND payment_preference IS NULL) OR "
    "(application_source = 'self_service' AND student_person_id IS NOT NULL "
    "AND selected_project_id IS NOT NULL AND selected_program_id IS NOT NULL "
    "AND project_name_snapshot IS NOT NULL AND btrim(project_name_snapshot) <> '' "
    "AND program_name_snapshot IS NOT NULL AND btrim(program_name_snapshot) <> '' "
    "AND payment_preference IS NOT NULL AND agreed_tuition_cents IS NOT NULL)"
)

NEW_SELF_SERVICE_CHECK = (
    "(application_source IS NULL AND selected_project_id IS NULL "
    "AND selected_program_id IS NULL AND project_name_snapshot IS NULL "
    "AND program_name_snapshot IS NULL AND selected_coach_person_id IS NULL "
    "AND coach_name_snapshot IS NULL AND payment_preference IS NULL) OR "
    "(application_source = 'self_service' AND student_person_id IS NOT NULL "
    "AND selected_project_id IS NOT NULL AND selected_program_id IS NOT NULL "
    "AND project_name_snapshot IS NOT NULL AND btrim(project_name_snapshot) <> '' "
    "AND program_name_snapshot IS NOT NULL AND btrim(program_name_snapshot) <> '' "
    "AND ((selected_coach_person_id IS NULL AND coach_name_snapshot IS NULL) OR "
    "(selected_coach_person_id IS NOT NULL AND coach_name_snapshot IS NOT NULL "
    "AND btrim(coach_name_snapshot) <> '')) "
    "AND payment_preference IS NOT NULL AND agreed_tuition_cents IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        "project_coach_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("coach_person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True)),
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
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            name="fk_project_coach_assignments_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["coach_person_id"],
            ["people.id"],
            name="fk_project_coach_assignments_coach",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            name="fk_project_coach_assignments_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            name="fk_project_coach_assignments_updated_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "coach_person_id",
            name="uq_project_coach_assignments_project_coach",
        ),
    )
    op.create_index(
        "ix_project_coach_assignments_project_active",
        "project_coach_assignments",
        ["project_id", "is_active"],
    )
    op.create_index(
        "ix_project_coach_assignments_coach_active",
        "project_coach_assignments",
        ["coach_person_id", "is_active"],
    )

    op.add_column(
        "admission_cases",
        sa.Column("selected_coach_person_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "admission_cases",
        sa.Column("coach_name_snapshot", sa.String(length=120)),
    )
    op.create_foreign_key(
        "fk_admission_cases_selected_coach",
        "admission_cases",
        "people",
        ["selected_coach_person_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "ck_admission_cases_self_service_fields",
        "admission_cases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_admission_cases_self_service_fields",
        "admission_cases",
        NEW_SELF_SERVICE_CHECK,
    )

    op.execute(
        sa.text(
            "INSERT INTO permissions (id, key, name, sensitive) VALUES "
            "(CAST(:id AS uuid), :key, :name, false) "
            "ON CONFLICT (key) DO UPDATE SET "
            "name = EXCLUDED.name, sensitive = EXCLUDED.sensitive"
        ).bindparams(
            id=PERMISSION_ID,
            key=PERMISSION_KEY,
            name="配置项目可报名教练",
        )
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

    op.drop_constraint(
        "ck_admission_cases_self_service_fields",
        "admission_cases",
        type_="check",
    )
    op.drop_constraint(
        "fk_admission_cases_selected_coach",
        "admission_cases",
        type_="foreignkey",
    )
    op.drop_column("admission_cases", "coach_name_snapshot")
    op.drop_column("admission_cases", "selected_coach_person_id")
    op.create_check_constraint(
        "ck_admission_cases_self_service_fields",
        "admission_cases",
        OLD_SELF_SERVICE_CHECK,
    )

    op.drop_index(
        "ix_project_coach_assignments_coach_active",
        table_name="project_coach_assignments",
    )
    op.drop_index(
        "ix_project_coach_assignments_project_active",
        table_name="project_coach_assignments",
    )
    op.drop_table("project_coach_assignments")
