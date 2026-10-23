"""Recoverable deferred-email profile claims; preserve historical snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0068_deferred_email_claims"
down_revision = "0067_person_profile_notes"
branch_labels = None
depends_on = None

TABLES = (
    "profile_claim_sessions",
    "profile_claims",
    "profile_claim_bindings",
    "profile_claim_decisions",
)


def _purpose(include_claim: bool) -> None:
    op.drop_constraint(
        "ck_registration_verification_challenges_purpose",
        "registration_verification_challenges",
        type_="check",
    )
    extra = ", 'profile_claim'" if include_claim else ""
    op.create_check_constraint(
        "ck_registration_verification_challenges_purpose",
        "registration_verification_challenges",
        "purpose IN ('registration','password_reset','guardian_invitation','add_login_identifier','assisted_activation'"
        + extra
        + ")",
    )


def _operation(include_claim: bool) -> None:
    op.drop_constraint("ck_assisted_approval_operation", "assisted_approvals", type_="check")
    extra = ", 'claim_bind', 'claim_revoke'" if include_claim else ""
    op.create_check_constraint(
        "ck_assisted_approval_operation",
        "assisted_approvals",
        "(operation_key IS NULL AND operation_digest IS NULL AND operation_kind IS NULL) OR (operation_key IS NOT NULL AND operation_digest IS NOT NULL AND operation_digest ~ '^[0-9a-f]{64}$' AND operation_kind IS NOT NULL AND operation_kind IN ('direct_create','correction'"
        + extra
        + "))",
    )


def upgrade() -> None:
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.create_unique_constraint("uq_user_accounts_id_person", "user_accounts", ["id", "person_id"])
    op.add_column(
        "assisted_person_profiles",
        sa.Column(
            "activation_requirement", sa.String(24), nullable=False, server_default="reserved_email"
        ),
    )
    op.create_check_constraint(
        "ck_assisted_profile_activation_requirement",
        "assisted_person_profiles",
        "activation_requirement IN ('reserved_email','reviewed_claim')",
    )
    op.alter_column("personnel_intake_revisions", "email_digest", nullable=True)
    _purpose(True)
    _operation(True)
    for statement in DDL:
        op.execute(statement)
    op.alter_column("personnel_credential_reservations", "request_id", nullable=True)
    op.add_column("personnel_credential_reservations", sa.Column("profile_claim_id", sa.UUID()))
    op.create_foreign_key(
        "fk_personnel_reservations_claim",
        "personnel_credential_reservations",
        "profile_claims",
        ["profile_claim_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_personnel_reservations_owner",
        "personnel_credential_reservations",
        "(request_id IS NOT NULL AND profile_claim_id IS NULL) OR (request_id IS NULL AND profile_claim_id IS NOT NULL AND kind='email' AND person_id IS NULL)",
    )
    op.create_index(
        "ix_personnel_reservations_claim",
        "personnel_credential_reservations",
        ["profile_claim_id", "ended_at"],
    )
    for table, columns in (
        (
            "profile_claims",
            "session_id,challenge_id,payload_ciphertext,payload_digest,submission_version,identity_digest,email_digest,verified_at,challenge_consumed_at,target_person_id,target_approval_id,private_match_reason,expires_at,created_at",
        ),
        (
            "profile_claim_sessions",
            "token_digest,challenge_id,payload_digest,identity_digest,email_digest,expires_at,created_at",
        ),
        ("profile_claim_bindings", "claim_id,person_id,email_digest,approval_id,created_at"),
    ):
        checks = " OR ".join(
            f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in columns.split(",")
        )
        if table == "profile_claim_sessions":
            checks += " OR (OLD.payload_ciphertext IS DISTINCT FROM NEW.payload_ciphertext AND NEW.payload_ciphertext IS NOT NULL) OR (OLD.verified_at IS NOT NULL AND NEW.verified_at IS DISTINCT FROM OLD.verified_at) OR (OLD.claim_id IS NOT NULL AND NEW.claim_id IS DISTINCT FROM OLD.claim_id) OR (OLD.state='verified' AND NEW.state <> 'verified')"
        if table == "profile_claim_bindings":
            checks += " OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)"
        op.execute(
            f"CREATE FUNCTION {table}_evidence_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {checks} THEN RAISE EXCEPTION 'profile claim evidence is immutable' USING ERRCODE='23514'; END IF; RETURN NEW; END $$"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_evidence_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_evidence_immutable()"
        )
    op.execute(
        "CREATE TRIGGER trg_profile_claim_decisions_immutable BEFORE UPDATE OR DELETE ON profile_claim_decisions FOR EACH ROW EXECUTE FUNCTION assisted_snapshot_immutable()"
    )
    op.execute(
        "CREATE TRIGGER trg_profile_claims_no_delete BEFORE DELETE ON profile_claims FOR EACH ROW EXECUTE FUNCTION assisted_snapshot_immutable()"
    )
    op.execute(
        "CREATE TRIGGER trg_profile_claim_bindings_no_delete BEFORE DELETE ON profile_claim_bindings FOR EACH ROW EXECUTE FUNCTION assisted_snapshot_immutable()"
    )
    op.execute("""CREATE FUNCTION assisted_profile_activation_monotonic() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF OLD.activation_requirement='reviewed_claim' AND NEW.activation_requirement<>'reviewed_claim' THEN
            RAISE EXCEPTION 'reviewed claim activation requirement cannot be removed' USING ERRCODE='23514';
        END IF; RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER trg_assisted_profile_activation_monotonic BEFORE UPDATE ON assisted_person_profiles FOR EACH ROW EXECUTE FUNCTION assisted_profile_activation_monotonic()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE user_accounts, assisted_person_profiles, assisted_approvals, personnel_intake_revisions, personnel_credential_reservations, registration_verification_challenges, "
            + ", ".join(TABLES)
            + " IN ACCESS EXCLUSIVE MODE"
        )
    )
    checks = [
        *(f"SELECT EXISTS(SELECT 1 FROM {table})" for table in TABLES),
        "SELECT EXISTS(SELECT 1 FROM personnel_intake_revisions WHERE email_digest IS NULL)",
        "SELECT EXISTS(SELECT 1 FROM personnel_credential_reservations WHERE request_id IS NULL OR profile_claim_id IS NOT NULL)",
        "SELECT EXISTS(SELECT 1 FROM assisted_person_profiles WHERE activation_requirement='reviewed_claim')",
        "SELECT EXISTS(SELECT 1 FROM assisted_approvals WHERE operation_kind IN ('claim_bind','claim_revoke'))",
        "SELECT EXISTS(SELECT 1 FROM registration_verification_challenges WHERE purpose='profile_claim')",
    ]
    if any(bind.scalar(sa.text(statement)) for statement in checks):
        raise RuntimeError(
            "profile claim downgrade requires no claim or null email business history"
        )
    op.execute("DROP TRIGGER trg_assisted_profile_activation_monotonic ON assisted_person_profiles")
    op.execute("DROP FUNCTION assisted_profile_activation_monotonic()")
    op.drop_constraint(
        "ck_personnel_reservations_owner", "personnel_credential_reservations", type_="check"
    )
    op.drop_index("ix_personnel_reservations_claim", table_name="personnel_credential_reservations")
    op.drop_column("personnel_credential_reservations", "profile_claim_id")
    op.alter_column("personnel_credential_reservations", "request_id", nullable=False)
    op.drop_constraint(
        "fk_profile_claim_session_claim", "profile_claim_sessions", type_="foreignkey"
    )
    for table in reversed(TABLES):
        op.drop_table(table)
    for table in ("profile_claims", "profile_claim_sessions", "profile_claim_bindings"):
        op.execute(f"DROP FUNCTION {table}_evidence_immutable()")
    op.alter_column("personnel_intake_revisions", "email_digest", nullable=False)
    op.drop_column("assisted_person_profiles", "activation_requirement")
    op.drop_constraint("uq_user_accounts_id_person", "user_accounts", type_="unique")
    _purpose(False)
    _operation(False)


DDL = (
    """CREATE TABLE profile_claim_sessions (
        id UUID PRIMARY KEY, token_digest VARCHAR(64) NOT NULL,
        challenge_id UUID NOT NULL REFERENCES registration_verification_challenges(id) ON DELETE RESTRICT,
        payload_ciphertext TEXT, payload_digest VARCHAR(64) NOT NULL,
        identity_digest VARCHAR(64) NOT NULL, email_digest VARCHAR(64) NOT NULL,
        state VARCHAR(16) NOT NULL, expires_at TIMESTAMPTZ NOT NULL, verified_at TIMESTAMPTZ,
        claim_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_profile_claim_session_token UNIQUE(token_digest),
        CONSTRAINT uq_profile_claim_session_challenge UNIQUE(challenge_id),
        CONSTRAINT uq_profile_claim_session_evidence UNIQUE(id,challenge_id,email_digest),
        CONSTRAINT ck_profile_claim_session_state CHECK(state IN ('requested','verified')),
        CONSTRAINT ck_profile_claim_session_digests CHECK(token_digest ~ '^[0-9a-f]{64}$' AND payload_digest ~ '^[0-9a-f]{64}$' AND identity_digest ~ '^[0-9a-f]{64}$' AND email_digest ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_profile_claim_session_expiry CHECK(expires_at > created_at),
        CONSTRAINT ck_profile_claim_session_verified CHECK((state='requested' AND verified_at IS NULL AND claim_id IS NULL) OR (state='verified' AND verified_at IS NOT NULL AND verified_at < expires_at))
    )""",
    "CREATE INDEX ix_profile_claim_session_expiry ON profile_claim_sessions(expires_at,id)",
    "CREATE INDEX ix_profile_claim_session_identity ON profile_claim_sessions(identity_digest,created_at,id)",
    "CREATE INDEX ix_profile_claim_session_email ON profile_claim_sessions(email_digest,created_at,id)",
    """CREATE TABLE profile_claims (
        id UUID PRIMARY KEY, session_id UUID NOT NULL, challenge_id UUID NOT NULL,
        payload_ciphertext TEXT NOT NULL, payload_digest VARCHAR(64) NOT NULL, submission_version INTEGER NOT NULL DEFAULT 1,
        identity_digest VARCHAR(64) NOT NULL, email_digest VARCHAR(64) NOT NULL,
        verified_at TIMESTAMPTZ NOT NULL, challenge_consumed_at TIMESTAMPTZ NOT NULL,
        target_person_id UUID REFERENCES people(id) ON DELETE RESTRICT, target_approval_id UUID,
        private_match_reason VARCHAR(100), status VARCHAR(16) NOT NULL, version INTEGER NOT NULL DEFAULT 1,
        expires_at TIMESTAMPTZ NOT NULL, reviewed_by_user_id UUID REFERENCES user_accounts(id) ON DELETE RESTRICT,
        reviewed_at TIMESTAMPTZ, public_reason VARCHAR(500), completed_account_id UUID, completed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_profile_claim_session UNIQUE(session_id), CONSTRAINT uq_profile_claim_challenge UNIQUE(challenge_id),
        CONSTRAINT uq_profile_claim_access_input UNIQUE(id,payload_digest,email_digest),
        CONSTRAINT uq_profile_claim_target_email UNIQUE(id,target_person_id,email_digest),
        CONSTRAINT fk_profile_claim_session_evidence FOREIGN KEY(session_id,challenge_id,email_digest) REFERENCES profile_claim_sessions(id,challenge_id,email_digest) ON DELETE RESTRICT,
        CONSTRAINT fk_profile_claim_target_approval FOREIGN KEY(target_approval_id,target_person_id) REFERENCES assisted_approvals(id,person_id) ON DELETE RESTRICT,
        CONSTRAINT fk_profile_claim_completed_account_person FOREIGN KEY(completed_account_id,target_person_id) REFERENCES user_accounts(id,person_id) ON DELETE RESTRICT,
        CONSTRAINT ck_profile_claim_target CHECK(target_approval_id IS NULL OR target_person_id IS NOT NULL),
        CONSTRAINT ck_profile_claim_status CHECK(status IN ('pending','rejected','expired','invalidated','approved','revoked','completed')),
        CONSTRAINT ck_profile_claim_versions CHECK(version>=1 AND submission_version>=1),
        CONSTRAINT ck_profile_claim_digests CHECK(payload_digest ~ '^[0-9a-f]{64}$' AND identity_digest ~ '^[0-9a-f]{64}$' AND email_digest ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_profile_claim_evidence_times CHECK(expires_at = verified_at + interval '7 days' AND challenge_consumed_at = verified_at),
        CONSTRAINT ck_profile_claim_completion CHECK((completed_account_id IS NULL AND completed_at IS NULL AND status<>'completed') OR (completed_account_id IS NOT NULL AND target_person_id IS NOT NULL AND completed_at IS NOT NULL AND status='completed'))
    )""",
    "ALTER TABLE profile_claim_sessions ADD CONSTRAINT fk_profile_claim_session_claim FOREIGN KEY(claim_id,payload_digest,email_digest) REFERENCES profile_claims(id,payload_digest,email_digest) ON DELETE RESTRICT",
    "CREATE INDEX ix_profile_claim_pending_expiry ON profile_claims(expires_at,id) WHERE status='pending'",
    "CREATE INDEX ix_profile_claim_status_page ON profile_claims(status,created_at,id)",
    "CREATE INDEX ix_profile_claim_email ON profile_claims(email_digest,status,id)",
    "CREATE INDEX ix_profile_claim_identity ON profile_claims(identity_digest,status,id)",
    "CREATE INDEX ix_profile_claim_target ON profile_claims(target_person_id,status,id)",
    """CREATE TABLE profile_claim_bindings (
        id UUID PRIMARY KEY, claim_id UUID NOT NULL, person_id UUID NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
        email_digest VARCHAR(64) NOT NULL, approval_id UUID NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), revoked_at TIMESTAMPTZ,
        CONSTRAINT uq_profile_claim_binding_claim UNIQUE(claim_id),
        CONSTRAINT fk_profile_claim_binding_claim FOREIGN KEY(claim_id,person_id,email_digest) REFERENCES profile_claims(id,target_person_id,email_digest) ON DELETE RESTRICT,
        CONSTRAINT fk_profile_claim_binding_approval FOREIGN KEY(approval_id,person_id) REFERENCES assisted_approvals(id,person_id) ON DELETE RESTRICT,
        CONSTRAINT ck_profile_claim_binding_digest CHECK(email_digest ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_profile_claim_binding_revoked CHECK(revoked_at IS NULL OR revoked_at>=created_at)
    )""",
    "CREATE UNIQUE INDEX uq_profile_claim_binding_active_person ON profile_claim_bindings(person_id) WHERE revoked_at IS NULL",
    "CREATE INDEX ix_profile_claim_binding_email ON profile_claim_bindings(email_digest,revoked_at)",
    """CREATE TABLE profile_claim_decisions (
        id UUID PRIMARY KEY, claim_id UUID NOT NULL REFERENCES profile_claims(id) ON DELETE RESTRICT,
        actor_user_id UUID NOT NULL REFERENCES user_accounts(id) ON DELETE RESTRICT,
        action VARCHAR(16) NOT NULL, idempotency_key UUID NOT NULL, request_fingerprint VARCHAR(64) NOT NULL,
        before_version INTEGER NOT NULL, after_version INTEGER NOT NULL,
        result_approval_id UUID REFERENCES assisted_approvals(id) ON DELETE RESTRICT,
        result_binding_id UUID REFERENCES profile_claim_bindings(id) ON DELETE RESTRICT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_profile_claim_decision_actor_key UNIQUE(actor_user_id,idempotency_key),
        CONSTRAINT ck_profile_claim_decision_action CHECK(action IN ('approve','reject','revoke')),
        CONSTRAINT ck_profile_claim_decision_fingerprint CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_profile_claim_decision_versions CHECK(before_version>=1 AND after_version=before_version+1)
    )""",
    "CREATE INDEX ix_profile_claim_decision_history ON profile_claim_decisions(claim_id,created_at,id)",
)
