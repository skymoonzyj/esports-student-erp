"""Add referral attribution, revenue-share ledger, settlements, and finance role."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_revenue_sharing"
down_revision: str | Sequence[str] | None = "0034_person_nicknames"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FINANCE_ROLE_ID = "10000000-0000-4000-8000-000000000011"
PERMISSIONS = {
    "revenue_share.read_self": (
        "20000000-0000-4000-8000-000000000061",
        "查看本人分成",
        False,
    ),
    "revenue_share.finance": (
        "20000000-0000-4000-8000-000000000062",
        "管理分成结算",
        True,
    ),
    "revenue_share.manage": (
        "20000000-0000-4000-8000-000000000063",
        "管理邀请与分成规则",
        True,
    ),
    "revenue_share.export": (
        "20000000-0000-4000-8000-000000000064",
        "导出分成结算",
        True,
    ),
}


def _seed_rbac() -> None:
    op.execute(
        sa.text(
            "INSERT INTO roles (id, key, name) VALUES "
            "(CAST(:id AS uuid), 'finance', '财务') "
            "ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name"
        ).bindparams(id=FINANCE_ROLE_ID)
    )
    for key, (permission_id, name, sensitive) in PERMISSIONS.items():
        op.execute(
            sa.text(
                "INSERT INTO permissions (id, key, name, sensitive) VALUES "
                "(CAST(:id AS uuid), :key, :name, :sensitive) "
                "ON CONFLICT (key) DO UPDATE SET "
                "name = EXCLUDED.name, sensitive = EXCLUDED.sensitive"
            ).bindparams(
                id=permission_id,
                key=key,
                name=name,
                sensitive=sensitive,
            )
        )
    grants = {
        *(("super_admin", key, "global") for key in PERMISSIONS),
        *(("admin", key, "global") for key in PERMISSIONS),
        ("finance", "payments.read", "global"),
        ("finance", "revenue_share.read_self", "self"),
        ("finance", "revenue_share.finance", "global"),
        ("finance", "revenue_share.export", "global"),
        *(
            (role, "revenue_share.read_self", "self")
            for role in (
                "super_admin",
                "admin",
                "coach",
                "customer_service",
                "life_teacher",
                "staff",
                "guardian",
                "student",
                "registered_user",
                "administration",
            )
        ),
        ("finance", "assets.read_self", "self"),
    }
    for role_key, permission_key, scope_kind in sorted(grants):
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT r.id, p.id, :scope_kind FROM roles r, permissions p "
                "WHERE r.key = :role_key AND p.key = :permission_key "
                "ON CONFLICT DO NOTHING"
            ).bindparams(
                role_key=role_key,
                permission_key=permission_key,
                scope_kind=scope_kind,
            )
        )


def upgrade() -> None:
    op.create_table(
        "revenue_share_organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_revenue_share_organizations_name"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_revenue_share_organizations_name"),
    )
    op.create_table(
        "referral_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("referrer_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("referrer_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_kind IN ('person', 'organization')", name="ck_referral_codes_source_kind"
        ),
        sa.CheckConstraint(
            "(source_kind = 'person' AND referrer_person_id IS NOT NULL AND referrer_organization_id IS NULL) OR "
            "(source_kind = 'organization' AND referrer_person_id IS NULL AND referrer_organization_id IS NOT NULL)",
            name="ck_referral_codes_source",
        ),
        sa.CheckConstraint("btrim(label) <> ''", name="ck_referral_codes_label"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["referrer_organization_id"], ["revenue_share_organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["referrer_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_referral_codes_code"),
    )
    op.add_column(
        "registration_applications",
        sa.Column("referral_code_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_registration_applications_referral_code",
        "registration_applications",
        "referral_codes",
        ["referral_code_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "student_referral_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("referral_code_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("registration_application_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("referrer_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("referrer_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "bound_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "source_kind IN ('person', 'organization')",
            name="ck_student_referral_bindings_source_kind",
        ),
        sa.CheckConstraint(
            "(source_kind = 'person' AND referrer_person_id IS NOT NULL AND referrer_organization_id IS NULL) OR "
            "(source_kind = 'organization' AND referrer_person_id IS NULL AND referrer_organization_id IS NOT NULL)",
            name="ck_student_referral_bindings_source",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["referral_code_id"], ["referral_codes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["referrer_organization_id"], ["revenue_share_organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["referrer_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["registration_application_id"], ["registration_applications.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["student_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "registration_application_id", name="uq_student_referral_bindings_application"
        ),
        sa.UniqueConstraint("student_person_id", name="uq_student_referral_bindings_student"),
    )
    op.create_table(
        "revenue_share_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role_kind", sa.String(length=32), nullable=False),
        sa.Column("recipient_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recipient_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("calculation_type", sa.String(length=16), nullable=False),
        sa.Column("percentage_basis_points", sa.Integer(), nullable=True),
        sa.Column("fixed_amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role_kind IN ('inviter', 'subject_coach', 'platform_developer', 'assistant', 'additional')",
            name="ck_revenue_share_rules_role_kind",
        ),
        sa.CheckConstraint(
            "calculation_type IN ('percentage', 'fixed')",
            name="ck_revenue_share_rules_calculation_type",
        ),
        sa.CheckConstraint(
            "(calculation_type = 'percentage' AND percentage_basis_points BETWEEN 1 AND 10000 AND fixed_amount_cents IS NULL) OR (calculation_type = 'fixed' AND percentage_basis_points IS NULL AND fixed_amount_cents > 0)",
            name="ck_revenue_share_rules_value",
        ),
        sa.CheckConstraint(
            "(role_kind IN ('inviter', 'subject_coach', 'assistant') AND recipient_person_id IS NULL AND recipient_organization_id IS NULL) OR (role_kind IN ('platform_developer', 'additional') AND ((recipient_person_id IS NOT NULL AND recipient_organization_id IS NULL) OR (recipient_person_id IS NULL AND recipient_organization_id IS NOT NULL)))",
            name="ck_revenue_share_rules_recipient",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_revenue_share_rules_effective_range",
        ),
        sa.CheckConstraint(
            "label IS NULL OR btrim(label) <> ''", name="ck_revenue_share_rules_label"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["game_projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["recipient_organization_id"], ["revenue_share_organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["recipient_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_revenue_share_rules_resolution",
        "revenue_share_rules",
        ["project_id", "role_kind", "is_active", "effective_from"],
    )
    op.create_table(
        "revenue_share_payment_statuses",
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "reconciled_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'allocated')", name="ck_revenue_share_payment_statuses_state"
        ),
        sa.CheckConstraint(
            "(state = 'allocated' AND reason_code IS NULL) OR (state = 'pending' AND reason_code IS NOT NULL)",
            name="ck_revenue_share_payment_statuses_reason",
        ),
        sa.ForeignKeyConstraint(["enrollment_id"], ["enrollments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["game_projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("payment_id"),
    )
    op.create_index(
        "ix_revenue_share_payment_statuses_state",
        "revenue_share_payment_statuses",
        ["state", "updated_at"],
    )
    op.create_table(
        "revenue_share_ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.String(length=180), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refund_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entry_kind", sa.String(length=24), nullable=False),
        sa.Column("role_kind", sa.String(length=32), nullable=False),
        sa.Column("beneficiary_kind", sa.String(length=16), nullable=False),
        sa.Column("beneficiary_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("beneficiary_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("calculation_type", sa.String(length=16), nullable=False),
        sa.Column("percentage_basis_points_snapshot", sa.Integer(), nullable=True),
        sa.Column("fixed_amount_cents_snapshot", sa.BigInteger(), nullable=True),
        sa.Column("base_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "entry_kind IN ('earning', 'refund_adjustment', 'manual_adjustment')",
            name="ck_revenue_share_ledger_entries_kind",
        ),
        sa.CheckConstraint(
            "role_kind IN ('inviter', 'subject_coach', 'platform_developer', 'assistant', 'additional')",
            name="ck_revenue_share_ledger_entries_role_kind",
        ),
        sa.CheckConstraint(
            "(beneficiary_kind = 'person' AND beneficiary_person_id IS NOT NULL AND beneficiary_organization_id IS NULL) OR (beneficiary_kind = 'organization' AND beneficiary_person_id IS NULL AND beneficiary_organization_id IS NOT NULL)",
            name="ck_revenue_share_ledger_entries_beneficiary",
        ),
        sa.CheckConstraint("amount_cents <> 0", name="ck_revenue_share_ledger_entries_amount"),
        sa.CheckConstraint("base_amount_cents > 0", name="ck_revenue_share_ledger_entries_base"),
        sa.CheckConstraint(
            "(entry_kind = 'earning' AND amount_cents > 0 AND refund_id IS NULL AND parent_entry_id IS NULL) OR (entry_kind = 'refund_adjustment' AND amount_cents < 0 AND refund_id IS NOT NULL AND parent_entry_id IS NOT NULL) OR entry_kind = 'manual_adjustment'",
            name="ck_revenue_share_ledger_entries_event_fields",
        ),
        sa.ForeignKeyConstraint(
            ["beneficiary_organization_id"], ["revenue_share_organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["beneficiary_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["parent_entry_id"], ["revenue_share_ledger_entries.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["refund_id"], ["payment_refunds.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rule_id"], ["revenue_share_rules.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_revenue_share_ledger_entries_event_key"),
    )
    op.create_index(
        "ix_revenue_share_ledger_entries_person",
        "revenue_share_ledger_entries",
        ["beneficiary_person_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_revenue_share_ledger_entries_organization",
        "revenue_share_ledger_entries",
        ["beneficiary_organization_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_revenue_share_ledger_entries_payment",
        "revenue_share_ledger_entries",
        ["payment_id", "id"],
    )
    op.create_table(
        "revenue_share_settlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("beneficiary_kind", sa.String(length=16), nullable=False),
        sa.Column("beneficiary_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("beneficiary_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("settled_on", sa.Date(), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(beneficiary_kind = 'person' AND beneficiary_person_id IS NOT NULL AND beneficiary_organization_id IS NULL) OR (beneficiary_kind = 'organization' AND beneficiary_person_id IS NULL AND beneficiary_organization_id IS NOT NULL)",
            name="ck_revenue_share_settlements_beneficiary",
        ),
        sa.CheckConstraint("amount_cents > 0", name="ck_revenue_share_settlements_amount"),
        sa.CheckConstraint("btrim(reference) <> ''", name="ck_revenue_share_settlements_reference"),
        sa.ForeignKeyConstraint(
            ["beneficiary_organization_id"], ["revenue_share_organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["beneficiary_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_revenue_share_settlements_person",
        "revenue_share_settlements",
        ["beneficiary_person_id", "settled_on", "id"],
    )
    op.create_index(
        "ix_revenue_share_settlements_organization",
        "revenue_share_settlements",
        ["beneficiary_organization_id", "settled_on", "id"],
    )
    _seed_rbac()


def downgrade() -> None:
    op.drop_index(
        "ix_revenue_share_settlements_organization", table_name="revenue_share_settlements"
    )
    op.drop_index("ix_revenue_share_settlements_person", table_name="revenue_share_settlements")
    op.drop_table("revenue_share_settlements")
    op.drop_index(
        "ix_revenue_share_ledger_entries_payment", table_name="revenue_share_ledger_entries"
    )
    op.drop_index(
        "ix_revenue_share_ledger_entries_organization", table_name="revenue_share_ledger_entries"
    )
    op.drop_index(
        "ix_revenue_share_ledger_entries_person", table_name="revenue_share_ledger_entries"
    )
    op.drop_table("revenue_share_ledger_entries")
    op.drop_index(
        "ix_revenue_share_payment_statuses_state", table_name="revenue_share_payment_statuses"
    )
    op.drop_table("revenue_share_payment_statuses")
    op.drop_index("ix_revenue_share_rules_resolution", table_name="revenue_share_rules")
    op.drop_table("revenue_share_rules")
    op.drop_table("student_referral_bindings")
    op.drop_constraint(
        "fk_registration_applications_referral_code",
        "registration_applications",
        type_="foreignkey",
    )
    op.drop_column("registration_applications", "referral_code_id")
    op.drop_table("referral_codes")
    op.drop_table("revenue_share_organizations")
    for key in PERMISSIONS:
        op.execute(
            sa.text(
                "DELETE FROM role_permissions WHERE permission_id = "
                "(SELECT id FROM permissions WHERE key = :key)"
            ).bindparams(key=key)
        )
        op.execute(sa.text("DELETE FROM permissions WHERE key = :key").bindparams(key=key))
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id = "
            "(SELECT id FROM roles WHERE key = 'finance')"
        )
    )
    op.execute(sa.text("DELETE FROM roles WHERE key = 'finance'"))
