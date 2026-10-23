"""Add personal food preferences, history and export permissions.

Revision ID: 0015_food_preferences
Revises: 0014_teaching_reports
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_food_preferences"
down_revision: str | Sequence[str] | None = "0014_teaching_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FOOD_OTHER_CUISINE_ID = UUID("60000000-0000-4000-8000-000000000001")

FOOD_PREFERENCE_PERMISSIONS = {
    "food_preferences.read": (
        UUID("20000000-0000-4000-8000-000000000047"),
        "查看个人饮食偏好",
        False,
    ),
    "food_preferences.update": (
        UUID("20000000-0000-4000-8000-000000000048"),
        "修改个人饮食偏好",
        True,
    ),
    "food_preferences.catalog.manage": (
        UUID("20000000-0000-4000-8000-000000000049"),
        "管理菜系字典",
        True,
    ),
    "food_preferences.export": (
        UUID("20000000-0000-4000-8000-000000000050"),
        "导出饮食偏好统计",
        True,
    ),
}

FOOD_PREFERENCE_GRANTS = {
    *(('admin', key, 'global') for key in FOOD_PREFERENCE_PERMISSIONS),
    ("student", "food_preferences.read", "self"),
    ("student", "food_preferences.update", "self"),
    ("guardian", "food_preferences.read", "family"),
    ("guardian", "food_preferences.update", "family"),
}

FOOD_PREFERENCE_TABLES = (
    "food_cuisine_options",
    "person_food_preferences",
    "person_food_preference_cuisines",
    "person_food_preference_dishes",
    "person_food_preference_versions",
)


def _preflight_upgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE permissions, role_permissions, roles "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    permission_ids = [value[0] for value in FOOD_PREFERENCE_PERMISSIONS.values()]
    permission_keys = list(FOOD_PREFERENCE_PERMISSIONS)
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM permissions "
            "WHERE id = ANY(CAST(:permission_ids AS uuid[])) "
            "OR key = ANY(CAST(:permission_keys AS text[])))"
        ),
        {"permission_ids": permission_ids, "permission_keys": permission_keys},
    ):
        raise RuntimeError(
            "cannot upgrade food preferences with preexisting permission"
        )
    existing_roles = set(
        bind.scalars(
            sa.text(
                "SELECT key FROM roles "
                "WHERE key = ANY(CAST(:role_keys AS text[]))"
            ),
            {"role_keys": ["admin", "student", "guardian"]},
        )
    )
    if existing_roles != {"admin", "student", "guardian"}:
        raise RuntimeError("cannot upgrade food preferences without required roles")


def _create_tables() -> None:
    op.add_column(
        "user_accounts",
        sa.Column(
            "food_preferences_onboarding_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_table(
        "food_cuisine_options",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("normalized_name", sa.String(length=160), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_other", sa.Boolean(), server_default="false", nullable=False),
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
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 40 AND name = btrim(name)",
            name="ck_food_cuisine_options_name",
        ),
        sa.CheckConstraint(
            "char_length(normalized_name) BETWEEN 1 AND 160 "
            "AND normalized_name = btrim(normalized_name)",
            name="ck_food_cuisine_options_normalized_name",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_food_cuisine_options_sort_order",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            name="fk_food_cuisine_options_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            name="fk_food_cuisine_options_updated_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "normalized_name",
            name="uq_food_cuisine_options_normalized_name",
        ),
    )
    op.create_index(
        "uq_food_cuisine_options_single_other",
        "food_cuisine_options",
        ["is_other"],
        unique=True,
        postgresql_where=sa.text("is_other"),
    )
    op.create_index(
        "ix_food_cuisine_options_catalog",
        "food_cuisine_options",
        ["is_active", "sort_order", "normalized_name", "id"],
        unique=False,
    )

    op.create_table(
        "person_food_preferences",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_person_food_preferences_version",
        ),
        sa.CheckConstraint(
            "source IN ('profile_self', 'profile_guardian', "
            "'profile_admin', 'camp_survey')",
            name="ck_person_food_preferences_source",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name="fk_person_food_preferences_person",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            name="fk_person_food_preferences_updated_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("person_id"),
    )
    op.create_index(
        "ix_person_food_preferences_updated_at",
        "person_food_preferences",
        ["updated_at", "person_id"],
        unique=False,
    )

    op.create_table(
        "person_food_preference_cuisines",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("cuisine_option_id", sa.UUID(), nullable=False),
        sa.Column("cuisine_name_snapshot", sa.String(length=40), nullable=False),
        sa.Column("custom_name", sa.String(length=40), nullable=True),
        sa.Column("custom_normalized_name", sa.String(length=160), nullable=True),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 3",
            name="ck_person_food_preference_cuisines_position",
        ),
        sa.CheckConstraint(
            "char_length(cuisine_name_snapshot) BETWEEN 1 AND 40 "
            "AND cuisine_name_snapshot = btrim(cuisine_name_snapshot)",
            name="ck_person_food_preference_cuisines_snapshot",
        ),
        sa.CheckConstraint(
            "(custom_name IS NULL) = (custom_normalized_name IS NULL)",
            name="ck_person_food_preference_cuisines_custom_pair",
        ),
        sa.CheckConstraint(
            "custom_name IS NULL OR "
            "(char_length(custom_name) BETWEEN 1 AND 40 "
            "AND custom_name = btrim(custom_name))",
            name="ck_person_food_preference_cuisines_custom_name",
        ),
        sa.CheckConstraint(
            "custom_normalized_name IS NULL OR "
            "(char_length(custom_normalized_name) BETWEEN 1 AND 160 "
            "AND custom_normalized_name = btrim(custom_normalized_name))",
            name="ck_person_food_preference_cuisines_custom_normalized_name",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person_food_preferences.person_id"],
            name="fk_person_food_preference_cuisines_preference",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cuisine_option_id"],
            ["food_cuisine_options.id"],
            name="fk_person_food_preference_cuisines_option",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("person_id", "position"),
        sa.UniqueConstraint(
            "person_id",
            "cuisine_option_id",
            name="uq_person_food_preference_cuisines_option",
        ),
        sa.UniqueConstraint(
            "person_id",
            "custom_normalized_name",
            name="uq_person_food_preference_cuisines_custom_name",
        ),
    )
    op.create_index(
        "ix_person_food_preference_cuisines_option",
        "person_food_preference_cuisines",
        ["cuisine_option_id", "person_id"],
        unique=False,
    )

    op.create_table(
        "person_food_preference_dishes",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=40), nullable=False),
        sa.Column("normalized_name", sa.String(length=160), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 3",
            name="ck_person_food_preference_dishes_position",
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 40 "
            "AND display_name = btrim(display_name)",
            name="ck_person_food_preference_dishes_display_name",
        ),
        sa.CheckConstraint(
            "char_length(normalized_name) BETWEEN 1 AND 160 "
            "AND normalized_name = btrim(normalized_name)",
            name="ck_person_food_preference_dishes_normalized_name",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person_food_preferences.person_id"],
            name="fk_person_food_preference_dishes_preference",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("person_id", "position"),
        sa.UniqueConstraint(
            "person_id",
            "normalized_name",
            name="uq_person_food_preference_dishes_normalized_name",
        ),
    )
    op.create_index(
        "ix_person_food_preference_dishes_suggestions",
        "person_food_preference_dishes",
        ["normalized_name", "person_id"],
        unique=False,
    )

    op.create_table(
        "person_food_preference_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("cuisines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dishes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_person_food_preference_versions_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(cuisines) = 'array' "
            "AND jsonb_array_length(cuisines) <= 3",
            name="ck_person_food_preference_versions_cuisines",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(dishes) = 'array' AND jsonb_array_length(dishes) <= 3",
            name="ck_person_food_preference_versions_dishes",
        ),
        sa.CheckConstraint(
            "source IN ('profile_self', 'profile_guardian', "
            "'profile_admin', 'camp_survey')",
            name="ck_person_food_preference_versions_source",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name="fk_person_food_preference_versions_person",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            name="fk_person_food_preference_versions_updated_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "person_id",
            "version",
            name="uq_person_food_preference_versions_person_version",
        ),
    )
    op.create_index(
        "ix_person_food_preference_versions_history",
        "person_food_preference_versions",
        ["person_id", "version", "id"],
        unique=False,
    )


def _create_protection_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION protect_system_other_cuisine()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND OLD.is_other THEN
                RAISE EXCEPTION 'system other cuisine is protected';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.is_other
               AND (NOT NEW.is_other OR NOT NEW.is_active) THEN
                RAISE EXCEPTION 'system other cuisine is protected';
            END IF;
            IF TG_OP <> 'DELETE' AND NEW.is_other AND NOT NEW.is_active THEN
                RAISE EXCEPTION 'system other cuisine is protected';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_system_other_cuisine
        BEFORE UPDATE OR DELETE ON food_cuisine_options
        FOR EACH ROW EXECUTE FUNCTION protect_system_other_cuisine()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_food_custom_cuisine_pairing()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            option_is_other boolean;
        BEGIN
            SELECT is_other INTO option_is_other
            FROM food_cuisine_options
            WHERE id = NEW.cuisine_option_id;
            IF option_is_other IS NULL
               OR (option_is_other AND
                   (NEW.custom_name IS NULL OR NEW.custom_normalized_name IS NULL))
               OR (NOT option_is_other AND
                   (NEW.custom_name IS NOT NULL OR NEW.custom_normalized_name IS NOT NULL)) THEN
                RAISE EXCEPTION 'custom cuisine pairing is invalid';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_validate_food_custom_cuisine_pairing
        BEFORE INSERT OR UPDATE ON person_food_preference_cuisines
        FOR EACH ROW EXECUTE FUNCTION validate_food_custom_cuisine_pairing()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_food_preference_version_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'food preference versions are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reject_food_preference_version_mutation
        BEFORE UPDATE OR DELETE ON person_food_preference_versions
        FOR EACH ROW EXECUTE FUNCTION reject_food_preference_version_mutation()
        """
    )


def _seed_permissions_and_other(bind: sa.Connection) -> None:
    for key, (permission_id, name, sensitive) in FOOD_PREFERENCE_PERMISSIONS.items():
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, key, name, sensitive) "
                "VALUES (:id, :key, :name, :sensitive)"
            ),
            {
                "id": permission_id,
                "key": key,
                "name": name,
                "sensitive": sensitive,
            },
        )
    for role_key, permission_key, scope_kind in sorted(FOOD_PREFERENCE_GRANTS):
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT roles.id, permissions.id, :scope_kind "
                "FROM roles CROSS JOIN permissions "
                "WHERE roles.key = :role_key AND permissions.key = :permission_key"
            ),
            {
                "role_key": role_key,
                "permission_key": permission_key,
                "scope_kind": scope_kind,
            },
        )
    bind.execute(
        sa.text(
            "INSERT INTO food_cuisine_options ("
            "id, name, normalized_name, sort_order, is_active, is_other"
            ") VALUES ("
            ":id, '其他', '其他', 100000, true, true)"
        ),
        {"id": FOOD_OTHER_CUISINE_ID},
    )


def _preflight_downgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE permissions, role_permissions, user_permission_overrides, "
            "user_accounts, "
            "cohort_staff_assignment_permissions, food_cuisine_options, "
            "person_food_preferences, person_food_preference_cuisines, "
            "person_food_preference_dishes, person_food_preference_versions "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    permission_ids = [value[0] for value in FOOD_PREFERENCE_PERMISSIONS.values()]
    rows = {
        (row.id, row.key, row.name, row.sensitive)
        for row in bind.execute(
            sa.text(
                "SELECT id, key, name, sensitive FROM permissions "
                "WHERE id = ANY(CAST(:permission_ids AS uuid[]))"
            ),
            {"permission_ids": permission_ids},
        )
    }
    expected_rows = {
        (permission_id, key, name, sensitive)
        for key, (permission_id, name, sensitive) in FOOD_PREFERENCE_PERMISSIONS.items()
    }
    if rows != expected_rows:
        raise RuntimeError(
            "cannot downgrade food preferences with modified permissions or grants"
        )
    grants = set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id "
                "WHERE permissions.id = ANY(CAST(:permission_ids AS uuid[]))"
            ),
            {"permission_ids": permission_ids},
        ).tuples()
    )
    if grants != FOOD_PREFERENCE_GRANTS:
        raise RuntimeError(
            "cannot downgrade food preferences with modified permissions or grants"
        )
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_permission_overrides "
            "WHERE permission_id = ANY(CAST(:permission_ids AS uuid[]))) "
            "OR EXISTS (SELECT 1 FROM cohort_staff_assignment_permissions "
            "WHERE permission_id = ANY(CAST(:permission_ids AS uuid[])))"
        ),
        {"permission_ids": permission_ids},
    ):
        raise RuntimeError(
            "cannot downgrade food preferences with non-seed permission references"
        )
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_accounts "
            "WHERE food_preferences_onboarding_completed_at IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "cannot downgrade food preferences with completed onboarding data"
        )
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM person_food_preferences) "
            "OR EXISTS (SELECT 1 FROM person_food_preference_versions) "
            "OR EXISTS (SELECT 1 FROM person_food_preference_cuisines) "
            "OR EXISTS (SELECT 1 FROM person_food_preference_dishes)"
        )
    ):
        raise RuntimeError("cannot downgrade food preferences with business data")
    other_rows = list(
        bind.execute(
            sa.text(
                "SELECT id, name, normalized_name, sort_order, is_active, is_other, "
                "created_by_user_id, updated_by_user_id "
                "FROM food_cuisine_options ORDER BY id"
            )
        ).tuples()
    )
    if other_rows != [
        (FOOD_OTHER_CUISINE_ID, "其他", "其他", 100000, True, True, None, None)
    ]:
        raise RuntimeError("cannot downgrade food preferences with customized cuisine data")


def upgrade() -> None:
    bind = op.get_bind()
    _preflight_upgrade(bind)
    _create_tables()
    _create_protection_triggers()
    _seed_permissions_and_other(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _preflight_downgrade(bind)
    permission_ids = [value[0] for value in FOOD_PREFERENCE_PERMISSIONS.values()]
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE permission_id = ANY(CAST(:permission_ids AS uuid[]))"
        ),
        {"permission_ids": permission_ids},
    )
    bind.execute(
        sa.text(
            "DELETE FROM permissions "
            "WHERE id = ANY(CAST(:permission_ids AS uuid[]))"
        ),
        {"permission_ids": permission_ids},
    )
    op.drop_table("person_food_preference_versions")
    op.drop_table("person_food_preference_dishes")
    op.drop_table("person_food_preference_cuisines")
    op.drop_table("person_food_preferences")
    op.drop_table("food_cuisine_options")
    op.drop_column("user_accounts", "food_preferences_onboarding_completed_at")
    op.execute("DROP FUNCTION reject_food_preference_version_mutation()")
    op.execute("DROP FUNCTION validate_food_custom_cuisine_pairing()")
    op.execute("DROP FUNCTION protect_system_other_cuisine()")
