"""Add evaluation data, media, staffing, and permission foundations.

Revision ID: 0008_evaluation_data_media_foundation
Revises: 0007_evaluation_page_integration
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_evaluation_data_media_foundation"
down_revision: str | Sequence[str] | None = "0007_evaluation_page_integration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVALUATION_FOUNDATION_PERMISSIONS = {
    "evaluation_media.upload": (
        UUID("20000000-0000-4000-8000-000000000027"),
        "上传评价媒体",
        True,
    ),
    "evaluation_media.original.read": (
        UUID("20000000-0000-4000-8000-000000000028"),
        "查看评价媒体原图",
        True,
    ),
    "analysis_profiles.manage": (
        UUID("20000000-0000-4000-8000-000000000030"),
        "管理分析配置",
        True,
    ),
    "student_game_accounts.manage": (
        UUID("20000000-0000-4000-8000-000000000031"),
        "管理学员游戏账号",
        True,
    ),
    "cohort_staff.manage": (
        UUID("20000000-0000-4000-8000-000000000036"),
        "管理届次员工分配",
        True,
    ),
}
EVALUATION_FOUNDATION_GRANTS = (
    *(("admin", key, "global") for key in EVALUATION_FOUNDATION_PERMISSIONS),
    ("student", "student_game_accounts.manage", "self"),
    ("student", "evaluation_media.original.read", "self"),
    ("guardian", "evaluation_media.original.read", "family"),
    ("coach", "evaluation_media.original.read", "assigned_class"),
)
EVALUATION_LIFE_TEACHER_ROLE_ID = UUID("41000000-0000-4000-8000-000000000001")


def _seed_permissions(bind: sa.Connection) -> None:
    permission_ids = [values[0] for values in EVALUATION_FOUNDATION_PERMISSIONS.values()]
    permission_keys = list(EVALUATION_FOUNDATION_PERMISSIONS)
    collision = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM permissions "
            "WHERE id = ANY(CAST(:ids AS uuid[])) "
            "OR key = ANY(CAST(:keys AS text[])))"
        ),
        {"ids": permission_ids, "keys": permission_keys},
    )
    if collision:
        raise RuntimeError(
            "cannot upgrade evaluation foundation with preexisting permission seeds"
        )

    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    for key, (permission_id, name, sensitive) in EVALUATION_FOUNDATION_PERMISSIONS.items():
        bind.execute(
            permissions.insert().values(
                id=permission_id,
                key=key,
                name=name,
                sensitive=sensitive,
            )
        )

    role_ids: dict[str, UUID] = {
        key: role_id
        for key, role_id in bind.execute(sa.text("SELECT key, id FROM roles")).tuples()
    }
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    for role_key, permission_key, scope_kind in EVALUATION_FOUNDATION_GRANTS:
        bind.execute(
            role_permissions.insert().values(
                role_id=role_ids[role_key],
                permission_id=EVALUATION_FOUNDATION_PERMISSIONS[permission_key][0],
                scope_kind=scope_kind,
            )
        )


def _extend_existing_tables() -> None:
    op.add_column(
        "student_profiles",
        sa.Column(
            "game_account_limit",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_student_profiles_game_account_limit",
        "student_profiles",
        "game_account_limit >= 3",
    )

    op.drop_constraint(
        "ck_permission_overrides_scope_kind",
        "user_permission_overrides",
        type_="check",
    )
    op.drop_constraint(
        "ck_permission_overrides_scope_id",
        "user_permission_overrides",
        type_="check",
    )
    op.add_column(
        "user_permission_overrides",
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_permission_overrides",
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_permission_overrides",
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "user_permission_overrides",
        sa.Column("grant_reason", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "user_permission_overrides",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_permission_overrides_created_by_user",
        "user_permission_overrides",
        "user_accounts",
        ["created_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_permission_overrides_scope_kind",
        "user_permission_overrides",
        "scope_kind IN ("
        "'global', 'self', 'family', 'person', 'assigned_class', "
        "'cohort', 'project', 'enrollment'"
        ")",
    )
    op.create_check_constraint(
        "ck_permission_overrides_scope_id",
        "user_permission_overrides",
        "(scope_kind IN ('global', 'self', 'family') AND scope_id IS NULL) OR "
        "(scope_kind IN ("
        "'person', 'assigned_class', 'cohort', 'project', 'enrollment'"
        ") AND scope_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_permission_overrides_validity",
        "user_permission_overrides",
        "valid_until IS NULL OR "
        "(valid_from IS NOT NULL AND valid_until > valid_from)",
    )

    op.create_unique_constraint(
        "uq_enrollments_id_student_person",
        "enrollments",
        ["id", "student_person_id"],
    )


def _create_staff_tables() -> None:
    op.create_table(
        "evaluation_staff_roles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
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
            "key ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'",
            name="ck_evaluation_staff_roles_key",
        ),
        sa.CheckConstraint(
            "btrim(name) <> ''",
            name="ck_evaluation_staff_roles_name",
        ),
        sa.CheckConstraint(
            "category IN ('teaching', 'life_service', 'operations')",
            name="ck_evaluation_staff_roles_category",
        ),
        sa.CheckConstraint(
            "created_by_user_id IS NOT NULL OR "
            "(id = '41000000-0000-4000-8000-000000000001'::uuid "
            "AND key = 'life_teacher' AND is_builtin IS TRUE)",
            name="ck_evaluation_staff_roles_creator",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "cohort_staff_assignments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cohort_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("staff_person_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("class_id", sa.UUID(), nullable=True),
        sa.Column("role_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("role_category_snapshot", sa.String(length=24), nullable=False),
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
            "(project_id IS NULL) <> (class_id IS NULL)",
            name="ck_cohort_staff_assignments_target",
        ),
        sa.CheckConstraint(
            "btrim(role_name_snapshot) <> ''",
            name="ck_cohort_staff_assignments_role_name_snapshot",
        ),
        sa.CheckConstraint(
            "role_category_snapshot IN ('teaching', 'life_service', 'operations')",
            name="ck_cohort_staff_assignments_role_category_snapshot",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_cohort_staff_assignments_valid_until",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at > valid_from",
            name="ck_cohort_staff_assignments_ended_at",
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_by_user_id IS NOT NULL "
            "AND end_reason IS NOT NULL AND btrim(end_reason) <> '')",
            name="ck_cohort_staff_assignments_end_fields",
        ),
        sa.ForeignKeyConstraint(["cohort_id"], ["cohorts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["evaluation_staff_roles.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["staff_person_id"],
            ["people.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_id", "cohort_id"],
            ["teaching_classes.id", "teaching_classes.cohort_id"],
            name="fk_cohort_staff_assignments_class_cohort",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ended_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
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
    effective_range = (
        "tstzrange(valid_from, "
        "COALESCE(LEAST(valid_until, ended_at), valid_until, ended_at, "
        "'infinity'::timestamptz), '[)')"
    )
    op.execute(
        "ALTER TABLE cohort_staff_assignments ADD CONSTRAINT "
        "excl_cohort_staff_assignments_project_overlap EXCLUDE USING gist ("
        "staff_person_id WITH =, role_id WITH =, project_id WITH =, "
        f"{effective_range} WITH &&) WHERE (project_id IS NOT NULL)"
    )
    op.execute(
        "ALTER TABLE cohort_staff_assignments ADD CONSTRAINT "
        "excl_cohort_staff_assignments_class_overlap EXCLUDE USING gist ("
        "staff_person_id WITH =, role_id WITH =, class_id WITH =, "
        f"{effective_range} WITH &&) WHERE (class_id IS NOT NULL)"
    )
    op.create_index(
        "ix_cohort_staff_assignments_project_current",
        "cohort_staff_assignments",
        ["project_id", "role_id", "staff_person_id", "valid_from", "valid_until"],
        postgresql_where=sa.text("project_id IS NOT NULL AND ended_at IS NULL"),
    )
    op.create_index(
        "ix_cohort_staff_assignments_class_current",
        "cohort_staff_assignments",
        ["class_id", "role_id", "staff_person_id", "valid_from", "valid_until"],
        postgresql_where=sa.text("class_id IS NOT NULL AND ended_at IS NULL"),
    )
    op.create_index(
        "ix_cohort_staff_assignments_staff_history",
        "cohort_staff_assignments",
        ["staff_person_id", "role_id", "valid_from", "id"],
    )
    op.create_table(
        "cohort_staff_assignment_permissions",
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("permission_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["cohort_staff_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permissions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("assignment_id", "permission_id"),
    )
    op.execute(
        """
        CREATE FUNCTION enforce_used_evaluation_staff_role_immutable()
        RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM cohort_staff_assignments
            WHERE role_id = OLD.id
          ) THEN
            IF OLD.key IS DISTINCT FROM NEW.key
               OR OLD.name IS DISTINCT FROM NEW.name
               OR OLD.category IS DISTINCT FROM NEW.category
               OR OLD.is_builtin IS DISTINCT FROM NEW.is_builtin
               OR OLD.created_by_user_id IS DISTINCT FROM NEW.created_by_user_id
               OR (OLD.is_active IS FALSE AND NEW.is_active IS TRUE) THEN
              RAISE EXCEPTION 'used evaluation staff role is immutable';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_used_evaluation_staff_roles_immutable
        BEFORE UPDATE ON evaluation_staff_roles
        FOR EACH ROW EXECUTE FUNCTION enforce_used_evaluation_staff_role_immutable()
        """
    )


def _create_game_tables() -> None:
    op.create_table(
        "student_game_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("student_person_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("identifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("identifier_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unbound_by_user_id", sa.UUID(), nullable=True),
        sa.Column("unbind_reason", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
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
            "btrim(identifier_ciphertext) <> ''",
            name="ck_student_game_accounts_ciphertext",
        ),
        sa.CheckConstraint(
            "identifier_digest ~ '^[0-9a-f]{64}$'",
            name="ck_student_game_accounts_digest",
        ),
        sa.CheckConstraint(
            "unbound_at IS NULL OR unbound_at > valid_from",
            name="ck_student_game_accounts_unbound_at",
        ),
        sa.CheckConstraint(
            "(unbound_at IS NULL AND unbound_by_user_id IS NULL "
            "AND unbind_reason IS NULL) OR "
            "(unbound_at IS NOT NULL AND unbound_by_user_id IS NOT NULL "
            "AND unbind_reason IS NOT NULL AND btrim(unbind_reason) <> '')",
            name="ck_student_game_accounts_unbound_fields",
        ),
        sa.ForeignKeyConstraint(
            ["student_person_id"],
            ["student_profiles.person_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["unbound_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_student_game_accounts_active_project_digest",
        "student_game_accounts",
        ["project_id", "identifier_digest"],
        unique=True,
        postgresql_where=sa.text("unbound_at IS NULL"),
    )
    op.execute(
        "ALTER TABLE student_game_accounts ADD CONSTRAINT "
        "excl_student_game_accounts_project_digest_overlap EXCLUDE USING gist ("
        "project_id WITH =, identifier_digest WITH =, "
        "tstzrange(valid_from, COALESCE(unbound_at, 'infinity'::timestamptz), '[)') "
        "WITH &&)"
    )
    op.create_index(
        "ix_student_game_accounts_student_current",
        "student_game_accounts",
        ["student_person_id", "project_id"],
        postgresql_where=sa.text("unbound_at IS NULL"),
    )
    op.create_index(
        "ix_student_game_accounts_history_match",
        "student_game_accounts",
        ["project_id", "identifier_digest", "valid_from", "unbound_at"],
    )
    op.create_index(
        "ix_student_game_accounts_student_history",
        "student_game_accounts",
        ["student_person_id", sa.text("valid_from DESC"), sa.text("id DESC")],
    )

    op.create_table(
        "student_auxiliary_phones",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("student_person_id", sa.UUID(), nullable=False),
        sa.Column("phone_ciphertext", sa.Text(), nullable=False),
        sa.Column("phone_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_by_user_id", sa.UUID(), nullable=True),
        sa.Column("disable_reason", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
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
            "btrim(phone_ciphertext) <> ''",
            name="ck_student_auxiliary_phones_ciphertext",
        ),
        sa.CheckConstraint(
            "phone_digest ~ '^[0-9a-f]{64}$'",
            name="ck_student_auxiliary_phones_digest",
        ),
        sa.CheckConstraint(
            "disabled_at IS NULL OR disabled_at > valid_from",
            name="ck_student_auxiliary_phones_disabled_at",
        ),
        sa.CheckConstraint(
            "(disabled_at IS NULL AND disabled_by_user_id IS NULL "
            "AND disable_reason IS NULL) OR "
            "(disabled_at IS NOT NULL AND disabled_by_user_id IS NOT NULL "
            "AND disable_reason IS NOT NULL AND btrim(disable_reason) <> '')",
            name="ck_student_auxiliary_phones_disabled_fields",
        ),
        sa.ForeignKeyConstraint(
            ["student_person_id"],
            ["student_profiles.person_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["disabled_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_student_auxiliary_phones_active_student_digest",
        "student_auxiliary_phones",
        ["student_person_id", "phone_digest"],
        unique=True,
        postgresql_where=sa.text("disabled_at IS NULL"),
    )
    op.create_index(
        "ix_student_auxiliary_phones_active_lookup",
        "student_auxiliary_phones",
        ["phone_digest", "student_person_id"],
        postgresql_where=sa.text("disabled_at IS NULL"),
    )
    op.create_index(
        "ix_student_auxiliary_phones_student_history",
        "student_auxiliary_phones",
        ["student_person_id", sa.text("valid_from DESC"), sa.text("id DESC")],
    )


def _create_analysis_table() -> None:
    op.create_table(
        "project_analysis_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("adapter_id", sa.String(length=80), nullable=True),
        sa.Column("adapter_version", sa.String(length=40), nullable=True),
        sa.Column("scoring_rule_version", sa.String(length=40), nullable=True),
        sa.Column(
            "required_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "confidence_thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "auto_publish_warning_levels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("publish_mode", sa.String(length=24), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("configured_by_user_id", sa.UUID(), nullable=True),
        sa.Column("change_reason", sa.String(length=300), nullable=False),
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
            "version >= 1",
            name="ck_project_analysis_profiles_version",
        ),
        sa.CheckConstraint(
            "mode IN ('manual_only', 'automatic')",
            name="ck_project_analysis_profiles_mode",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(required_fields) = 'array'",
            name="ck_project_analysis_profiles_required_fields",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(confidence_thresholds) = 'object'",
            name="ck_project_analysis_profiles_confidence_thresholds",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(auto_publish_warning_levels) = 'array'",
            name="ck_project_analysis_profiles_warning_levels",
        ),
        sa.CheckConstraint(
            "publish_mode IN ('auto_publish', 'review_required')",
            name="ck_project_analysis_profiles_publish_mode",
        ),
        sa.CheckConstraint(
            "mode <> 'manual_only' OR "
            "(adapter_id IS NULL AND adapter_version IS NULL "
            "AND scoring_rule_version IS NULL AND required_fields = '[]'::jsonb "
            "AND confidence_thresholds = '{}'::jsonb "
            "AND auto_publish_warning_levels = '[]'::jsonb "
            "AND publish_mode = 'review_required')",
            name="ck_project_analysis_profiles_manual_only",
        ),
        sa.CheckConstraint(
            "configured_by_user_id IS NOT NULL OR "
            "(version = 1 AND mode = 'manual_only' "
            "AND change_reason = 'migration backfill')",
            name="ck_project_analysis_profiles_configured_by",
        ),
        sa.CheckConstraint(
            "btrim(change_reason) <> ''",
            name="ck_project_analysis_profiles_change_reason",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["configured_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "version",
            name="uq_project_analysis_profiles_project_version",
        ),
    )
    op.execute(
        "INSERT INTO project_analysis_profiles ("
        "id, project_id, version, mode, required_fields, confidence_thresholds, "
        "auto_publish_warning_levels, publish_mode, effective_from, "
        "configured_by_user_id, change_reason) "
        "SELECT gen_random_uuid(), id, 1, 'manual_only', '[]'::jsonb, '{}'::jsonb, "
        "'[]'::jsonb, 'review_required', CURRENT_TIMESTAMP, NULL, "
        "'migration backfill' FROM game_projects"
    )
    op.execute(
        """
        CREATE FUNCTION enforce_project_analysis_profile_identity_immutable()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.project_id IS DISTINCT FROM NEW.project_id
             OR OLD.version IS DISTINCT FROM NEW.version THEN
            RAISE EXCEPTION 'analysis profile project and version are immutable';
          ELSIF OLD IS DISTINCT FROM NEW THEN
            RAISE EXCEPTION 'analysis profile rows are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_project_analysis_profiles_identity_immutable
        BEFORE UPDATE ON project_analysis_profiles
        FOR EACH ROW EXECUTE FUNCTION enforce_project_analysis_profile_identity_immutable()
        """
    )


def _create_media_tables() -> None:
    op.create_table(
        "evaluation_media_blobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("preview_storage_key", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=32), nullable=False),
        sa.Column("canonical_extension", sa.String(length=8), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width_pixels", sa.Integer(), nullable=False),
        sa.Column("height_pixels", sa.Integer(), nullable=False),
        sa.Column("storage_status", sa.String(length=16), nullable=False),
        sa.Column("validation_status", sa.String(length=16), nullable=False),
        sa.Column("retention_status", sa.String(length=16), nullable=False),
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
            "btrim(storage_key) <> '' AND btrim(preview_storage_key) <> '' "
            "AND left(storage_key, 1) <> '/' AND left(preview_storage_key, 1) <> '/' "
            "AND position('..' IN storage_key) = 0 "
            "AND position('..' IN preview_storage_key) = 0",
            name="ck_evaluation_media_blobs_storage_keys",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_media_blobs_sha256_format",
        ),
        sa.CheckConstraint(
            "(mime_type = 'image/jpeg' AND canonical_extension = 'jpg') OR "
            "(mime_type = 'image/png' AND canonical_extension = 'png') OR "
            "(mime_type = 'image/webp' AND canonical_extension = 'webp')",
            name="ck_evaluation_media_blobs_mime_extension",
        ),
        sa.CheckConstraint(
            "size_bytes BETWEEN 1 AND 20971520",
            name="ck_evaluation_media_blobs_size",
        ),
        sa.CheckConstraint(
            "width_pixels > 0 AND height_pixels > 0 "
            "AND width_pixels::bigint * height_pixels::bigint <= 50000000",
            name="ck_evaluation_media_blobs_dimensions",
        ),
        sa.CheckConstraint(
            "storage_status IN ('available', 'missing')",
            name="ck_evaluation_media_blobs_storage_status",
        ),
        sa.CheckConstraint(
            "validation_status IN ('validated', 'invalid')",
            name="ck_evaluation_media_blobs_validation_status",
        ),
        sa.CheckConstraint(
            "retention_status IN ('retained', 'archived')",
            name="ck_evaluation_media_blobs_retention_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "storage_key",
            name="uq_evaluation_media_blobs_storage_key",
        ),
        sa.UniqueConstraint(
            "preview_storage_key",
            name="uq_evaluation_media_blobs_preview_storage_key",
        ),
        sa.UniqueConstraint("sha256", name="uq_evaluation_media_blobs_sha256"),
    )
    op.create_table(
        "evaluation_media_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("blob_id", sa.UUID(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("student_person_id", sa.UUID(), nullable=True),
        sa.Column("enrollment_id", sa.UUID(), nullable=True),
        sa.Column("class_id", sa.UUID(), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_by_user_id", sa.UUID(), nullable=True),
        sa.Column("match_reason", sa.String(length=300), nullable=True),
        sa.Column("delegated_upload_reason", sa.String(length=300), nullable=True),
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
            "(student_person_id IS NULL AND enrollment_id IS NULL AND class_id IS NULL "
            "AND matched_at IS NULL AND matched_by_user_id IS NULL "
            "AND match_reason IS NULL) OR "
            "(student_person_id IS NOT NULL AND enrollment_id IS NOT NULL "
            "AND class_id IS NOT NULL AND matched_at IS NOT NULL "
            "AND matched_by_user_id IS NOT NULL AND match_reason IS NOT NULL "
            "AND btrim(match_reason) <> '')",
            name="ck_evaluation_media_assets_target_fields",
        ),
        sa.CheckConstraint(
            "delegated_upload_reason IS NULL OR btrim(delegated_upload_reason) <> ''",
            name="ck_evaluation_media_assets_delegated_reason",
        ),
        sa.ForeignKeyConstraint(
            ["blob_id"],
            ["evaluation_media_blobs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["game_projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_person_id"],
            ["student_profiles.person_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id", "student_person_id"],
            ["enrollments.id", "enrollments.student_person_id"],
            name="fk_evaluation_media_assets_enrollment_student",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["teaching_classes.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["matched_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_evaluation_media_assets_matched_target_blob",
        "evaluation_media_assets",
        ["student_person_id", "enrollment_id", "project_id", "blob_id"],
        unique=True,
        postgresql_where=sa.text("enrollment_id IS NOT NULL"),
    )
    op.create_index(
        "ix_evaluation_media_assets_pending",
        "evaluation_media_assets",
        ["project_id", "created_at", "id"],
        postgresql_where=sa.text("enrollment_id IS NULL"),
    )
    op.create_index(
        "ix_evaluation_media_assets_matched",
        "evaluation_media_assets",
        ["student_person_id", "enrollment_id", "project_id"],
        postgresql_where=sa.text("enrollment_id IS NOT NULL"),
    )
    op.create_index(
        "ix_evaluation_media_assets_upload_history",
        "evaluation_media_assets",
        ["uploaded_by_user_id", "created_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION enforce_evaluation_media_asset_target_immutable()
        RETURNS trigger AS $$
        BEGIN
          IF ROW(
            OLD.student_person_id,
            OLD.enrollment_id,
            OLD.class_id,
            OLD.matched_at,
            OLD.matched_by_user_id,
            OLD.match_reason
          ) IS DISTINCT FROM ROW(
            NEW.student_person_id,
            NEW.enrollment_id,
            NEW.class_id,
            NEW.matched_at,
            NEW.matched_by_user_id,
            NEW.match_reason
          ) THEN
            IF OLD.student_person_id IS NULL
               AND OLD.enrollment_id IS NULL
               AND OLD.class_id IS NULL
               AND OLD.matched_at IS NULL
               AND OLD.matched_by_user_id IS NULL
               AND OLD.match_reason IS NULL
               AND NEW.student_person_id IS NOT NULL
               AND NEW.enrollment_id IS NOT NULL
               AND NEW.class_id IS NOT NULL
               AND NEW.matched_at IS NOT NULL
               AND NEW.matched_by_user_id IS NOT NULL
               AND NEW.match_reason IS NOT NULL THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'evaluation media target is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evaluation_media_assets_target_immutable
        BEFORE UPDATE ON evaluation_media_assets
        FOR EACH ROW EXECUTE FUNCTION enforce_evaluation_media_asset_target_immutable()
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    _seed_permissions(bind)
    _extend_existing_tables()
    _create_staff_tables()
    _create_game_tables()
    _create_analysis_table()
    _create_media_tables()

    roles = sa.table(
        "evaluation_staff_roles",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("category", sa.String()),
        sa.column("is_builtin", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_by_user_id", sa.Uuid()),
    )
    bind.execute(
        roles.insert().values(
            id=EVALUATION_LIFE_TEACHER_ROLE_ID,
            key="life_teacher",
            name="生活老师",
            category="life_service",
            is_builtin=True,
            is_active=True,
            created_by_user_id=None,
        )
    )


def _lock_downgrade_tables(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE student_profiles, user_permission_overrides, enrollments, "
            "permissions, role_permissions, roles, game_projects, "
            "evaluation_staff_roles, cohort_staff_assignments, "
            "cohort_staff_assignment_permissions, student_game_accounts, "
            "student_auxiliary_phones, project_analysis_profiles, "
            "evaluation_media_blobs, evaluation_media_assets "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )


def _preflight_downgrade(bind: sa.Connection) -> None:
    _lock_downgrade_tables(bind)
    has_business_data = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM cohort_staff_assignments) "
            "OR EXISTS (SELECT 1 FROM cohort_staff_assignment_permissions) "
            "OR EXISTS (SELECT 1 FROM student_game_accounts) "
            "OR EXISTS (SELECT 1 FROM student_auxiliary_phones) "
            "OR EXISTS (SELECT 1 FROM evaluation_media_blobs) "
            "OR EXISTS (SELECT 1 FROM evaluation_media_assets) "
            "OR EXISTS (SELECT 1 FROM student_profiles WHERE game_account_limit <> 3)"
        )
    )
    if has_business_data:
        raise RuntimeError("cannot downgrade evaluation foundation with business data")

    invalid_roles = bind.scalar(
        sa.text(
            "SELECT count(*) <> 1 OR NOT EXISTS ("
            "SELECT 1 FROM evaluation_staff_roles "
            "WHERE id = :role_id AND key = 'life_teacher' AND name = '生活老师' "
            "AND category = 'life_service' AND is_builtin IS TRUE "
            "AND is_active IS TRUE AND created_by_user_id IS NULL) "
            "FROM evaluation_staff_roles"
        ),
        {"role_id": EVALUATION_LIFE_TEACHER_ROLE_ID},
    )
    if invalid_roles:
        raise RuntimeError("cannot downgrade evaluation foundation with modified staff role")

    invalid_profiles = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM game_projects AS project "
            "LEFT JOIN project_analysis_profiles AS profile "
            "ON profile.project_id = project.id "
            "WHERE profile.id IS NULL OR profile.version <> 1 "
            "OR profile.mode <> 'manual_only' OR profile.adapter_id IS NOT NULL "
            "OR profile.adapter_version IS NOT NULL "
            "OR profile.scoring_rule_version IS NOT NULL "
            "OR profile.required_fields <> '[]'::jsonb "
            "OR profile.confidence_thresholds <> '{}'::jsonb "
            "OR profile.auto_publish_warning_levels <> '[]'::jsonb "
            "OR profile.publish_mode <> 'review_required' "
            "OR profile.configured_by_user_id IS NOT NULL "
            "OR profile.change_reason <> 'migration backfill') "
            "OR EXISTS ("
            "SELECT 1 FROM project_analysis_profiles AS profile "
            "LEFT JOIN game_projects AS project ON project.id = profile.project_id "
            "WHERE project.id IS NULL) "
            "OR (SELECT count(*) FROM project_analysis_profiles) "
            "<> (SELECT count(*) FROM game_projects)"
        )
    )
    if invalid_profiles:
        raise RuntimeError(
            "cannot downgrade evaluation foundation with modified analysis profiles"
        )

    permission_ids = [values[0] for values in EVALUATION_FOUNDATION_PERMISSIONS.values()]
    permission_keys = list(EVALUATION_FOUNDATION_PERMISSIONS)
    permission_rows = set(
        bind.execute(
            sa.text(
                "SELECT id, key, name, sensitive FROM permissions "
                "WHERE id = ANY(CAST(:ids AS uuid[])) "
                "OR key = ANY(CAST(:keys AS text[]))"
            ),
            {"ids": permission_ids, "keys": permission_keys},
        ).tuples()
    )
    expected_permission_rows = {
        (permission_id, key, name, sensitive)
        for key, (permission_id, name, sensitive) in EVALUATION_FOUNDATION_PERMISSIONS.items()
    }
    if permission_rows != expected_permission_rows:
        raise RuntimeError(
            "cannot downgrade evaluation foundation with modified permission seeds"
        )

    grant_rows = set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id "
                "WHERE permissions.id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": permission_ids},
        ).tuples()
    )
    if grant_rows != set(EVALUATION_FOUNDATION_GRANTS):
        raise RuntimeError(
            "cannot downgrade evaluation foundation with customized role grants"
        )

    invalid_override = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM user_permission_overrides "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[])) "
            "OR scope_kind IN ('cohort', 'project', 'enrollment') "
            "OR valid_from IS NOT NULL OR valid_until IS NOT NULL "
            "OR created_by_user_id IS NOT NULL OR grant_reason IS NOT NULL)"
        ),
        {"ids": permission_ids},
    )
    if invalid_override:
        raise RuntimeError(
            "cannot downgrade evaluation foundation with extended permission overrides"
        )


def _drop_foundation_tables() -> None:
    op.execute(
        "DROP TRIGGER trg_evaluation_media_assets_target_immutable "
        "ON evaluation_media_assets"
    )
    op.execute("DROP FUNCTION enforce_evaluation_media_asset_target_immutable()")
    op.drop_table("evaluation_media_assets")
    op.drop_table("evaluation_media_blobs")
    op.execute(
        "DROP TRIGGER trg_project_analysis_profiles_identity_immutable "
        "ON project_analysis_profiles"
    )
    op.execute("DROP FUNCTION enforce_project_analysis_profile_identity_immutable()")
    op.drop_table("project_analysis_profiles")
    op.drop_table("student_auxiliary_phones")
    op.drop_table("student_game_accounts")
    op.execute(
        "DROP TRIGGER trg_used_evaluation_staff_roles_immutable "
        "ON evaluation_staff_roles"
    )
    op.execute("DROP FUNCTION enforce_used_evaluation_staff_role_immutable()")
    op.drop_table("cohort_staff_assignment_permissions")
    op.drop_table("cohort_staff_assignments")
    op.drop_table("evaluation_staff_roles")


def _restore_existing_tables() -> None:
    op.drop_constraint(
        "uq_enrollments_id_student_person",
        "enrollments",
        type_="unique",
    )

    op.drop_constraint(
        "fk_permission_overrides_created_by_user",
        "user_permission_overrides",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_permission_overrides_validity",
        "user_permission_overrides",
        type_="check",
    )
    op.drop_constraint(
        "ck_permission_overrides_scope_id",
        "user_permission_overrides",
        type_="check",
    )
    op.drop_constraint(
        "ck_permission_overrides_scope_kind",
        "user_permission_overrides",
        type_="check",
    )
    op.drop_column("user_permission_overrides", "created_at")
    op.drop_column("user_permission_overrides", "grant_reason")
    op.drop_column("user_permission_overrides", "created_by_user_id")
    op.drop_column("user_permission_overrides", "valid_until")
    op.drop_column("user_permission_overrides", "valid_from")
    op.create_check_constraint(
        "ck_permission_overrides_scope_kind",
        "user_permission_overrides",
        "scope_kind IN ('global', 'self', 'family', 'person', 'assigned_class')",
    )
    op.create_check_constraint(
        "ck_permission_overrides_scope_id",
        "user_permission_overrides",
        "(scope_kind IN ('global', 'self', 'family') AND scope_id IS NULL) OR "
        "(scope_kind IN ('person', 'assigned_class') AND scope_id IS NOT NULL)",
    )

    op.drop_constraint(
        "ck_student_profiles_game_account_limit",
        "student_profiles",
        type_="check",
    )
    op.drop_column("student_profiles", "game_account_limit")


def downgrade() -> None:
    bind = op.get_bind()
    _preflight_downgrade(bind)
    _drop_foundation_tables()

    permission_ids = [values[0] for values in EVALUATION_FOUNDATION_PERMISSIONS.values()]
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": permission_ids},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": permission_ids},
    )
    _restore_existing_tables()
