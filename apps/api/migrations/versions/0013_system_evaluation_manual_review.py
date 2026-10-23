"""Add manual system-evaluation drafts and review metadata transitions.

Revision ID: 0013_system_evaluation_manual_review
Revises: 0012_system_evaluation_processing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_system_evaluation_manual_review"
down_revision: str | Sequence[str] | None = "0012_system_evaluation_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_manual_draft_table() -> None:
    op.create_table(
        "system_evaluation_manual_drafts",
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("last_edited_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "confidences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "missing_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("grade", sa.String(length=24), nullable=True),
        sa.Column("advice", sa.Text(), nullable=True),
        sa.Column(
            "lock_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(fields) = 'object'",
            name="ck_system_evaluation_manual_drafts_fields",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(confidences) = 'object'",
            name="ck_system_evaluation_manual_drafts_confidences",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(warnings) = 'array'",
            name="ck_system_evaluation_manual_drafts_warnings",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(components) = 'array'",
            name="ck_system_evaluation_manual_drafts_components",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(missing_fields) = 'array'",
            name="ck_system_evaluation_manual_drafts_missing_fields",
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100",
            name="ck_system_evaluation_manual_drafts_score",
        ),
        sa.CheckConstraint(
            "lock_version >= 1",
            name="ck_system_evaluation_manual_drafts_lock_version",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["system_evaluation_tasks.id"],
            name="fk_system_evaluation_manual_drafts_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_edited_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_manual_drafts_editor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(
        "ix_system_evaluation_manual_drafts_updated",
        "system_evaluation_manual_drafts",
        ["updated_at", "task_id"],
    )


def _allow_only_review_metadata_transition() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_system_evaluation_result_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'system evaluation result versions cannot be deleted'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.review_decision IS DISTINCT FROM 'pending'
               OR NEW.review_decision NOT IN ('approved', 'rejected')
               OR OLD.reviewed_by_user_id IS NOT NULL
               OR OLD.reviewed_at IS NOT NULL
               OR OLD.rejection_reason IS NOT NULL
               OR OLD.published_at IS NOT NULL THEN
                RAISE EXCEPTION 'system evaluation result versions are immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.task_id IS DISTINCT FROM NEW.task_id
               OR OLD.version IS DISTINCT FROM NEW.version
               OR OLD.source IS DISTINCT FROM NEW.source
               OR OLD.fields IS DISTINCT FROM NEW.fields
               OR OLD.confidences IS DISTINCT FROM NEW.confidences
               OR OLD.warnings IS DISTINCT FROM NEW.warnings
               OR OLD.components IS DISTINCT FROM NEW.components
               OR OLD.missing_fields IS DISTINCT FROM NEW.missing_fields
               OR OLD.score IS DISTINCT FROM NEW.score
               OR OLD.grade IS DISTINCT FROM NEW.grade
               OR OLD.advice IS DISTINCT FROM NEW.advice
               OR OLD.adapter_id IS DISTINCT FROM NEW.adapter_id
               OR OLD.adapter_version IS DISTINCT FROM NEW.adapter_version
               OR OLD.scoring_rule_version IS DISTINCT FROM NEW.scoring_rule_version
               OR OLD.result_schema_version IS DISTINCT FROM NEW.result_schema_version
               OR OLD.created_by_user_id IS DISTINCT FROM NEW.created_by_user_id
               OR OLD.previous_version_id IS DISTINCT FROM NEW.previous_version_id
               OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
                RAISE EXCEPTION 'system evaluation result versions are immutable'
                    USING ERRCODE = '55000';
            END IF;

            NEW.updated_at := NEW.reviewed_at;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def _restore_result_immutability() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_system_evaluation_result_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'system evaluation result versions cannot be deleted'
                    USING ERRCODE = '55000';
            END IF;
            RAISE EXCEPTION 'system evaluation result versions are immutable'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )


def upgrade() -> None:
    _create_manual_draft_table()
    _allow_only_review_metadata_transition()


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE system_evaluation_manual_drafts, "
            "system_evaluation_result_versions "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    if bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM system_evaluation_manual_drafts)")
    ):
        raise RuntimeError(
            "cannot downgrade system evaluation review with manual drafts"
        )

    _restore_result_immutability()
    op.drop_index(
        "ix_system_evaluation_manual_drafts_updated",
        table_name="system_evaluation_manual_drafts",
    )
    op.drop_table("system_evaluation_manual_drafts")
