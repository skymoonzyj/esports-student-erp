"""Isolated ReactionX ownership, pairing and immutable evidence foundation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0080_reactionx_foundation"
down_revision = "0079_talent_item_ranking_indexes"
branch_labels = None
depends_on = None


def _id(name="id", target=None, *, nullable=False, primary_key=False):
    arguments = [sa.ForeignKey(target, ondelete="RESTRICT")] if target else []
    return sa.Column(name, sa.UUID(), *arguments, nullable=nullable, primary_key=primary_key)


def _timestamps():
    return [
        sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.create_table(
        "reactionx_connectors",
        _id(primary_key=True),
        _id("actor_account_id", "user_accounts.id"),
        _id("browser_session_id", "auth_sessions.id"),
        sa.Column("credential_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("origin", sa.String(255), nullable=False),
        sa.Column("client_name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        _id("control_tab_id", nullable=True),
        sa.Column("last_sequence", sa.Integer(), nullable=False, server_default="0"),
        _id("instance_id", nullable=True),
        sa.Column("connector_version", sa.String(32)),
        sa.Column("protocol_version", sa.Integer()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("clock_seen_at", sa.DateTime(timezone=True)),
        sa.Column("devices", postgresql.JSONB(), nullable=False, server_default="[]"),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending','active','revoked')", name="ck_reactionx_connector_status"
        ),
        sa.CheckConstraint(
            "credential_digest ~ '^[0-9a-f]{64}$'", name="ck_reactionx_connector_digest"
        ),
    )
    op.create_index(
        "ix_reactionx_connectors_actor_account_id", "reactionx_connectors", ["actor_account_id"]
    )
    op.create_table(
        "reactionx_pairings",
        _id(primary_key=True),
        sa.Column("origin", sa.String(255), nullable=False),
        _id("actor_account_id", "user_accounts.id"),
        _id("browser_session_id", "auth_sessions.id"),
        sa.Column("challenge_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("verification_phrase", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _id("connector_id", "reactionx_connectors.id", nullable=True),
        sa.Column("browser_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("device_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        _id("browser_tab_id", nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("connector_id"),
    )
    op.create_index(
        "ix_reactionx_pairings_actor_account_id", "reactionx_pairings", ["actor_account_id"]
    )
    op.create_table(
        "reactionx_runs",
        _id(primary_key=True),
        _id("actor_account_id", "user_accounts.id"),
        _id("subject_person_id", "people.id"),
        _id("connector_id", "reactionx_connectors.id"),
        _id("browser_session_id", "auth_sessions.id"),
        _id("browser_tab_id"),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("lease_observed_at", sa.DateTime(timezone=True)),
        sa.Column("stop_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("upload_permit_digest", sa.String(64)),
        sa.Column("upload_permit_expires_at", sa.DateTime(timezone=True)),
        sa.Column("upload_authorization", postgresql.JSONB()),
        sa.Column("uncertainty_record", postgresql.JSONB()),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('prepared','ready','starting','running','stopping','unconfirmed',"
            "'awaiting_sync','completed','cancelled','expired','interrupted','uncertain_closed')",
            name="ck_reactionx_run_status",
        ),
        sa.CheckConstraint("last_sequence >= 0", name="ck_reactionx_run_sequence"),
    )
    op.create_index("ix_reactionx_runs_actor_account_id", "reactionx_runs", ["actor_account_id"])
    op.create_index("ix_reactionx_runs_subject_person_id", "reactionx_runs", ["subject_person_id"])
    op.create_index(
        "uq_reactionx_connector_active_run",
        "reactionx_runs",
        ["connector_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('prepared','ready','starting','running','stopping','unconfirmed')"
        ),
    )
    op.create_table(
        "reactionx_commands",
        _id(primary_key=True),
        _id("operation_id"),
        _id("run_id", "reactionx_runs.id", nullable=True),
        _id("connector_id", "reactionx_connectors.id"),
        _id("actor_account_id", "user_accounts.id"),
        _id("subject_person_id", "people.id", nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("ack_payload", postgresql.JSONB()),
        sa.Column("delivery_envelope", postgresql.JSONB()),
        _id("browser_tab_id", nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("operation_id"),
        sa.UniqueConstraint("connector_id", "sequence", name="uq_reactionx_command_sequence"),
    )
    op.create_index("ix_reactionx_commands_run_id", "reactionx_commands", ["run_id"])
    op.create_table(
        "reactionx_events",
        _id("run_id", "reactionx_runs.id", primary_key=True),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("event", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("seq > 0", name="ck_reactionx_event_seq"),
    )
    op.create_table(
        "reactionx_result_receipts",
        _id("run_id", "reactionx_runs.id", primary_key=True),
        _id("result_id"),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("envelope", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("ranking_reason", sa.String(40)),
        *[
            column
            for i in (1, 2)
            for column in (
                sa.Column(f"rank_n{i}", sa.BigInteger()),
                sa.Column(f"rank_d{i}", sa.Integer()),
                sa.Column(f"rank_sign{i}", sa.Integer()),
                sa.Column(
                    f"rank_key{i}",
                    sa.Numeric(30, 0),
                    sa.Computed(
                        f"rank_sign{i} * div(CAST(rank_n{i} AS numeric) * 100000000, CAST(rank_d{i} AS numeric))",
                        persisted=True,
                    ),
                ),
            )
        ],
        sa.Column("comparison_key", sa.String(128)),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("result_id"),
        sa.CheckConstraint(
            "(NOT eligible) OR (rank_n1 IS NOT NULL AND rank_d1 IS NOT NULL "
            "AND rank_sign1 IS NOT NULL AND rank_n2 IS NOT NULL AND rank_d2 IS NOT NULL "
            "AND rank_sign2 IS NOT NULL AND comparison_key IS NOT NULL)",
            name="ck_reactionx_rank_complete",
        ),
        *[
            sa.CheckConstraint(
                f"rank_n{i} BETWEEN 0 AND 9007199254740991 AND "
                f"rank_d{i} BETWEEN 1 AND 10000 AND rank_sign{i} IN (-1,1)",
                name=f"ck_reactionx_rank_domain_{i}",
            )
            for i in (1, 2)
        ],
    )
    op.create_index(
        "ix_reactionx_result_receipts_comparison_key",
        "reactionx_result_receipts",
        ["comparison_key"],
    )
    op.create_index(
        "ix_reactionx_rank_group",
        "reactionx_result_receipts",
        ["comparison_key", "rank_key1", "rank_key2", "saved_at", "result_id"],
        postgresql_where=sa.text("eligible"),
    )
    op.create_index(
        "ix_reactionx_result_page", "reactionx_result_receipts", ["saved_at", "result_id"]
    )
    op.execute("""CREATE FUNCTION reactionx_evidence_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'ReactionX evidence is immutable'; END; $$""")
    for table in ("reactionx_events", "reactionx_result_receipts"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reactionx_evidence_immutable()"
        )
    op.execute(
        "INSERT INTO permissions (id,key,name,sensitive) VALUES ('20000000-0000-4000-8000-000000000092','reactionx.manage','管理 ReactionX 连接器',true),('20000000-0000-4000-8000-000000000093','reactionx.conduct','ReactionX 现场代测',true)"
    )
    op.execute(
        "INSERT INTO role_permissions (role_id,permission_id,scope_kind) SELECT r.id,p.id,'global' FROM roles r CROSS JOIN permissions p WHERE r.key='super_admin' AND p.key IN ('reactionx.manage','reactionx.conduct')"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM user_permission_overrides WHERE permission_id IN (SELECT id FROM permissions WHERE key IN ('reactionx.manage','reactionx.conduct'))"
    )
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE key IN ('reactionx.manage','reactionx.conduct'))"
    )
    op.execute("DELETE FROM permissions WHERE key IN ('reactionx.manage','reactionx.conduct')")
    for table in (
        "reactionx_result_receipts",
        "reactionx_events",
        "reactionx_commands",
        "reactionx_runs",
        "reactionx_pairings",
        "reactionx_connectors",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION reactionx_evidence_immutable()")
