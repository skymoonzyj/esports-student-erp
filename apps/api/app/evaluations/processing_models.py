from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class SystemEvaluationAttempt(Base):
    __tablename__ = "system_evaluation_attempts"
    __table_args__ = (
        CheckConstraint(
            "attempt BETWEEN 1 AND 3",
            name="ck_system_evaluation_attempts_number",
        ),
        CheckConstraint(
            "task_lock_version >= 1",
            name="ck_system_evaluation_attempts_task_version",
        ),
        CheckConstraint(
            "status IN ('active', 'succeeded', 'failed', 'expired')",
            name="ck_system_evaluation_attempts_status",
        ),
        CheckConstraint(
            "lease_token_digest ~ '^[0-9a-f]{64}$' "
            "AND lease_expires_at > claimed_at",
            name="ck_system_evaluation_attempts_lease",
        ),
        CheckConstraint(
            "result_idempotency_digest IS NULL OR "
            "result_idempotency_digest ~ '^[0-9a-f]{64}$'",
            name="ck_system_evaluation_attempts_result_idempotency",
        ),
        CheckConstraint(
            "(status = 'active' AND completed_at IS NULL) OR "
            "(status <> 'active' AND completed_at IS NOT NULL)",
            name="ck_system_evaluation_attempts_completion",
        ),
        CheckConstraint(
            "error_class IS NULL OR error_class ~ '^[a-z][a-z0-9_]{0,79}$'",
            name="ck_system_evaluation_attempts_error_class",
        ),
        CheckConstraint(
            "btrim(adapter_id) <> '' AND btrim(adapter_version) <> '' "
            "AND btrim(scoring_rule_version) <> '' "
            "AND btrim(result_schema_version) <> '' "
            "AND btrim(worker_identity) <> ''",
            name="ck_system_evaluation_attempts_versions",
        ),
        UniqueConstraint(
            "task_id", "attempt", name="uq_system_evaluation_attempts_task_attempt"
        ),
        Index("ix_system_evaluation_attempts_active_lease", "status", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("system_evaluation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    task_lock_version: Mapped[int] = mapped_column(Integer, nullable=False)
    adapter_id: Mapped[str] = mapped_column(String(80), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(40), nullable=False)
    scoring_rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    result_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    worker_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    lease_token_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_class: Mapped[str | None] = mapped_column(String(80))
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    safe_error_summary: Mapped[str | None] = mapped_column(String(300))
    result_idempotency_digest: Mapped[str | None] = mapped_column(CHAR(64))
    trace_id: Mapped[str | None] = mapped_column(String(128))


class EvaluationAdapterCertification(Base):
    __tablename__ = "evaluation_adapter_certifications"
    __table_args__ = (
        CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_adapter_certifications_manifest",
        ),
        CheckConstraint(
            "btrim(adapter_id) <> '' AND btrim(adapter_version) <> '' "
            "AND btrim(scoring_rule_version) <> '' "
            "AND btrim(result_schema_version) <> ''",
            name="ck_evaluation_adapter_certifications_versions",
        ),
        UniqueConstraint(
            "project_id",
            "adapter_id",
            "adapter_version",
            "scoring_rule_version",
            "result_schema_version",
            name="uq_evaluation_adapter_certifications_version",
        ),
        Index(
            "ix_evaluation_adapter_certifications_project_time",
            "project_id",
            "certified_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    adapter_id: Mapped[str] = mapped_column(String(80), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(40), nullable=False)
    scoring_rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    result_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    certified_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    certified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
