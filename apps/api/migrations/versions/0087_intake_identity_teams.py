"""Preserve managed identity intent and personnel-level team staff evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0087_intake_identity_teams"
down_revision = "0086_intake_activation_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intake_identity_selections",
        sa.Column(
            "person_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("origin_approval_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("primary_identity", sa.String(64), nullable=False),
        sa.Column("concurrent_identities", pg.ARRAY(sa.String(64)), nullable=False),
        sa.Column("primary_staff_identity", sa.String(64)),
        sa.Column("resolved_staff_type", sa.String(32)),
        sa.Column(
            "created_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("operation_summary", pg.JSONB(), nullable=False),
        sa.Column("operation_digest", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["origin_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            ondelete="RESTRICT",
            name="fk_intake_identity_selection_approval_person",
        ),
        sa.CheckConstraint(
            "operation_digest ~ '^[0-9a-f]{64}$'", name="ck_intake_identity_selection_digest"
        ),
    )
    op.create_table(
        "person_team_staff_positions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("professional_teams.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("staff_role", sa.String(24), nullable=False),
        sa.Column("origin_approval_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column(
            "ended_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        ),
        sa.Column("end_reason", sa.String(500)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "account_assignment_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("team_staff_assignments.id", ondelete="RESTRICT"),
        ),
        sa.ForeignKeyConstraint(
            ["origin_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            ondelete="RESTRICT",
            name="fk_person_team_staff_approval_person",
        ),
        sa.CheckConstraint(
            "staff_role IN ('manager','coach','assistant_coach')", name="ck_person_team_staff_role"
        ),
        sa.CheckConstraint("version >= 1", name="ck_person_team_staff_version"),
        sa.CheckConstraint(
            "(ended_at IS NULL AND ended_by_user_id IS NULL AND end_reason IS NULL) OR (ended_at IS NOT NULL AND ended_by_user_id IS NOT NULL AND ended_at >= started_at AND end_reason IS NOT NULL AND length(btrim(end_reason)) BETWEEN 1 AND 500)",
            name="ck_person_team_staff_end",
        ),
    )
    op.create_index(
        "uq_person_team_staff_current",
        "person_team_staff_positions",
        ["person_id", "team_id", "staff_role"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "uq_person_team_staff_account_assignment",
        "person_team_staff_positions",
        ["account_assignment_id"],
        unique=True,
        postgresql_where=sa.text("account_assignment_id IS NOT NULL"),
    )
    op.create_index(
        "ix_person_team_staff_person_current",
        "person_team_staff_positions",
        ["person_id", "ended_at"],
    )
    op.create_index(
        "ix_person_team_staff_team_current", "person_team_staff_positions", ["team_id", "ended_at"]
    )
    op.create_index(
        "ix_person_team_staff_origin", "person_team_staff_positions", ["origin_approval_id"]
    )
    op.create_table(
        "person_team_staff_authorizations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "position_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("person_team_staff_positions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "authorizer_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("authorized_role_key", sa.String(64), nullable=False),
        sa.Column(
            "role_plan_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("intake_activation_role_plans.id", ondelete="RESTRICT"),
        ),
        sa.Column("operation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "position_id", "version", name="uq_person_team_staff_authorization_version"
        ),
        sa.CheckConstraint("version >= 1", name="ck_person_team_staff_authorization_version"),
        sa.CheckConstraint(
            "authorized_role_key IN ('team_manager','coach','assistant_coach')",
            name="ck_person_team_staff_authorization_role",
        ),
        sa.CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'", name="ck_person_team_staff_authorization_digest"
        ),
    )
    op.execute("""
        CREATE FUNCTION validate_person_staff_authorization() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE position_role varchar;
        BEGIN
          SELECT staff_role INTO position_role FROM person_team_staff_positions WHERE id=NEW.position_id;
          IF NEW.authorized_role_key IS DISTINCT FROM
             (CASE position_role WHEN 'manager' THEN 'team_manager' ELSE position_role END) THEN
            RAISE EXCEPTION 'staff authorization role does not match position' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER person_staff_authorization_role BEFORE INSERT OR UPDATE ON person_team_staff_authorizations FOR EACH ROW EXECUTE FUNCTION validate_person_staff_authorization()"
    )
    op.execute("""
        CREATE FUNCTION prevent_intake_identity_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'intake identity and staff authorization evidence is immutable' USING ERRCODE='23514';
        END $$
    """)
    for table in ("intake_identity_selections", "person_team_staff_authorizations"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_intake_identity_evidence_mutation()"
        )
    op.execute("""
        CREATE FUNCTION protect_person_team_staff_position_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'position history immutable: end instead of delete' USING ERRCODE='23514';
          END IF;
          IF ROW(NEW.id, NEW.person_id, NEW.team_id, NEW.staff_role,
                 NEW.origin_approval_id, NEW.created_by_user_id, NEW.started_at)
             IS DISTINCT FROM
             ROW(OLD.id, OLD.person_id, OLD.team_id, OLD.staff_role,
                 OLD.origin_approval_id, OLD.created_by_user_id, OLD.started_at) THEN
            RAISE EXCEPTION 'position history immutable: end and append instead of rewrite' USING ERRCODE='23514';
          END IF;
          IF OLD.ended_at IS NOT NULL AND
             ROW(NEW.ended_at, NEW.ended_by_user_id, NEW.end_reason)
             IS DISTINCT FROM
             ROW(OLD.ended_at, OLD.ended_by_user_id, OLD.end_reason) THEN
            RAISE EXCEPTION 'position end audit immutable' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER person_team_staff_positions_history_guard BEFORE UPDATE OR DELETE ON person_team_staff_positions FOR EACH ROW EXECUTE FUNCTION protect_person_team_staff_position_history()"
    )


def downgrade() -> None:
    connection = op.get_bind()
    # One lock statement fences all three tables before the first existence check.
    connection.execute(
        sa.text(
            "LOCK TABLE intake_identity_selections, person_team_staff_positions, person_team_staff_authorizations IN ACCESS EXCLUSIVE MODE"
        )
    )
    for table in (
        "intake_identity_selections",
        "person_team_staff_positions",
        "person_team_staff_authorizations",
    ):
        if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
            raise RuntimeError("人员身份或队内工作人员已有历史，拒绝降级删除")
    op.drop_table("person_team_staff_authorizations")
    op.execute("DROP FUNCTION validate_person_staff_authorization()")
    op.drop_table("person_team_staff_positions")
    op.execute("DROP FUNCTION protect_person_team_staff_position_history()")
    op.drop_table("intake_identity_selections")
    op.execute("DROP FUNCTION prevent_intake_identity_evidence_mutation()")
