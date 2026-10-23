"""Recoverable assisted personnel records and immutable account origin."""

import sqlalchemy as sa
from alembic import op

revision = "0066_assisted_personnel"
down_revision = "0065_invited_housing_visibility"
branch_labels = None
depends_on = None

TABLES = (
    "personnel_intake_requests",
    "personnel_intake_revisions",
    "personnel_credential_reservations",
    "assisted_approvals",
    "assisted_person_profiles",
    "coach_person_responsibilities",
    "assisted_activation_sessions",
)


def upgrade() -> None:
    # Retire only the legacy role grant. Individual overrides and other roles
    # remain intact; live housing access enforces the owner-only role boundary.
    op.execute("""
        DELETE FROM role_permissions rp USING roles r, permissions p
        WHERE rp.role_id = r.id AND rp.permission_id = p.id
          AND r.key = 'coach' AND p.key = 'housing.read' AND rp.scope_kind = 'global'
    """)
    # 0017 backfills accounts when upgrading a populated older schema in this
    # same transaction. 0026 drops those named triggers, but queued events still
    # block ALTER TABLE until flushed. ALL also drains events for dropped names;
    # it checks outstanding constraints rather than disabling any invariant.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    for statement in DDL:
        op.execute(statement)
    op.add_column("guardian_relationships", sa.Column(
        "requires_verification", sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    for suffix, key, label in (
        (87, "personnel_intake.submit", "提交受控人员建档"),
        (88, "personnel_intake.review", "审核受控人员建档"),
        (89, "personnel_intake.housing.read", "查看负责人员住宿摘要"),
    ):
        op.execute(
            sa.text(
                "INSERT INTO permissions(id,key,name,sensitive) VALUES (CAST(:id AS uuid),:key,:name,true) ON CONFLICT (key) DO NOTHING"
            ).bindparams(id=f"20000000-0000-4000-8000-{suffix:012d}", key=key, name=label)
        )
    op.execute("""
        INSERT INTO role_permissions(role_id,permission_id,scope_kind)
        SELECT r.id,p.id,'global' FROM roles r CROSS JOIN permissions p
        WHERE p.key IN ('personnel_intake.submit','personnel_intake.review','personnel_intake.housing.read')
          AND (r.key IN ('admin','super_admin') OR
               (r.key IN ('coach','team_manager','administration') AND p.key <> 'personnel_intake.review'))
        ON CONFLICT DO NOTHING
    """)
    op.add_column(
        "user_accounts",
        sa.Column("account_access_origin", sa.String(24), nullable=False, server_default="legacy"),
    )
    op.add_column("user_accounts", sa.Column("assisted_approval_id", sa.UUID()))
    op.create_check_constraint(
        "ck_user_accounts_access_origin",
        "user_accounts",
        "(account_access_origin = 'legacy' AND assisted_approval_id IS NULL) OR "
        "(account_access_origin = 'assisted_activation' AND assisted_approval_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_user_accounts_assisted_approval_person",
        "user_accounts",
        "assisted_approvals",
        ["assisted_approval_id", "person_id"],
        ["id", "person_id"],
        ondelete="RESTRICT",
    )
    op.execute("""
        CREATE FUNCTION assisted_account_origin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.account_access_origin IS DISTINCT FROM OLD.account_access_origin
               OR NEW.assisted_approval_id IS DISTINCT FROM OLD.assisted_approval_id THEN
                RAISE EXCEPTION 'account access origin is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_assisted_account_origin_immutable BEFORE UPDATE ON user_accounts "
        "FOR EACH ROW EXECUTE FUNCTION assisted_account_origin_immutable()"
    )
    op.execute("""
        CREATE FUNCTION assisted_snapshot_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'assisted approval and revision snapshots are immutable'
                USING ERRCODE = '23514';
        END $$
    """)
    for table in ("personnel_intake_revisions", "assisted_approvals"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION assisted_snapshot_immutable()"
        )
    op.execute("""
        CREATE FUNCTION assisted_approval_guardian_references() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE matched integer;
        BEGIN
            PERFORM id FROM guardian_relationships
                WHERE id = ANY(NEW.guardian_relationship_ids) AND student_person_id = NEW.person_id
                FOR KEY SHARE;
            GET DIAGNOSTICS matched = ROW_COUNT;
            IF matched <> cardinality(NEW.guardian_relationship_ids) THEN
                RAISE EXCEPTION 'approval guardian references must belong to approved person'
                    USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_assisted_approval_guardian_references BEFORE INSERT ON assisted_approvals "
        "FOR EACH ROW EXECUTE FUNCTION assisted_approval_guardian_references()"
    )
    op.drop_constraint(
        "ck_registration_verification_challenges_purpose",
        "registration_verification_challenges",
        type_="check",
    )
    op.create_check_constraint(
        "ck_registration_verification_challenges_purpose",
        "registration_verification_challenges",
        "purpose IN ('registration', 'password_reset', "
        "'guardian_invitation', 'add_login_identifier', 'assisted_activation')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Lock references first, then all new data, so no insertion can race the guard.
    bind.execute(
        sa.text(
            "LOCK TABLE user_accounts, people, guardian_relationships, "
            "registration_verification_challenges IN ACCESS EXCLUSIVE MODE"
        )
    )
    bind.execute(sa.text("LOCK TABLE " + ", ".join(TABLES) + " IN ACCESS EXCLUSIVE MODE"))
    if any(bind.scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {table})")) for table in TABLES):
        raise RuntimeError("assisted personnel downgrade requires no assisted business history")
    if bind.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM guardian_relationships WHERE requires_verification)")):
        raise RuntimeError("assisted personnel downgrade requires no unverified guardian relationships")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM user_accounts WHERE "
            "account_access_origin <> 'legacy' OR assisted_approval_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("assisted personnel downgrade requires no assisted accounts")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM registration_verification_challenges "
            "WHERE purpose='assisted_activation')"
        )
    ):
        raise RuntimeError("assisted personnel downgrade requires no activation challenges")
    op.drop_constraint(
        "ck_registration_verification_challenges_purpose",
        "registration_verification_challenges",
        type_="check",
    )
    op.create_check_constraint(
        "ck_registration_verification_challenges_purpose",
        "registration_verification_challenges",
        "purpose IN ('registration', 'password_reset', "
        "'guardian_invitation', 'add_login_identifier')",
    )
    op.execute("DROP TRIGGER trg_assisted_account_origin_immutable ON user_accounts")
    op.execute("DROP FUNCTION assisted_account_origin_immutable()")
    op.drop_constraint(
        "fk_user_accounts_assisted_approval_person", "user_accounts", type_="foreignkey"
    )
    op.drop_constraint("ck_user_accounts_access_origin", "user_accounts", type_="check")
    op.drop_column("user_accounts", "assisted_approval_id")
    op.drop_column("user_accounts", "account_access_origin")
    op.drop_column("guardian_relationships", "requires_verification")
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE key LIKE 'personnel_intake.%')"
    )
    op.execute(
        "DELETE FROM user_permission_overrides WHERE permission_id IN (SELECT id FROM permissions WHERE key LIKE 'personnel_intake.%')"
    )
    op.execute("DELETE FROM permissions WHERE key LIKE 'personnel_intake.%'")
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION assisted_snapshot_immutable()")
    op.execute("DROP FUNCTION assisted_approval_guardian_references()")


DDL = (
    """
    CREATE TABLE personnel_intake_requests (
        id UUID NOT NULL,
        submitted_by_user_id UUID NOT NULL,
        version INTEGER NOT NULL,
        state VARCHAR(16) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        person_id UUID,
        review_reason VARCHAR(500),
        target_coach_person_ids UUID[] DEFAULT '{}' NOT NULL,
        target_team_id UUID REFERENCES professional_teams(id) ON DELETE RESTRICT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_personnel_intake_requests_state CHECK (state IN ('pending', 'returned', 'approved', 'withdrawn')),
        CONSTRAINT ck_personnel_intake_requests_version CHECK (version >= 1),
        CONSTRAINT ck_personnel_intake_requests_kind CHECK (kind IN ('student', 'player', 'both')),
        FOREIGN KEY(submitted_by_user_id) REFERENCES user_accounts (id) ON DELETE RESTRICT,
        FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_personnel_intake_requests_state_submitter ON personnel_intake_requests (state, submitted_by_user_id, id)
    """,
    "CREATE INDEX ix_personnel_intake_requests_target_team ON personnel_intake_requests(target_team_id,state,id)",
    """
    CREATE INDEX ix_personnel_intake_requests_submitter ON personnel_intake_requests (submitted_by_user_id, created_at, id)
    """,
    """
    CREATE TABLE personnel_intake_revisions (
        id UUID NOT NULL,
        request_id UUID NOT NULL,
        version INTEGER NOT NULL,
        payload_ciphertext TEXT NOT NULL,
        payload_digest VARCHAR(64) NOT NULL,
        identity_digest VARCHAR(64) NOT NULL,
        email_digest VARCHAR(64) NOT NULL,
        created_by_user_id UUID NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_personnel_intake_revision_version UNIQUE (request_id, version),
        CONSTRAINT ck_personnel_intake_revisions_version CHECK (version >= 1),
        CONSTRAINT ck_personnel_intake_revisions_digest CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
        FOREIGN KEY(request_id) REFERENCES personnel_intake_requests (id) ON DELETE RESTRICT,
        FOREIGN KEY(created_by_user_id) REFERENCES user_accounts (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_personnel_intake_revisions_email ON personnel_intake_revisions (email_digest, request_id, version)
    """,
    """
    CREATE INDEX ix_personnel_intake_revisions_identity ON personnel_intake_revisions (identity_digest, request_id)
    """,
    """
    CREATE TABLE personnel_credential_reservations (
        id UUID NOT NULL,
        kind VARCHAR(16) NOT NULL,
        digest VARCHAR(64) NOT NULL,
        request_id UUID NOT NULL,
        person_id UUID,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        ended_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        CONSTRAINT ck_personnel_reservations_kind CHECK (kind IN ('identity', 'email')),
        CONSTRAINT ck_personnel_reservations_digest CHECK (digest ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_personnel_reservations_end CHECK (ended_at IS NULL OR ended_at >= created_at),
        FOREIGN KEY(request_id) REFERENCES personnel_intake_requests (id) ON DELETE RESTRICT,
        FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_personnel_reservations_person ON personnel_credential_reservations (person_id, ended_at)
    """,
    """
    CREATE INDEX ix_personnel_reservations_request ON personnel_credential_reservations (request_id, ended_at)
    """,
    """
    CREATE UNIQUE INDEX uq_personnel_reservations_active ON personnel_credential_reservations (kind, digest) WHERE ended_at IS NULL
    """,
    """
    CREATE TABLE assisted_approvals (
        id UUID NOT NULL,
        request_id UUID NOT NULL,
        revision_version INTEGER NOT NULL,
        person_id UUID NOT NULL,
        approved_by_user_id UUID NOT NULL,
        approved_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        snapshot_ciphertext TEXT NOT NULL,
        snapshot_digest VARCHAR(64) NOT NULL,
        guardian_relationship_version INTEGER NOT NULL,
        guardian_relationship_ids UUID[] DEFAULT '{}' NOT NULL,
        guardian_snapshot_ciphertext TEXT NOT NULL,
        guardian_snapshot_digest VARCHAR(64) NOT NULL,
        supersedes_approval_id UUID,
        operation_key UUID,
        operation_digest VARCHAR(64),
        operation_kind VARCHAR(24),
        PRIMARY KEY (id),
        CONSTRAINT fk_assisted_approval_revision FOREIGN KEY(request_id, revision_version) REFERENCES personnel_intake_revisions (request_id, version) ON DELETE RESTRICT,
        CONSTRAINT fk_assisted_approval_supersedes_person FOREIGN KEY(supersedes_approval_id, person_id) REFERENCES assisted_approvals (id, person_id) ON DELETE RESTRICT,
        CONSTRAINT uq_assisted_approval_person UNIQUE (id, person_id),
        CONSTRAINT uq_assisted_approval_person_version UNIQUE (id, person_id, revision_version),
        CONSTRAINT uq_assisted_approval_revision UNIQUE (request_id, revision_version),
        CONSTRAINT uq_assisted_approval_operation UNIQUE (approved_by_user_id, operation_key),
        CONSTRAINT ck_assisted_approval_operation CHECK (
            (operation_key IS NULL AND operation_digest IS NULL AND operation_kind IS NULL) OR
            (operation_key IS NOT NULL AND operation_digest IS NOT NULL AND
             operation_digest ~ '^[0-9a-f]{64}$' AND operation_kind IS NOT NULL AND
             operation_kind IN ('direct_create', 'correction'))),
        CONSTRAINT ck_assisted_approval_versions CHECK (revision_version >= 1 AND guardian_relationship_version >= 1),
        CONSTRAINT ck_assisted_approval_digests CHECK (snapshot_digest ~ '^[0-9a-f]{64}$' AND guardian_snapshot_digest ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_assisted_approval_not_self CHECK (supersedes_approval_id IS NULL OR supersedes_approval_id <> id),
        FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE RESTRICT,
        FOREIGN KEY(approved_by_user_id) REFERENCES user_accounts (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_assisted_approvals_person ON assisted_approvals (person_id, approved_at, id)
    """,
    """
    CREATE UNIQUE INDEX uq_assisted_approval_initial ON assisted_approvals (request_id) WHERE supersedes_approval_id IS NULL
    """,
    """
    CREATE UNIQUE INDEX uq_assisted_approval_successor ON assisted_approvals (supersedes_approval_id) WHERE supersedes_approval_id IS NOT NULL
    """,
    """
    CREATE TABLE assisted_person_profiles (
        person_id UUID NOT NULL,
        current_approval_id UUID NOT NULL,
        version INTEGER DEFAULT 1 NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (person_id),
        CONSTRAINT fk_assisted_profile_approval_person FOREIGN KEY(current_approval_id, person_id) REFERENCES assisted_approvals (id, person_id) ON DELETE RESTRICT,
        CONSTRAINT ck_assisted_profile_version CHECK (version >= 1),
        FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE coach_person_responsibilities (
        id UUID NOT NULL,
        coach_person_id UUID NOT NULL,
        person_id UUID NOT NULL,
        created_by_user_id UUID NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        ended_at TIMESTAMP WITH TIME ZONE,
        ended_by_user_id UUID,
        PRIMARY KEY (id),
        CONSTRAINT ck_coach_person_distinct CHECK (coach_person_id <> person_id),
        CONSTRAINT ck_coach_person_end CHECK ((ended_at IS NULL AND ended_by_user_id IS NULL) OR (ended_at IS NOT NULL AND ended_by_user_id IS NOT NULL AND ended_at >= created_at)),
        FOREIGN KEY(coach_person_id) REFERENCES people (id) ON DELETE RESTRICT,
        FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE RESTRICT,
        FOREIGN KEY(created_by_user_id) REFERENCES user_accounts (id) ON DELETE RESTRICT,
        FOREIGN KEY(ended_by_user_id) REFERENCES user_accounts (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_coach_person_active_person ON coach_person_responsibilities (person_id, coach_person_id) WHERE ended_at IS NULL
    """,
    """
    CREATE UNIQUE INDEX uq_coach_person_active ON coach_person_responsibilities (coach_person_id, person_id) WHERE ended_at IS NULL
    """,
    """
    CREATE TABLE assisted_activation_sessions (
        id UUID NOT NULL,
        token_digest VARCHAR(64) NOT NULL,
        challenge_id UUID NOT NULL,
        person_id UUID NOT NULL,
        approval_id UUID NOT NULL,
        revision_version INTEGER NOT NULL,
        email_digest VARCHAR(64) NOT NULL,
        state VARCHAR(16) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        verified_at TIMESTAMP WITH TIME ZONE,
        completed_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_assisted_activation_token UNIQUE (token_digest),
        CONSTRAINT uq_assisted_activation_challenge UNIQUE (challenge_id),
        CONSTRAINT fk_assisted_activation_approval FOREIGN KEY(approval_id, person_id, revision_version) REFERENCES assisted_approvals (id, person_id, revision_version) ON DELETE RESTRICT,
        CONSTRAINT ck_assisted_activation_state CHECK (state IN ('requested', 'verified', 'completed')),
        CONSTRAINT ck_assisted_activation_digests CHECK (token_digest ~ '^[0-9a-f]{64}$' AND email_digest ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_assisted_activation_expiry CHECK (expires_at > created_at),
        CONSTRAINT ck_assisted_activation_times CHECK ((state='requested' AND verified_at IS NULL AND completed_at IS NULL) OR (state='verified' AND verified_at IS NOT NULL AND completed_at IS NULL) OR (state='completed' AND verified_at IS NOT NULL AND completed_at IS NOT NULL AND completed_at >= verified_at)),
        FOREIGN KEY(challenge_id) REFERENCES registration_verification_challenges (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_assisted_activation_email ON assisted_activation_sessions (email_digest, created_at, id)
    """,
    """
    CREATE INDEX ix_assisted_activation_expiry ON assisted_activation_sessions (state, expires_at, id)
    """,
    """
    CREATE INDEX ix_assisted_activation_person ON assisted_activation_sessions (person_id, created_at, id)
    """,
)
