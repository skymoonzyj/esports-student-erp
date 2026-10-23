from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class SystemEvaluationManualDraft(TimestampMixin, Base):
    __tablename__ = "system_evaluation_manual_drafts"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(fields) = 'object'",
            name="ck_system_evaluation_manual_drafts_fields",
        ),
        CheckConstraint(
            "jsonb_typeof(confidences) = 'object'",
            name="ck_system_evaluation_manual_drafts_confidences",
        ),
        CheckConstraint(
            "jsonb_typeof(warnings) = 'array'",
            name="ck_system_evaluation_manual_drafts_warnings",
        ),
        CheckConstraint(
            "jsonb_typeof(components) = 'array'",
            name="ck_system_evaluation_manual_drafts_components",
        ),
        CheckConstraint(
            "jsonb_typeof(missing_fields) = 'array'",
            name="ck_system_evaluation_manual_drafts_missing_fields",
        ),
        CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100",
            name="ck_system_evaluation_manual_drafts_score",
        ),
        CheckConstraint(
            "lock_version >= 1",
            name="ck_system_evaluation_manual_drafts_lock_version",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["system_evaluation_tasks.id"],
            name="fk_system_evaluation_manual_drafts_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_edited_by_user_id"],
            ["user_accounts.id"],
            name="fk_system_evaluation_manual_drafts_editor",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_system_evaluation_manual_drafts_updated",
            "updated_at",
            "task_id",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    last_edited_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    confidences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    components: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    missing_fields: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    grade: Mapped[str | None] = mapped_column(String(24))
    advice: Mapped[str | None] = mapped_column(Text)
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class SystemEvaluationResultVersion(TimestampMixin, Base):
    __tablename__ = "system_evaluation_result_versions"
    __table_args__ = (
        CheckConstraint(
            "version >= 1", name="ck_system_evaluation_results_version"
        ),
        CheckConstraint(
            "source IN ('automatic', 'manual', 'correction', 'revision')",
            name="ck_system_evaluation_results_source",
        ),
        CheckConstraint(
            "score BETWEEN 0 AND 100",
            name="ck_system_evaluation_results_score",
        ),
        CheckConstraint(
            "btrim(grade) <> '' AND btrim(advice) <> ''",
            name="ck_system_evaluation_results_display_text",
        ),
        CheckConstraint(
            "jsonb_typeof(fields) = 'object' "
            "AND jsonb_typeof(confidences) = 'object' "
            "AND jsonb_typeof(warnings) = 'array' "
            "AND jsonb_typeof(components) = 'array' "
            "AND jsonb_typeof(missing_fields) = 'array'",
            name="ck_system_evaluation_results_structured",
        ),
        CheckConstraint(
            "NOT jsonb_path_exists(warnings, "
            "'$[*] ? (@.type() != \"object\" || !exists (@.severity) || "
            "!(@.severity == \"info\" || @.severity == \"warning\" || "
            "@.severity == \"blocking\"))')",
            name="ck_system_evaluation_results_warning_severity",
        ),
        CheckConstraint(
            "review_decision IN ('pending', 'approved', 'rejected') "
            "AND ((review_decision = 'pending' AND reviewed_by_user_id IS NULL "
            "AND reviewed_at IS NULL AND rejection_reason IS NULL "
            "AND published_at IS NULL) OR "
            "(review_decision = 'approved' AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND rejection_reason IS NULL "
            "AND published_at IS NOT NULL) OR "
            "(review_decision = 'rejected' AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND rejection_reason IS NOT NULL "
            "AND btrim(rejection_reason) <> '' AND published_at IS NULL))",
            name="ck_system_evaluation_results_review",
        ),
        CheckConstraint(
            "(source = 'automatic' AND created_by_user_id IS NULL) OR "
            "(source <> 'automatic' AND created_by_user_id IS NOT NULL)",
            name="ck_system_evaluation_results_author",
        ),
        CheckConstraint(
            "(adapter_id IS NULL AND adapter_version IS NULL "
            "AND scoring_rule_version IS NULL) OR "
            "(adapter_id IS NOT NULL AND adapter_version IS NOT NULL "
            "AND scoring_rule_version IS NOT NULL)",
            name="ck_system_evaluation_results_adapter",
        ),
        CheckConstraint(
            "btrim(result_schema_version) <> ''",
            name="ck_system_evaluation_results_schema_version",
        ),
        ForeignKeyConstraint(
            ["previous_version_id", "task_id"],
            [
                "system_evaluation_result_versions.id",
                "system_evaluation_result_versions.task_id",
            ],
            name="fk_system_evaluation_results_previous",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            name="uq_system_evaluation_results_id_task",
        ),
        UniqueConstraint(
            "task_id", "version", name="uq_system_evaluation_results_task_version"
        ),
        Index(
            "ix_system_evaluation_results_task_created",
            "task_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("system_evaluation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    confidences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    components: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    missing_fields: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    grade: Mapped[str] = mapped_column(String(24), nullable=False)
    advice: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_id: Mapped[str | None] = mapped_column(String(80))
    adapter_version: Mapped[str | None] = mapped_column(String(40))
    scoring_rule_version: Mapped[str | None] = mapped_column(String(40))
    result_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    review_decision: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
    )
