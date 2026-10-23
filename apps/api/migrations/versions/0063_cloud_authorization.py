"""Persist fenced cloud authorization without changing existing backup targets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063_cloud_authorization"
down_revision = "0062_cloud_backups"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TIME = sa.DateTime(timezone=True)
LIVE_SESSION = "state NOT IN ('applied', 'expired', 'cancelled', 'failed')"


def _timestamps():
    return (
        sa.Column("created_at", TIME, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIME, nullable=False, server_default=sa.func.now()),
    )


def upgrade():
    op.create_table(
        "cloud_auth_applications",
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("secret_ciphertext", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("provider IN ('baidu', '115')", name="ck_cloud_auth_app_provider"),
        sa.CheckConstraint("revision >= 1", name="ck_cloud_auth_app_revision"),
    )
    op.create_index("uq_cloud_auth_app_id", "cloud_auth_applications", ["id"], unique=True)
    op.create_table(
        "cloud_auth_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "provider",
            sa.String(16),
            sa.ForeignKey("cloud_auth_applications.provider", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "owner_account_id",
            UUID,
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("owner_login_session_id", UUID, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("app_revision", sa.Integer(), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("expires_at", TIME, nullable=False),
        sa.Column("qr_expires_at", TIME),
        sa.Column("authorized_at", TIME),
        sa.Column("device_ciphertext", sa.Text()),
        sa.Column("token_ciphertext", sa.Text()),
        sa.Column("qr_ciphertext", sa.Text()),
        sa.Column("account_label", sa.String(255)),
        sa.Column("selected_directory", sa.String(1024)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("next_poll_at", TIME),
        sa.Column("candidate_id", UUID),
        *_timestamps(),
        sa.CheckConstraint("provider IN ('baidu', '115')", name="ck_cloud_auth_session_provider"),
        sa.CheckConstraint(
            "state IN ('creating', 'awaiting_confirmation', 'authorized', 'configuring', 'test_pending', 'ready', 'applying', 'applied', 'expired', 'cancelled', 'failed')",
            name="ck_cloud_auth_session_state",
        ),
        sa.CheckConstraint(
            "revision >= 1 AND app_revision >= 1 AND target_revision >= 0",
            name="ck_cloud_auth_session_revisions",
        ),
    )
    op.create_index(
        "uq_cloud_auth_session_live_provider",
        "cloud_auth_sessions",
        ["provider"],
        unique=True,
        postgresql_where=sa.text(LIVE_SESSION),
    )
    op.create_index(
        "ix_cloud_auth_session_owner",
        "cloud_auth_sessions",
        ["owner_account_id", "owner_login_session_id"],
    )
    op.create_index(
        "ix_cloud_auth_session_expiry",
        "cloud_auth_sessions",
        ["expires_at"],
        postgresql_where=sa.text(LIVE_SESSION),
    )
    op.create_table(
        "cloud_auth_candidates",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("cloud_auth_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("operation_id", UUID, nullable=False),
        sa.Column("connector_id", UUID, nullable=False),
        sa.Column("storage_id", sa.String(255)),
        sa.Column("webdav_user_id", sa.String(255)),
        sa.Column("mount_path", sa.String(1024), nullable=False),
        sa.Column("account_fingerprint_ciphertext", sa.Text()),
        sa.Column("username_ciphertext", sa.Text()),
        sa.Column("password_ciphertext", sa.Text()),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("selected_directory", sa.String(1024)),
        sa.Column("probe_operation_id", UUID),
        sa.Column("probe_sha256", sa.String(64)),
        sa.Column("probe_verified_at", TIME),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64)),
        *_timestamps(),
        sa.CheckConstraint("provider IN ('baidu', '115')", name="ck_cloud_auth_candidate_provider"),
        sa.CheckConstraint(
            "state IN ('preparing', 'configured', 'verified', 'active', 'cleanup_pending', 'cleaned', 'failed')",
            name="ck_cloud_auth_candidate_state",
        ),
        sa.CheckConstraint("config_revision >= 1", name="ck_cloud_auth_candidate_revision"),
        sa.CheckConstraint(
            "(probe_operation_id IS NULL AND probe_sha256 IS NULL AND probe_verified_at IS NULL) OR (probe_operation_id IS NOT NULL AND probe_sha256 IS NOT NULL AND probe_verified_at IS NOT NULL AND selected_directory IS NOT NULL)",
            name="ck_cloud_auth_candidate_probe",
        ),
    )
    op.create_index(
        "uq_cloud_auth_candidate_session", "cloud_auth_candidates", ["session_id"], unique=True
    )
    op.create_index(
        "uq_cloud_auth_candidate_operation", "cloud_auth_candidates", ["operation_id"], unique=True
    )
    op.create_table(
        "cloud_auth_operations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("cloud_auth_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("candidate_id", UUID),
        sa.Column(
            "owner_account_id",
            UUID,
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("owner_login_session_id", UUID, nullable=False),
        sa.Column("idempotency_key", UUID, nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("session_revision", sa.Integer(), nullable=False),
        sa.Column("app_revision", sa.Integer(), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("lease_id", UUID),
        sa.Column("lease_expires_at", TIME),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", TIME, nullable=False),
        sa.Column("cleanup_status", sa.String(16), nullable=False),
        sa.Column("payload_ciphertext", sa.Text()),
        sa.Column("result_ciphertext", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", TIME, nullable=False),
        sa.Column("completed_at", TIME),
        sa.CheckConstraint("provider IN ('baidu', '115')", name="ck_cloud_auth_operation_provider"),
        sa.CheckConstraint(
            "action IN ('create_qr', 'poll', 'prepare', 'list_directories', 'select_directory', 'probe', 'apply', 'cleanup', 'unbind')",
            name="ck_cloud_auth_operation_action",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_cloud_auth_operation_status",
        ),
        sa.CheckConstraint(
            "cleanup_status IN ('none', 'pending', 'running', 'succeeded', 'failed')",
            name="ck_cloud_auth_operation_cleanup",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND session_revision >= 1 AND app_revision >= 1 AND target_revision >= 0",
            name="ck_cloud_auth_operation_revisions",
        ),
        sa.CheckConstraint(
            "(lease_id IS NULL) = (lease_expires_at IS NULL)", name="ck_cloud_auth_operation_lease"
        ),
    )
    op.create_index(
        "uq_cloud_auth_operation_owner_key",
        "cloud_auth_operations",
        ["owner_account_id", "owner_login_session_id", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_cloud_auth_operation_claim",
        "cloud_auth_operations",
        ["status", "next_attempt_at", "lease_expires_at"],
    )
    op.create_index(
        "ix_cloud_auth_operation_cleanup",
        "cloud_auth_operations",
        ["cleanup_status", "next_attempt_at", "lease_expires_at"],
    )
    op.create_table(
        "cloud_backup_target_history",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_ciphertext", sa.Text(), nullable=False),
        sa.Column("candidate_id", UUID),
        sa.Column("account_label", sa.String(255)),
        sa.Column("last_backup_id", sa.String(64)),
        sa.Column("last_success_at", TIME),
        sa.Column("connection_status", sa.String(16), nullable=False),
        sa.Column("created_at", TIME, nullable=False),
        sa.CheckConstraint(
            "provider IN ('baidu', '115', 'quark')", name="ck_cloud_target_history_provider"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_cloud_target_history_revision"),
        sa.CheckConstraint(
            "connection_status IN ('untested', 'ok', 'failed')",
            name="ck_cloud_target_history_connection",
        ),
    )
    op.create_index(
        "uq_cloud_target_history_revision",
        "cloud_backup_target_history",
        ["provider", "revision"],
        unique=True,
    )
    op.create_table(
        "cloud_connector_state",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("registered", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", TIME),
        sa.Column("maintenance_operation_id", UUID),
        sa.Column("maintenance_lease_expires_at", TIME),
        sa.CheckConstraint(
            "(maintenance_operation_id IS NULL) = (maintenance_lease_expires_at IS NULL)",
            name="ck_cloud_connector_maintenance_lease",
        ),
    )
    op.add_column(
        "cloud_backup_targets",
        sa.Column("authorization_status", sa.String(32), nullable=False, server_default="manual"),
    )
    op.add_column("cloud_backup_targets", sa.Column("managed_candidate_id", UUID))
    op.add_column("cloud_backup_targets", sa.Column("switch_operation_id", UUID))
    op.create_check_constraint(
        "ck_cloud_target_authorization",
        "cloud_backup_targets",
        "authorization_status IN ('manual', 'valid', 'reauthorization_required')",
    )
    op.create_foreign_key(
        "fk_cloud_target_managed_candidate",
        "cloud_backup_targets",
        "cloud_auth_candidates",
        ["managed_candidate_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_cloud_target_switch_operation",
        "cloud_backup_targets",
        "cloud_auth_operations",
        ["switch_operation_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade():
    # A downgrade must never silently erase retained authorization credentials.
    connection = op.get_bind()
    for table in (
        "cloud_auth_applications",
        "cloud_auth_sessions",
        "cloud_auth_candidates",
        "cloud_auth_operations",
        "cloud_backup_target_history",
        "cloud_connector_state",
    ):
        if connection.scalar(sa.text(f'SELECT EXISTS (SELECT 1 FROM "{table}")')):
            raise RuntimeError("cloud authorization downgrade requires empty new tables")
    op.drop_constraint(
        "fk_cloud_target_switch_operation", "cloud_backup_targets", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_cloud_target_managed_candidate", "cloud_backup_targets", type_="foreignkey"
    )
    op.drop_constraint("ck_cloud_target_authorization", "cloud_backup_targets", type_="check")
    op.drop_column("cloud_backup_targets", "switch_operation_id")
    op.drop_column("cloud_backup_targets", "managed_candidate_id")
    op.drop_column("cloud_backup_targets", "authorization_status")
    for table in (
        "cloud_connector_state",
        "cloud_backup_target_history",
        "cloud_auth_operations",
        "cloud_auth_candidates",
        "cloud_auth_sessions",
        "cloud_auth_applications",
    ):
        op.drop_table(table)
