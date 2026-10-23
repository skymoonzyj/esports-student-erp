"""ERP-owned encrypted cloud targets and leased replication jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0062_cloud_backups"
down_revision = "0061_team_management"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cloud_backup_targets",
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("remote_path", sa.String(1024), nullable=False),
        sa.Column("username_ciphertext", sa.Text(), nullable=False),
        sa.Column("password_ciphertext", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("connection_status", sa.String(16), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_backup_id", sa.String(64)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "provider IN ('baidu', '115', 'quark')", name="ck_cloud_target_provider"
        ),
        sa.CheckConstraint(
            "connection_status IN ('untested', 'ok', 'failed')", name="ck_cloud_target_connection"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_cloud_target_revision"),
    )
    op.create_table(
        "cloud_backup_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider",
            sa.String(16),
            sa.ForeignKey("cloud_backup_targets.provider", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("backup_id", sa.String(64)),
        sa.Column("automatic_backup_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("lease_seen_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.CheckConstraint("kind IN ('test', 'copy')", name="ck_cloud_job_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_cloud_job_status"
        ),
    )
    op.create_index(
        "uq_cloud_job_live_target",
        "cloud_backup_jobs",
        ["provider"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "uq_cloud_job_auto_backup",
        "cloud_backup_jobs",
        ["provider", "revision", "automatic_backup_id"],
        unique=True,
    )


def downgrade():
    op.drop_table("cloud_backup_jobs")
    op.drop_table("cloud_backup_targets")
