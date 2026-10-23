"""Add typed pending passport facts without rewriting historical evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0069_pending_profile_intake"
down_revision = "0068_deferred_email_claims"
branch_labels = None
depends_on = None

PEOPLE_CHECKS = {
    "ck_people_document_type": "document_type IN ('cn_id','passport')",
    "ck_people_intake_mode": "intake_mode IN ('strict','pending')",
    "ck_people_team_allocation": "team_allocation_status IN ('untracked','pending','assigned','not_required') AND team_allocation_version >= 1",
    "ck_people_document_keys": "(document_type='passport' AND passport_number_digest IS NOT NULL AND (document_issuing_country IS NULL OR document_issuing_country ~ '^[A-Z]{2}$')) OR (document_type='cn_id' AND passport_number_digest IS NULL AND document_issuing_country IS NULL)",
    "ck_people_required_identity_facts": "(intake_mode='pending' AND document_type='passport') OR (sex IS NOT NULL AND date_of_birth IS NOT NULL AND (document_type='cn_id' OR document_issuing_country IS NOT NULL))",
}


def _kind(pending: bool) -> None:
    op.drop_constraint(
        "ck_personnel_intake_requests_kind", "personnel_intake_requests", type_="check"
    )
    op.create_check_constraint(
        "ck_personnel_intake_requests_kind",
        "personnel_intake_requests",
        "kind IN ('student','player','both'" + (",'staff')" if pending else ")"),
    )
    op.drop_constraint("ck_assisted_approval_operation", "assisted_approvals", type_="check")
    op.create_check_constraint(
        "ck_assisted_approval_operation",
        "assisted_approvals",
        "(operation_key IS NULL AND operation_digest IS NULL AND operation_kind IS NULL) OR "
        "(operation_key IS NOT NULL AND operation_digest IS NOT NULL AND operation_digest ~ '^[0-9a-f]{64}$' AND operation_kind IS NOT NULL AND operation_kind IN ('direct_create','correction','claim_bind','claim_revoke'"
        + (",'claim_complete'))" if pending else "))"),
    )


def _binding_immutable(include_origin: bool) -> None:
    columns = "claim_id,person_id,email_digest,approval_id,created_at"
    if include_origin:
        columns += ",email_binding_origin"
    checks = " OR ".join(
        f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in columns.split(",")
    )
    op.execute(
        f"CREATE OR REPLACE FUNCTION profile_claim_bindings_evidence_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {checks} OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN RAISE EXCEPTION 'profile claim evidence is immutable' USING ERRCODE='23514'; END IF; RETURN NEW; END $$"
    )


def _session_immutable(include_version: bool) -> None:
    columns = "token_digest,challenge_id,payload_digest,identity_digest,email_digest,expires_at,created_at"
    if include_version:
        columns += ",submission_version"
    checks = " OR ".join(
        f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in columns.split(",")
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION profile_claim_sessions_evidence_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF "
        + checks
        + " OR (OLD.payload_ciphertext IS DISTINCT FROM NEW.payload_ciphertext AND NEW.payload_ciphertext IS NOT NULL)"
        " OR (OLD.verified_at IS NOT NULL AND NEW.verified_at IS DISTINCT FROM OLD.verified_at)"
        " OR (OLD.claim_id IS NOT NULL AND NEW.claim_id IS DISTINCT FROM OLD.claim_id)"
        " OR (OLD.state='verified' AND NEW.state <> 'verified')"
        " THEN RAISE EXCEPTION 'profile claim evidence is immutable' USING ERRCODE='23514'; END IF; RETURN NEW; END $$"
    )


def _claim_submission_versions(typed: bool) -> None:
    op.drop_constraint("ck_profile_claim_versions", "profile_claims", type_="check")
    op.create_check_constraint(
        "ck_profile_claim_versions",
        "profile_claims",
        "version >= 1 AND "
        + ("submission_version IN (1,2)" if typed else "submission_version >= 1"),
    )


def upgrade() -> None:
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    for name, column_type, default, nullable in (
        ("document_type", sa.String(16), "cn_id", False),
        ("document_issuing_country", sa.String(2), None, True),
        ("passport_number_digest", sa.String(64), None, True),
        ("intake_mode", sa.String(16), "strict", False),
        ("team_allocation_status", sa.String(16), "untracked", False),
        ("team_allocation_version", sa.Integer(), "1", False),
    ):
        op.add_column(
            "people", sa.Column(name, column_type, nullable=nullable, server_default=default)
        )
    op.alter_column("people", "sex", nullable=True)
    op.alter_column("people", "date_of_birth", nullable=True)
    for name, expression in PEOPLE_CHECKS.items():
        op.create_check_constraint(name, "people", expression)
    op.create_index("ix_people_passport_number_digest", "people", ["passport_number_digest"])
    op.add_column(
        "profile_claim_bindings",
        sa.Column(
            "email_binding_origin", sa.String(16), nullable=False, server_default="introduced"
        ),
    )
    op.create_check_constraint(
        "ck_profile_claim_binding_email_origin",
        "profile_claim_bindings",
        "email_binding_origin IN ('introduced','preserved')",
    )
    _binding_immutable(True)
    op.add_column(
        "profile_claim_sessions",
        sa.Column("submission_version", sa.SmallInteger(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_profile_claim_session_submission_version",
        "profile_claim_sessions",
        "submission_version IN (1,2)",
    )
    _session_immutable(True)
    _claim_submission_versions(True)
    _kind(True)
    # Deferred cross-table guard inspects final state, allowing facts and account
    # to be completed in the same transaction, in either write order.
    op.execute("""CREATE FUNCTION require_account_identity_facts() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE target UUID;
    BEGIN
      IF TG_TABLE_NAME = 'people' THEN target := NEW.id; ELSE target := NEW.person_id; END IF;
      PERFORM 1 FROM people WHERE id=target FOR UPDATE;
      IF EXISTS (SELECT 1 FROM people p JOIN user_accounts u ON u.person_id=p.id
                 WHERE p.id=target AND (p.sex IS NULL OR p.date_of_birth IS NULL OR
                   (p.document_type='passport' AND p.document_issuing_country IS NULL))) THEN
        RAISE EXCEPTION 'account requires complete identity facts' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_accounts_identity_facts AFTER INSERT OR UPDATE ON user_accounts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_account_identity_facts()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_people_account_identity_facts AFTER INSERT OR UPDATE ON people DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_account_identity_facts()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(
        "LOCK TABLE people, user_accounts, personnel_intake_requests, assisted_approvals, profile_claim_bindings, profile_claim_sessions, profile_claims IN ACCESS EXCLUSIVE MODE"
    )
    for statement in (
        "SELECT EXISTS(SELECT 1 FROM people WHERE document_type <> 'cn_id' OR intake_mode <> 'strict' OR document_issuing_country IS NOT NULL OR passport_number_digest IS NOT NULL OR team_allocation_status <> 'untracked' OR team_allocation_version <> 1)",
        "SELECT EXISTS(SELECT 1 FROM personnel_intake_requests WHERE kind='staff')",
        "SELECT EXISTS(SELECT 1 FROM assisted_approvals WHERE operation_kind='claim_complete')",
        "SELECT EXISTS(SELECT 1 FROM profile_claim_bindings WHERE email_binding_origin<>'introduced')",
        "SELECT EXISTS(SELECT 1 FROM profile_claim_sessions WHERE submission_version<>1)",
        "SELECT EXISTS(SELECT 1 FROM profile_claims WHERE submission_version<>1)",
    ):
        if bind.scalar(sa.text(statement)):
            raise RuntimeError("pending profile downgrade would discard business facts")
    op.execute("DROP TRIGGER trg_accounts_identity_facts ON user_accounts")
    op.execute("DROP TRIGGER trg_people_account_identity_facts ON people")
    op.execute("DROP FUNCTION require_account_identity_facts()")
    _kind(False)
    _claim_submission_versions(False)
    _session_immutable(False)
    op.drop_constraint(
        "ck_profile_claim_session_submission_version", "profile_claim_sessions", type_="check"
    )
    op.drop_column("profile_claim_sessions", "submission_version")
    _binding_immutable(False)
    op.drop_constraint(
        "ck_profile_claim_binding_email_origin", "profile_claim_bindings", type_="check"
    )
    op.drop_column("profile_claim_bindings", "email_binding_origin")
    for name in PEOPLE_CHECKS:
        op.drop_constraint(name, "people", type_="check")
    op.alter_column("people", "sex", nullable=False)
    op.alter_column("people", "date_of_birth", nullable=False)
    op.drop_index("ix_people_passport_number_digest", table_name="people")
    for name in (
        "team_allocation_version",
        "team_allocation_status",
        "intake_mode",
        "passport_number_digest",
        "document_issuing_country",
        "document_type",
    ):
        op.drop_column("people", name)
