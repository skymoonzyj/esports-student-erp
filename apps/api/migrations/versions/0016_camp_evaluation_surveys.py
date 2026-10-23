"""Add immutable camp completion survey cycles, snapshots and submissions.

Revision ID: 0016_camp_evaluation_surveys
Revises: 0015_food_preferences
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_camp_evaluation_surveys"
down_revision: str | Sequence[str] | None = "0015_food_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SURVEY_PERMISSIONS = {
    "camp_surveys.identity.read": (
        UUID("20000000-0000-4000-8000-000000000034"),
        "查看营期问卷实名内容",
        True,
    ),
    "camp_surveys.print": (
        UUID("20000000-0000-4000-8000-000000000035"),
        "打印营期工作人员评价",
        True,
    ),
}
SURVEY_GRANTS = {
    ("admin", permission_key, "global")
    for permission_key in SURVEY_PERMISSIONS
}
SURVEY_TABLES = (
    "camp_survey_cycles",
    "camp_survey_snapshot_versions",
    "camp_survey_staff_snapshots",
    "camp_survey_submissions",
    "camp_survey_versions",
    "camp_survey_staff_ratings",
    "camp_survey_supplements",
    "camp_survey_supplement_ratings",
)


def _preflight_upgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE permissions, role_permissions, roles "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    ids = [item[0] for item in SURVEY_PERMISSIONS.values()]
    keys = list(SURVEY_PERMISSIONS)
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM permissions "
            "WHERE id = ANY(CAST(:ids AS uuid[])) "
            "OR key = ANY(CAST(:keys AS text[])))"
        ),
        {"ids": ids, "keys": keys},
    ):
        raise RuntimeError("cannot upgrade camp surveys with preexisting permission")
    roles = set(
        bind.scalars(
            sa.text(
                "SELECT key FROM roles WHERE key = ANY(CAST(:keys AS text[]))"
            ),
            {"keys": ["admin"]},
        )
    )
    if roles != {"admin"}:
        raise RuntimeError("cannot upgrade camp surveys without required roles")


def _create_tables() -> None:
    uuid = sa.UUID()
    tz = sa.DateTime(timezone=True)
    jsonb = postgresql.JSONB

    op.create_table(
        "camp_survey_cycles",
        sa.Column("id", uuid, nullable=False),
        sa.Column("enrollment_id", uuid, nullable=False),
        sa.Column("student_person_id", uuid, nullable=False),
        sa.Column("cohort_id", uuid, nullable=False),
        sa.Column("class_id", uuid, nullable=False),
        sa.Column("opens_at", tz, nullable=False),
        sa.Column("due_at", tz, nullable=False),
        sa.Column("current_snapshot_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("due_at > opens_at", name="ck_camp_survey_cycles_window"),
        sa.CheckConstraint(
            "current_snapshot_version >= 1",
            name="ck_camp_survey_cycles_snapshot_version",
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id", "student_person_id", "cohort_id", "class_id"],
            [
                "enrollments.id",
                "enrollments.student_person_id",
                "enrollments.cohort_id",
                "enrollments.class_id",
            ],
            name="fk_camp_survey_cycles_enrollment_target",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("enrollment_id", name="uq_camp_survey_cycles_enrollment"),
    )
    op.create_index(
        "ix_camp_survey_cycles_window",
        "camp_survey_cycles",
        ["opens_at", "due_at", "id"],
    )

    op.create_table(
        "camp_survey_snapshot_versions",
        sa.Column("id", uuid, nullable=False),
        sa.Column("cycle_id", uuid, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("change_reason", sa.String(length=500), nullable=False),
        sa.Column("created_by_user_id", uuid, nullable=True),
        sa.Column("created_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "version >= 1", name="ck_camp_survey_snapshot_versions_version"
        ),
        sa.CheckConstraint(
            "btrim(change_reason) <> ''",
            name="ck_camp_survey_snapshot_versions_reason",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["camp_survey_cycles.id"],
            name="fk_camp_survey_snapshot_versions_cycle", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["user_accounts.id"],
            name="fk_camp_survey_snapshot_versions_creator", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cycle_id", "version", name="uq_camp_survey_snapshot_versions_cycle_version"
        ),
        sa.UniqueConstraint(
            "id", "cycle_id", name="uq_camp_survey_snapshot_versions_id_cycle"
        ),
    )

    op.create_table(
        "camp_survey_staff_snapshots",
        sa.Column("id", uuid, nullable=False),
        sa.Column("snapshot_version_id", uuid, nullable=False),
        sa.Column("staff_person_id", uuid, nullable=False),
        sa.Column("staff_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("role_key_snapshot", sa.String(length=40), nullable=False),
        sa.Column("role_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("role_category", sa.String(length=24), nullable=False),
        sa.Column("roles_snapshot", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("service_intervals", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("include_in_summary", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "btrim(staff_name_snapshot) <> ''",
            name="ck_camp_survey_staff_snapshots_name",
        ),
        sa.CheckConstraint(
            "role_category IN ('teaching', 'life_service', 'operations')",
            name="ck_camp_survey_staff_snapshots_category",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(service_intervals) = 'array'",
            name="ck_camp_survey_staff_snapshots_service_intervals",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(roles_snapshot) = 'array' AND jsonb_array_length(roles_snapshot) >= 1",
            name="ck_camp_survey_staff_snapshots_roles_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_version_id"], ["camp_survey_snapshot_versions.id"],
            name="fk_camp_survey_staff_snapshots_version", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["staff_person_id"], ["people.id"],
            name="fk_camp_survey_staff_snapshots_person", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_version_id", "staff_person_id",
            name="uq_camp_survey_staff_snapshots_staff",
        ),
        sa.UniqueConstraint(
            "id", "snapshot_version_id",
            name="uq_camp_survey_staff_snapshots_id_version",
        ),
    )
    op.create_index(
        "ix_camp_survey_staff_snapshots_staff_history",
        "camp_survey_staff_snapshots",
        ["staff_person_id", "id"],
    )

    op.create_table(
        "camp_survey_submissions",
        sa.Column("id", uuid, nullable=False),
        sa.Column("cycle_id", uuid, nullable=False),
        sa.Column("respondent_person_id", uuid, nullable=False),
        sa.Column("student_person_id", uuid, nullable=False),
        sa.Column("respondent_kind", sa.String(length=16), nullable=False),
        sa.Column("guardian_relationship_id", uuid, nullable=True),
        sa.Column("guardian_relationship_snapshot", jsonb, nullable=True),
        sa.Column("revision_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_version", sa.Integer(), nullable=True),
        sa.Column("first_submitted_at", tz, nullable=True),
        sa.Column("last_submitted_at", tz, nullable=True),
        sa.Column("created_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "respondent_kind IN ('self', 'guardian')",
            name="ck_camp_survey_submissions_kind",
        ),
        sa.CheckConstraint(
            "revision_count BETWEEN 0 AND 1",
            name="ck_camp_survey_submissions_revision_count",
        ),
        sa.CheckConstraint(
            "(respondent_kind = 'self' AND guardian_relationship_id IS NULL) OR "
            "(respondent_kind = 'guardian' AND guardian_relationship_id IS NOT NULL)",
            name="ck_camp_survey_submissions_relationship",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["camp_survey_cycles.id"],
            name="fk_camp_survey_submissions_cycle", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["respondent_person_id"], ["people.id"],
            name="fk_camp_survey_submissions_respondent", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["student_person_id"], ["people.id"],
            name="fk_camp_survey_submissions_student", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["guardian_relationship_id"], ["guardian_relationships.id"],
            name="fk_camp_survey_submissions_relationship", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cycle_id", "respondent_person_id",
            name="uq_camp_survey_submissions_cycle_respondent",
        ),
        sa.UniqueConstraint(
            "id", "cycle_id", name="uq_camp_survey_submissions_id_cycle"
        ),
    )
    op.create_index(
        "ix_camp_survey_submissions_student",
        "camp_survey_submissions",
        ["student_person_id", "created_at", "id"],
    )

    op.create_table(
        "camp_survey_versions",
        sa.Column("id", uuid, nullable=False),
        sa.Column("submission_id", uuid, nullable=False),
        sa.Column("snapshot_version_id", uuid, nullable=False),
        sa.Column("cycle_id", uuid, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("course_training_score", sa.Integer(), nullable=False),
        sa.Column("course_training_reason", sa.Text(), nullable=True),
        sa.Column("life_service_score", sa.Integer(), nullable=False),
        sa.Column("life_service_reason", sa.Text(), nullable=True),
        sa.Column("service_management_score", sa.Integer(), nullable=False),
        sa.Column("service_management_reason", sa.Text(), nullable=True),
        sa.Column("overall_suggestion", sa.Text(), nullable=True),
        sa.Column("favorite_cuisines", jsonb, nullable=False),
        sa.Column("favorite_dishes", jsonb, nullable=False),
        sa.Column("food_preference_version", sa.Integer(), nullable=True),
        sa.Column("published_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("version IN (1, 2)", name="ck_camp_survey_versions_version"),
        sa.CheckConstraint(
            "course_training_score BETWEEN 1 AND 10 "
            "AND life_service_score BETWEEN 1 AND 10 "
            "AND service_management_score BETWEEN 1 AND 10",
            name="ck_camp_survey_versions_scores",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(favorite_cuisines) = 'array' "
            "AND jsonb_array_length(favorite_cuisines) BETWEEN 1 AND 3 "
            "AND jsonb_typeof(favorite_dishes) = 'array' "
            "AND jsonb_array_length(favorite_dishes) BETWEEN 1 AND 3 "
            "AND NOT jsonb_path_exists(favorite_cuisines, "
            "'$[*]?(@.cuisine_option_id.type() != \"string\" || "
            "@.name.type() != \"string\" || "
            "(@.custom_name != null && @.custom_name.type() != \"string\"))') "
            "AND NOT jsonb_path_exists(favorite_dishes, "
            "'$[*]?(@.type() != \"string\" || @ == \"\")')",
            name="ck_camp_survey_versions_food_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id", "cycle_id"],
            ["camp_survey_submissions.id", "camp_survey_submissions.cycle_id"],
            name="fk_camp_survey_versions_submission", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_version_id", "cycle_id"],
            ["camp_survey_snapshot_versions.id", "camp_survey_snapshot_versions.cycle_id"],
            name="fk_camp_survey_versions_snapshot", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "submission_id", "version", name="uq_camp_survey_versions_submission_version"
        ),
        sa.UniqueConstraint(
            "id", "snapshot_version_id", name="uq_camp_survey_versions_id_snapshot"
        ),
    )
    op.create_index(
        "ix_camp_survey_versions_submission_history",
        "camp_survey_versions",
        ["submission_id", "version", "id"],
    )

    op.create_table(
        "camp_survey_staff_ratings",
        sa.Column("id", uuid, nullable=False),
        sa.Column("version_id", uuid, nullable=False),
        sa.Column("staff_snapshot_id", uuid, nullable=False),
        sa.Column("snapshot_version_id", uuid, nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("created_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("score BETWEEN 1 AND 10", name="ck_camp_survey_staff_ratings_score"),
        sa.CheckConstraint(
            "reason IS NULL OR btrim(reason) <> ''",
            name="ck_camp_survey_staff_ratings_reason",
        ),
        sa.CheckConstraint(
            "suggestion IS NULL OR btrim(suggestion) <> ''",
            name="ck_camp_survey_staff_ratings_suggestion",
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "snapshot_version_id"],
            ["camp_survey_versions.id", "camp_survey_versions.snapshot_version_id"],
            name="fk_camp_survey_staff_ratings_version", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["staff_snapshot_id", "snapshot_version_id"],
            ["camp_survey_staff_snapshots.id", "camp_survey_staff_snapshots.snapshot_version_id"],
            name="fk_camp_survey_staff_ratings_staff", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "version_id", "staff_snapshot_id",
            name="uq_camp_survey_staff_ratings_version_staff",
        ),
    )

    op.create_table(
        "camp_survey_supplements",
        sa.Column("id", uuid, nullable=False),
        sa.Column("submission_id", uuid, nullable=False),
        sa.Column("snapshot_version_id", uuid, nullable=False),
        sa.Column("cycle_id", uuid, nullable=False),
        sa.Column("change_reason", sa.String(length=500), nullable=False),
        sa.Column("created_by_user_id", uuid, nullable=False),
        sa.Column("submitted_at", tz, nullable=True),
        sa.Column("created_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "btrim(change_reason) <> ''", name="ck_camp_survey_supplements_reason"
        ),
        sa.ForeignKeyConstraint(
            ["submission_id", "cycle_id"],
            ["camp_survey_submissions.id", "camp_survey_submissions.cycle_id"],
            name="fk_camp_survey_supplements_submission", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_version_id", "cycle_id"],
            ["camp_survey_snapshot_versions.id", "camp_survey_snapshot_versions.cycle_id"],
            name="fk_camp_survey_supplements_snapshot", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["user_accounts.id"],
            name="fk_camp_survey_supplements_creator", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "submission_id", "snapshot_version_id",
            name="uq_camp_survey_supplements_submission_snapshot",
        ),
        sa.UniqueConstraint(
            "id", "snapshot_version_id",
            name="uq_camp_survey_supplements_id_snapshot",
        ),
    )

    op.create_table(
        "camp_survey_supplement_ratings",
        sa.Column("id", uuid, nullable=False),
        sa.Column("supplement_id", uuid, nullable=False),
        sa.Column("staff_snapshot_id", uuid, nullable=False),
        sa.Column("snapshot_version_id", uuid, nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("created_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", tz, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "score BETWEEN 1 AND 10",
            name="ck_camp_survey_supplement_ratings_score",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR btrim(reason) <> ''",
            name="ck_camp_survey_supplement_ratings_reason",
        ),
        sa.CheckConstraint(
            "suggestion IS NULL OR btrim(suggestion) <> ''",
            name="ck_camp_survey_supplement_ratings_suggestion",
        ),
        sa.ForeignKeyConstraint(
            ["supplement_id", "snapshot_version_id"],
            ["camp_survey_supplements.id", "camp_survey_supplements.snapshot_version_id"],
            name="fk_camp_survey_supplement_ratings_supplement", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["staff_snapshot_id", "snapshot_version_id"],
            ["camp_survey_staff_snapshots.id", "camp_survey_staff_snapshots.snapshot_version_id"],
            name="fk_camp_survey_supplement_ratings_staff", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "supplement_id", "staff_snapshot_id",
            name="uq_camp_survey_supplement_ratings_supplement_staff",
        ),
    )


def _create_immutability_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_camp_survey_history_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'camp survey history is immutable'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name, trigger_name in (
        ("camp_survey_snapshot_versions", "camp_survey_snapshot_versions_immutable"),
        ("camp_survey_staff_snapshots", "camp_survey_staff_snapshots_immutable"),
        ("camp_survey_versions", "camp_survey_versions_immutable"),
        ("camp_survey_staff_ratings", "camp_survey_staff_ratings_immutable"),
        ("camp_survey_supplements", "camp_survey_supplements_immutable"),
        ("camp_survey_supplement_ratings", "camp_survey_supplement_ratings_immutable"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_camp_survey_history_mutation()"
        )


def _seed_permissions(bind: sa.Connection) -> None:
    for key, (permission_id, name, sensitive) in SURVEY_PERMISSIONS.items():
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, key, name, sensitive) "
                "VALUES (:id, :key, :name, :sensitive)"
            ),
            {"id": permission_id, "key": key, "name": name, "sensitive": sensitive},
        )
    for role_key, permission_key, scope_kind in sorted(SURVEY_GRANTS):
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT roles.id, permissions.id, :scope_kind "
                "FROM roles CROSS JOIN permissions "
                "WHERE roles.key = :role_key AND permissions.key = :permission_key"
            ),
            {"role_key": role_key, "permission_key": permission_key, "scope_kind": scope_kind},
        )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight_upgrade(bind)
    _create_tables()
    _create_immutability_triggers()
    _seed_permissions(bind)


def _preflight_downgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE camp_survey_supplement_ratings, camp_survey_supplements, "
            "camp_survey_staff_ratings, camp_survey_versions, camp_survey_submissions, "
            "camp_survey_staff_snapshots, camp_survey_snapshot_versions, "
            "camp_survey_cycles, permissions, role_permissions, roles, "
            "user_permission_overrides, cohort_staff_assignment_permissions "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    if any(
        bind.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table_name})"))
        for table_name in SURVEY_TABLES
    ):
        raise RuntimeError("cannot downgrade camp surveys with business data")
    ids = [item[0] for item in SURVEY_PERMISSIONS.values()]
    rows = set(
        bind.execute(
            sa.text(
                "SELECT id, key, name, sensitive FROM permissions "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": ids},
        ).tuples()
    )
    expected = {
        (permission_id, key, name, sensitive)
        for key, (permission_id, name, sensitive) in SURVEY_PERMISSIONS.items()
    }
    if rows != expected:
        raise RuntimeError("cannot downgrade camp surveys with modified permissions")
    grants = set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id "
                "WHERE permissions.id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": ids},
        ).tuples()
    )
    if grants != SURVEY_GRANTS:
        raise RuntimeError("cannot downgrade camp surveys with modified grants")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_permission_overrides "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[]))) "
            "OR EXISTS (SELECT 1 FROM cohort_staff_assignment_permissions "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[])))"
        ),
        {"ids": ids},
    ):
        raise RuntimeError("cannot downgrade camp surveys with custom permission references")


def downgrade() -> None:
    bind = op.get_bind()
    _preflight_downgrade(bind)
    for table_name, trigger_name in (
        ("camp_survey_snapshot_versions", "camp_survey_snapshot_versions_immutable"),
        ("camp_survey_staff_snapshots", "camp_survey_staff_snapshots_immutable"),
        ("camp_survey_versions", "camp_survey_versions_immutable"),
        ("camp_survey_staff_ratings", "camp_survey_staff_ratings_immutable"),
        ("camp_survey_supplements", "camp_survey_supplements_immutable"),
        ("camp_survey_supplement_ratings", "camp_survey_supplement_ratings_immutable"),
    ):
        op.execute(f"DROP TRIGGER {trigger_name} ON {table_name}")
    op.execute("DROP FUNCTION prevent_camp_survey_history_mutation()")
    ids = [item[0] for item in SURVEY_PERMISSIONS.values()]
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": ids},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": ids},
    )
    for table_name in reversed(SURVEY_TABLES):
        op.drop_table(table_name)
