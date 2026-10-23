"""Persist quarantine cleanup retries and index bounded upload quotas."""

import sqlalchemy as sa
from alembic import op

revision = "0056_evaluation_upload_cleanup"
down_revision = "0055_manual_payment_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_quarantine_cleanup",
        sa.Column("storage_key", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("storage_key ~ '^\\.owned-[0-9a-f]{32}$'",
                           name="ck_evaluation_quarantine_cleanup_key"),
        sa.CheckConstraint("attempts >= 0", name="ck_evaluation_quarantine_cleanup_attempts"),
    )
    op.create_index("ix_evaluation_quarantine_cleanup_due", "evaluation_quarantine_cleanup",
                    ["next_attempt_at", "storage_key"])
    op.create_index("ix_evaluation_upload_account_open", "system_evaluation_upload_sessions",
                    ["created_by_user_id", "expires_at"],
                    postgresql_where=sa.text("status = 'open'"))


def downgrade() -> None:
    op.execute("LOCK TABLE evaluation_quarantine_cleanup IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM evaluation_quarantine_cleanup) THEN
                RAISE EXCEPTION 'pending quarantine cleanup prevents downgrade';
            END IF;
        END $$;
    """)
    op.drop_index("ix_evaluation_upload_account_open", "system_evaluation_upload_sessions")
    op.drop_table("evaluation_quarantine_cleanup")
