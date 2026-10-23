"""Add explicit organization managers and relationship-scoped housing read grants."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065_invited_housing_visibility"
down_revision = "0064_cloud_auth_builtin_import"
branch_labels = None
depends_on = None

TABLE = "revenue_share_organization_manager_assignments"
PERMISSION_ID = "20000000-0000-4000-8000-000000000086"
STANDARD_ROLES = (
    "admin",
    "coach",
    "customer_service",
    "life_teacher",
    "staff",
    "guardian",
    "student",
    "registered_user",
    "super_admin",
    "administration",
    "finance",
    "team_member",
    "team_manager",
)
SOURCE_INDEXES = (
    (
        "ix_student_referral_bindings_person_source",
        ["referrer_person_id", "student_person_id"],
        "source_kind = 'person'",
    ),
    (
        "ix_student_referral_bindings_org_source",
        ["referrer_organization_id", "student_person_id"],
        "source_kind = 'organization'",
    ),
)


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        TABLE,
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "organization_id",
            uuid,
            sa.ForeignKey("revenue_share_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            uuid,
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "granted_by_user_id",
            uuid,
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "revoked_by_user_id", uuid, sa.ForeignKey("user_accounts.id", ondelete="RESTRICT")
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_user_id IS NULL) OR (revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL)",
            name="ck_referral_org_manager_revocation_pair",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="ck_referral_org_manager_revocation_time",
        ),
    )
    op.create_index(
        "uq_referral_org_manager_active",
        TABLE,
        ["organization_id", "account_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_referral_org_manager_account_active",
        TABLE,
        ["account_id", "organization_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    for name, columns, predicate in SOURCE_INDEXES:
        op.create_index(
            name, "student_referral_bindings", columns, postgresql_where=sa.text(predicate)
        )
    op.create_index(
        "ix_housing_stays_person_latest_checkout",
        "housing_stays",
        ["person_id", sa.text("actual_check_out_at DESC"), "id"],
        postgresql_where=sa.text("status = 'checked_out'"),
    )
    op.execute(
        sa.text(
            "INSERT INTO permissions (id, key, name, sensitive) VALUES (CAST(:id AS uuid), 'referrals.read_invited_housing', '查看受邀人员住宿', true)"
        ).bindparams(id=PERMISSION_ID)
    )
    for role_key in STANDARD_ROLES:
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT id, CAST(:permission_id AS uuid), 'self' FROM roles WHERE key = :key "
                "ON CONFLICT DO NOTHING"
            ).bindparams(permission_id=PERMISSION_ID, key=role_key)
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Hold the guard and its destructive DDL in the same PostgreSQL transaction.
    # Reference tables come first, in grant-compatible order: DROP TABLE removes
    # FK triggers there and must not wait on a grant blocked by our history lock.
    bind.execute(
        sa.text("LOCK TABLE user_accounts, revenue_share_organizations IN ACCESS EXCLUSIVE MODE")
    )
    bind.execute(sa.text(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"))
    # The fixed order also freezes custom RBAC rules before inspecting them.
    bind.execute(
        sa.text(
            "LOCK TABLE roles, permissions, role_permissions, user_permission_overrides, "
            "cohort_staff_assignment_permissions IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    if bind.scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})")):
        raise RuntimeError("invited housing downgrade requires no organization manager history")
    for table in ("user_permission_overrides", "cohort_staff_assignment_permissions"):
        if bind.scalar(
            sa.text(
                f"SELECT EXISTS(SELECT 1 FROM {table} WHERE permission_id = CAST(:id AS uuid))"
            ).bindparams(id=PERMISSION_ID)
        ):
            raise RuntimeError("invited housing downgrade requires no custom permission rules")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
            "WHERE rp.permission_id = CAST(:id AS uuid) AND "
            "(rp.scope_kind <> 'self' OR r.key NOT IN :role_keys))"
        )
        .bindparams(sa.bindparam("role_keys", expanding=True), id=PERMISSION_ID)
        .params(role_keys=STANDARD_ROLES)
    ):
        raise RuntimeError("invited housing downgrade requires no custom permission rules")
    op.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_id = CAST(:id AS uuid)").bindparams(
            id=PERMISSION_ID
        )
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE id = CAST(:id AS uuid)").bindparams(id=PERMISSION_ID)
    )
    op.drop_index("ix_housing_stays_person_latest_checkout", table_name="housing_stays")
    for name, _, _ in SOURCE_INDEXES:
        op.drop_index(name, table_name="student_referral_bindings")
    op.drop_table(TABLE)
