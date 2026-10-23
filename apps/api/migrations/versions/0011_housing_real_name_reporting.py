"""Add housing real-name reporting batches.

Revision ID: 0011_housing_real_name_reporting
Revises: 0010_housing_occupancy_lifecycle
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_housing_real_name_reporting"
down_revision: str | Sequence[str] | None = "0010_housing_occupancy_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPORT_PERMISSION = (
    "housing.reports.export_sensitive",
    UUID("20000000-0000-4000-8000-000000000043"),
    "导出房态实名报送",
    True,
)
DEFAULT_GRANTS = (("admin", REPORT_PERMISSION[0], "global"),)
REPORT_BATCH_NUMBER_SEQUENCE = "housing_report_batch_number_seq"


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


def _preflight_upgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text("LOCK TABLE permissions, role_permissions, roles IN SHARE ROW EXCLUSIVE MODE")
    )
    permission_key, permission_id, _, _ = REPORT_PERMISSION
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM permissions "
            "WHERE id = :permission_id OR key = :permission_key)"
        ),
        {"permission_id": permission_id, "permission_key": permission_key},
    ):
        raise RuntimeError("cannot upgrade housing reporting with preexisting permission")
    if _role_permission_grants(bind).intersection(DEFAULT_GRANTS):
        raise RuntimeError("cannot upgrade housing reporting with preexisting grant")
    if bind.scalar(sa.text("SELECT id FROM roles WHERE key = 'admin'")) is None:
        raise RuntimeError("cannot upgrade housing reporting without admin role")


def _create_failure_summary_validator() -> None:
    op.execute(
        """
        CREATE FUNCTION housing_report_failure_summary_is_safe(
            candidate_code text,
            candidate_summary jsonb
        ) RETURNS boolean AS $$
        DECLARE
            issue_count_text text;
            summary_key_count integer;
            field_key_count integer;
            field_name text;
            field_count jsonb;
            field_count_text text;
        BEGIN
            IF candidate_code NOT IN (
                'housing_report_empty',
                'housing_export_incomplete'
            ) THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(candidate_summary) <> 'object' THEN
                RETURN false;
            END IF;
            SELECT count(*)
              INTO summary_key_count
              FROM jsonb_object_keys(candidate_summary);
            IF NOT candidate_summary ?& ARRAY['issue_count', 'field_counts']
               OR summary_key_count <> 2 THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(candidate_summary->'issue_count') <> 'number'
               OR jsonb_typeof(candidate_summary->'field_counts') <> 'object' THEN
                RETURN false;
            END IF;

            issue_count_text := candidate_summary->>'issue_count';
            SELECT count(*)
              INTO field_key_count
              FROM jsonb_object_keys(candidate_summary->'field_counts');
            IF issue_count_text !~ '^(0|[1-9][0-9]*)$' THEN
                RETURN false;
            END IF;
            IF candidate_code = 'housing_report_empty' AND (
                issue_count_text <> '0'
                OR field_key_count <> 0
            ) THEN
                RETURN false;
            END IF;
            IF candidate_code = 'housing_export_incomplete' AND (
                issue_count_text = '0'
                OR field_key_count = 0
            ) THEN
                RETURN false;
            END IF;

            FOR field_name, field_count IN
                SELECT key, value
                  FROM jsonb_each(candidate_summary->'field_counts')
            LOOP
                IF field_name NOT IN (
                    'phone',
                    'sex',
                    'identity_number',
                    'person_type',
                    'full_name',
                    'building',
                    'floor',
                    'room',
                    'stay_purpose',
                    'cohort',
                    'visit_reason',
                    'actual_check_out_at',
                    'stay_status',
                    'guardian_relationship',
                    'guardian.phone',
                    'guardian.sex',
                    'guardian.identity_number',
                    'guardian.full_name',
                    'guardian.age'
                ) THEN
                    RETURN false;
                END IF;
                IF jsonb_typeof(field_count) <> 'number' THEN
                    RETURN false;
                END IF;
                field_count_text := field_count #>> '{}';
                IF field_count_text !~ '^[1-9][0-9]*$' THEN
                    RETURN false;
                END IF;
                IF field_count_text::numeric > issue_count_text::numeric THEN
                    RETURN false;
                END IF;
            END LOOP;
            RETURN true;
        END;
        $$ LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path = pg_catalog
        """
    )


def _create_report_batch_tables() -> None:
    op.execute(
        f"CREATE SEQUENCE {REPORT_BATCH_NUMBER_SEQUENCE} "
        "AS bigint START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
    )
    op.create_table(
        "housing_report_batches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "batch_number",
            sa.String(length=48),
            server_default=sa.text(
                "'HR-' || to_char(CURRENT_TIMESTAMP AT TIME ZONE "
                "'Asia/Shanghai', 'YYYYMMDD') || '-' || "
                "lpad(nextval('housing_report_batch_number_seq')::text, 6, '0')"
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="generated",
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cohort_filter",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "person_type_filter",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=200), nullable=False),
        sa.Column(
            "projection_schema_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("snapshot_ciphertext", sa.Text(), nullable=True),
        sa.Column("snapshot_key_version", sa.Integer(), nullable=True),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "resident_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "stay_row_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "guardian_row_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("submission_note", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("corrects_batch_id", sa.UUID(), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column(
            "failure_summary",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('generated', 'marked_submitted', 'voided', 'failed')",
            name="ck_housing_report_batches_status",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_housing_report_batches_version",
        ),
        sa.CheckConstraint(
            "range_start < range_end",
            name="ck_housing_report_batches_range",
        ),
        sa.CheckConstraint(
            "btrim(purpose) <> ''",
            name="ck_housing_report_batches_purpose",
        ),
        sa.CheckConstraint(
            "projection_schema_version >= 1",
            name="ck_housing_report_batches_projection_schema_version",
        ),
        sa.CheckConstraint(
            "resident_count >= 0 AND stay_row_count >= 0 AND guardian_row_count >= 0",
            name="ck_housing_report_batches_counts",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(cohort_filter) = 'array'",
            name="ck_housing_report_batches_cohort_filter",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(person_type_filter) = 'array'",
            name="ck_housing_report_batches_person_type_filter",
        ),
        sa.CheckConstraint(
            "snapshot_digest IS NULL OR snapshot_digest ~ '^[0-9a-f]{64}$'",
            name="ck_housing_report_batches_snapshot_digest",
        ),
        sa.CheckConstraint(
            "snapshot_key_version IS NULL OR snapshot_key_version >= 1",
            name="ck_housing_report_batches_snapshot_key_version",
        ),
        sa.CheckConstraint(
            "((status IN ('generated', 'marked_submitted', 'voided') "
            "AND snapshot_ciphertext IS NOT NULL "
            "AND snapshot_key_version IS NOT NULL "
            "AND snapshot_digest IS NOT NULL) OR "
            "(status = 'failed' AND snapshot_ciphertext IS NULL "
            "AND snapshot_key_version IS NULL "
            "AND snapshot_digest IS NULL))",
            name="ck_housing_report_batches_snapshot_state",
        ),
        sa.CheckConstraint(
            "(submitted_at IS NULL AND submitted_by_user_id IS NULL) OR "
            "(submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL)",
            name="ck_housing_report_batches_submitted_fields",
        ),
        sa.CheckConstraint(
            "status <> 'marked_submitted' OR "
            "(submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL)",
            name="ck_housing_report_batches_submitted_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('generated', 'failed') OR "
            "(submitted_at IS NULL AND submitted_by_user_id IS NULL "
            "AND submission_note IS NULL)",
            name="ck_housing_report_batches_unsubmitted_status",
        ),
        sa.CheckConstraint(
            "submission_note IS NULL OR "
            "(submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL)",
            name="ck_housing_report_batches_submission_note",
        ),
        sa.CheckConstraint(
            "((status = 'voided' AND voided_at IS NOT NULL "
            "AND voided_by_user_id IS NOT NULL AND void_reason IS NOT NULL "
            "AND btrim(void_reason) <> '') OR "
            "(status <> 'voided' AND voided_at IS NULL "
            "AND voided_by_user_id IS NULL AND void_reason IS NULL))",
            name="ck_housing_report_batches_voided_status",
        ),
        sa.CheckConstraint(
            "((status = 'failed' AND failure_code IS NOT NULL "
            "AND failure_summary IS NOT NULL "
            "AND housing_report_failure_summary_is_safe("
            "failure_code, failure_summary)) OR "
            "(status <> 'failed' AND failure_code IS NULL "
            "AND failure_summary IS NULL))",
            name="ck_housing_report_batches_failure_state",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR corrects_batch_id IS NULL",
            name="ck_housing_report_batches_failed_not_correction",
        ),
        sa.CheckConstraint(
            "corrects_batch_id IS NULL OR corrects_batch_id <> id",
            name="ck_housing_report_batches_corrects_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["corrects_batch_id"],
            ["housing_report_batches.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_number", name="uq_housing_report_batches_batch_number"),
    )
    op.create_index(
        "ix_housing_report_batches_status_created_at",
        "housing_report_batches",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_housing_report_batches_range",
        "housing_report_batches",
        ["range_start", "range_end", "id"],
    )
    op.create_index(
        "uq_housing_report_batches_corrects_batch_id",
        "housing_report_batches",
        ["corrects_batch_id"],
        unique=True,
        postgresql_where=sa.text("corrects_batch_id IS NOT NULL"),
    )

    op.create_table(
        "housing_report_batch_scopes",
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("building_id", sa.UUID(), nullable=False),
        sa.Column("floor_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["housing_report_batches.id"],
            name="fk_housing_report_batch_scopes_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["floor_id", "building_id"],
            ["housing_floors.id", "housing_floors.building_id"],
            name="fk_housing_report_batch_scopes_floor_building",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "batch_id",
            "floor_id",
            name="pk_housing_report_batch_scopes",
        ),
    )
    op.create_index(
        "ix_housing_report_batch_scopes_building_floor",
        "housing_report_batch_scopes",
        ["building_id", "floor_id", "batch_id"],
    )


def _create_reporting_immutability_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_housing_report_batch_correction_predecessor()
        RETURNS trigger AS $$
        DECLARE
            predecessor_status text;
        BEGIN
            IF NEW.status NOT IN ('generated', 'failed') OR NEW.version <> 1 THEN
                RAISE EXCEPTION 'housing report batch initial state is invalid'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_housing_report_batches_initial_state';
            END IF;
            IF NEW.corrects_batch_id IS NULL THEN
                RETURN NEW;
            END IF;
            IF NEW.corrects_batch_id = NEW.id THEN
                RAISE EXCEPTION 'housing report batch cannot correct itself'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_housing_report_batches_corrects_not_self';
            END IF;

            SELECT status
              INTO predecessor_status
              FROM housing_report_batches
             WHERE id = NEW.corrects_batch_id
             FOR UPDATE;

            IF predecessor_status IS DISTINCT FROM 'voided' THEN
                RAISE EXCEPTION 'housing report correction predecessor must be voided'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_housing_report_batches_correction_predecessor';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_housing_report_batches_correction_predecessor
        BEFORE INSERT ON housing_report_batches
        FOR EACH ROW EXECUTE FUNCTION
            enforce_housing_report_batch_correction_predecessor()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_housing_report_batch_update()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.corrects_batch_id IS NOT NULL
               AND NEW.corrects_batch_id = NEW.id THEN
                RAISE EXCEPTION 'housing report batch cannot correct itself'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_housing_report_batches_corrects_not_self';
            END IF;

            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.batch_number IS DISTINCT FROM NEW.batch_number
               OR OLD.range_start IS DISTINCT FROM NEW.range_start
               OR OLD.range_end IS DISTINCT FROM NEW.range_end
               OR OLD.cohort_filter IS DISTINCT FROM NEW.cohort_filter
               OR OLD.person_type_filter IS DISTINCT FROM NEW.person_type_filter
               OR OLD.purpose IS DISTINCT FROM NEW.purpose
               OR OLD.projection_schema_version IS DISTINCT FROM
                    NEW.projection_schema_version
               OR OLD.snapshot_ciphertext IS DISTINCT FROM NEW.snapshot_ciphertext
               OR OLD.snapshot_key_version IS DISTINCT FROM NEW.snapshot_key_version
               OR OLD.snapshot_digest IS DISTINCT FROM NEW.snapshot_digest
               OR OLD.resident_count IS DISTINCT FROM NEW.resident_count
               OR OLD.stay_row_count IS DISTINCT FROM NEW.stay_row_count
               OR OLD.guardian_row_count IS DISTINCT FROM NEW.guardian_row_count
               OR OLD.created_at IS DISTINCT FROM NEW.created_at
               OR OLD.created_by_user_id IS DISTINCT FROM NEW.created_by_user_id
               OR OLD.corrects_batch_id IS DISTINCT FROM NEW.corrects_batch_id
               OR OLD.failure_code IS DISTINCT FROM NEW.failure_code
               OR OLD.failure_summary IS DISTINCT FROM NEW.failure_summary THEN
                RAISE EXCEPTION 'housing report batch business fields are immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF NOT (
                (OLD.status = 'generated'
                    AND NEW.status IN ('marked_submitted', 'voided'))
                OR (OLD.status = 'marked_submitted' AND NEW.status = 'voided')
            ) THEN
                RAISE EXCEPTION 'invalid housing report batch state transition'
                    USING ERRCODE = '55000';
            END IF;

            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'housing report batch version must increment by one'
                    USING ERRCODE = '55000';
            END IF;

            IF (
                OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
                OR OLD.submitted_by_user_id IS DISTINCT FROM
                    NEW.submitted_by_user_id
                OR OLD.submission_note IS DISTINCT FROM NEW.submission_note
            ) AND NOT (
                OLD.status = 'generated' AND NEW.status = 'marked_submitted'
            ) THEN
                RAISE EXCEPTION 'submitted housing report fields are immutable'
                    USING ERRCODE = '55000';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_housing_report_batches_update
        BEFORE UPDATE ON housing_report_batches
        FOR EACH ROW EXECUTE FUNCTION enforce_housing_report_batch_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_housing_report_batch_delete()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'housing report batches cannot be deleted'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_housing_report_batches_no_delete
        BEFORE DELETE ON housing_report_batches
        FOR EACH ROW EXECUTE FUNCTION prevent_housing_report_batch_delete()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_housing_report_batch_scope_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'housing report batch scopes are immutable'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_housing_report_batch_scopes_immutable
        BEFORE UPDATE OR DELETE ON housing_report_batch_scopes
        FOR EACH ROW EXECUTE FUNCTION prevent_housing_report_batch_scope_mutation()
        """
    )


def _seed_permission_and_grant(bind: sa.Connection) -> None:
    permission_key, permission_id, permission_name, sensitive = REPORT_PERMISSION
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    bind.execute(
        permissions.insert().values(
            id=permission_id,
            key=permission_key,
            name=permission_name,
            sensitive=sensitive,
        )
    )
    admin_role_id = bind.scalar(sa.text("SELECT id FROM roles WHERE key = 'admin'"))
    if admin_role_id is None:
        raise RuntimeError("cannot seed housing reporting without admin role")
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    bind.execute(
        role_permissions.insert().values(
            role_id=admin_role_id,
            permission_id=permission_id,
            scope_kind="global",
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight_upgrade(bind)
    _create_failure_summary_validator()
    _create_report_batch_tables()
    _create_reporting_immutability_triggers()
    _seed_permission_and_grant(bind)


def _preflight_downgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE housing_report_batch_scopes, housing_report_batches, "
            "permissions, role_permissions, roles, user_permission_overrides, "
            "cohort_staff_assignment_permissions IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM housing_report_batch_scopes) "
            "OR EXISTS (SELECT 1 FROM housing_report_batches)"
        )
    ):
        raise RuntimeError("cannot downgrade housing reporting with report data")

    permission_key, permission_id, permission_name, sensitive = REPORT_PERMISSION
    permission_rows = set(
        bind.execute(
            sa.text(
                "SELECT id, key, name, sensitive FROM permissions "
                "WHERE id = :permission_id OR key = :permission_key"
            ),
            {"permission_id": permission_id, "permission_key": permission_key},
        ).tuples()
    )
    if permission_rows != {(permission_id, permission_key, permission_name, sensitive)}:
        raise RuntimeError("cannot downgrade housing reporting with customized permission")
    grants = {
        grant
        for grant in _role_permission_grants(bind)
        if grant[1] == permission_key
    }
    if grants != {("admin", permission_key, "global")}:
        raise RuntimeError("cannot downgrade housing reporting with customized grant")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_permission_overrides "
            "WHERE permission_id = :permission_id)"
        ),
        {"permission_id": permission_id},
    ):
        raise RuntimeError("cannot downgrade housing reporting with custom overrides")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM cohort_staff_assignment_permissions "
            "WHERE permission_id = :permission_id)"
        ),
        {"permission_id": permission_id},
    ):
        raise RuntimeError("cannot downgrade housing reporting with custom assignments")


def downgrade() -> None:
    bind = op.get_bind()
    _preflight_downgrade(bind)
    permission_key, permission_id, _, _ = REPORT_PERMISSION
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions USING roles, permissions "
            "WHERE role_permissions.role_id = roles.id "
            "AND role_permissions.permission_id = permissions.id "
            "AND roles.key = 'admin' AND permissions.key = :permission_key "
            "AND role_permissions.scope_kind = 'global'"
        ),
        {"permission_key": permission_key},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE id = :permission_id"),
        {"permission_id": permission_id},
    )
    op.execute(
        "DROP TRIGGER trg_housing_report_batch_scopes_immutable "
        "ON housing_report_batch_scopes"
    )
    op.execute(
        "DROP FUNCTION prevent_housing_report_batch_scope_mutation()"
    )
    op.execute(
        "DROP TRIGGER trg_housing_report_batches_no_delete "
        "ON housing_report_batches"
    )
    op.execute("DROP FUNCTION prevent_housing_report_batch_delete()")
    op.execute(
        "DROP TRIGGER trg_housing_report_batches_update "
        "ON housing_report_batches"
    )
    op.execute("DROP FUNCTION enforce_housing_report_batch_update()")
    op.execute(
        "DROP TRIGGER trg_housing_report_batches_correction_predecessor "
        "ON housing_report_batches"
    )
    op.execute(
        "DROP FUNCTION enforce_housing_report_batch_correction_predecessor()"
    )
    op.drop_index(
        "ix_housing_report_batch_scopes_building_floor",
        table_name="housing_report_batch_scopes",
    )
    op.drop_table("housing_report_batch_scopes")
    op.drop_index(
        "uq_housing_report_batches_corrects_batch_id",
        table_name="housing_report_batches",
    )
    op.drop_index(
        "ix_housing_report_batches_range",
        table_name="housing_report_batches",
    )
    op.drop_index(
        "ix_housing_report_batches_status_created_at",
        table_name="housing_report_batches",
    )
    op.drop_table("housing_report_batches")
    op.execute(
        "DROP FUNCTION housing_report_failure_summary_is_safe(text, jsonb)"
    )
    op.execute(f"DROP SEQUENCE {REPORT_BATCH_NUMBER_SEQUENCE}")
