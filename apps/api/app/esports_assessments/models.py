from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base

_SUITE_CHECK = "suite_id IN ('pc-fps', 'mobile-fps', 'pc-moba', 'mobile-moba')"
_RUN_STATE_CHECK = (
    "state IN ('running', 'awaiting_submission', 'interrupted', 'expired', 'submitted')"
)
_DIGEST_CHECK = "~ '^[0-9a-f]{64}$'"


class AssessmentSession(Base):
    __tablename__ = "esports_assessment_sessions"
    __table_args__ = (
        UniqueConstraint(
            "created_by_user_id",
            "creation_key",
            name="uq_ea_session_creator_creation",
        ),
        UniqueConstraint(
            "created_by_user_id",
            "abandon_key",
            name="uq_ea_session_creator_abandon",
        ),
        Index(
            "uq_ea_open_person_suite",
            "person_id",
            "suite_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
        ),
        Index(
            "ix_ea_session_person_suite_status_created",
            "person_id",
            "suite_id",
            "status",
            "created_at",
            "id",
        ),
        CheckConstraint(_SUITE_CHECK, name="ck_ea_session_suite"),
        CheckConstraint(
            "(status = 'in_progress' AND completed_at IS NULL AND abandoned_at IS NULL) "
            "OR (status = 'completed' AND completed_at IS NOT NULL "
            "AND abandoned_at IS NULL) "
            "OR (status = 'abandoned' AND completed_at IS NULL "
            "AND abandoned_at IS NOT NULL)",
            name="ck_ea_session_state_times",
        ),
        CheckConstraint(
            f"creation_request_digest {_DIGEST_CHECK}",
            name="ck_ea_session_creation_digest",
        ),
        CheckConstraint(
            "(abandon_key IS NULL AND abandon_request_digest IS NULL) OR "
            f"(abandon_key IS NOT NULL AND abandon_request_digest {_DIGEST_CHECK})",
            name="ck_ea_session_abandon_pair",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    suite_id: Mapped[str] = mapped_column(String(32), nullable=False)
    creation_key: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    creation_request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    abandon_key: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    abandon_request_digest: Mapped[str | None] = mapped_column(String(64))
    catalog_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    scoring_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    report_rules_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress", server_default="in_progress"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssessmentAttempt(Base):
    __tablename__ = "esports_assessment_attempts"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "session_id",
            "test_id",
            name="uq_ea_attempt_identity",
        ),
        UniqueConstraint(
            "created_by_user_id",
            "creation_key",
            name="uq_ea_attempt_creator_creation",
        ),
        UniqueConstraint(
            "created_by_user_id",
            "interrupt_key",
            name="uq_ea_attempt_creator_interrupt",
        ),
        UniqueConstraint(
            "session_id",
            "test_id",
            "sequence_no",
            name="uq_ea_attempt_sequence",
        ),
        Index(
            "uq_ea_attempt_submittable_session_test",
            "session_id",
            "test_id",
            unique=True,
            postgresql_where=text("state IN ('running', 'awaiting_submission')"),
        ),
        Index(
            "ix_ea_attempt_creator_state_deadline",
            "created_by_user_id",
            "state",
            "run_until",
            "id",
        ),
        CheckConstraint(_RUN_STATE_CHECK, name="ck_ea_attempt_state"),
        CheckConstraint(
            "(state = 'submitted' AND submitted_at IS NOT NULL "
            "AND interrupted_at IS NULL) "
            "OR (state = 'interrupted' AND submitted_at IS NULL "
            "AND interrupted_at IS NOT NULL) "
            "OR (state IN ('running', 'awaiting_submission', 'expired') "
            "AND submitted_at IS NULL AND interrupted_at IS NULL)",
            name="ck_ea_attempt_state_times",
        ),
        CheckConstraint(
            "run_until > created_at AND submit_until > run_until",
            name="ck_ea_attempt_deadlines",
        ),
        CheckConstraint("seed BETWEEN 0 AND 4294967295", name="ck_ea_attempt_seed"),
        CheckConstraint(
            f"creation_request_digest {_DIGEST_CHECK}",
            name="ck_ea_attempt_creation_digest",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("esports_assessment_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    test_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    environment_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="running", server_default="running"
    )
    creation_key: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    creation_request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    interrupt_key: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    run_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submit_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssessmentResult(Base):
    __tablename__ = "esports_assessment_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attempt_id", "session_id", "test_id"],
            [
                "esports_assessment_attempts.id",
                "esports_assessment_attempts.session_id",
                "esports_assessment_attempts.test_id",
            ],
            name="fk_ea_result_attempt_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("session_id", "test_id", name="uq_ea_result_session_test"),
        UniqueConstraint("attempt_id", name="uq_ea_result_attempt"),
        UniqueConstraint(
            "created_by_user_id",
            "submission_key",
            name="uq_ea_result_creator_submission",
        ),
        Index(
            "ix_ea_result_comparison_created",
            "test_id",
            "comparison_key",
            "created_at",
            "id",
        ),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_ea_result_score"),
        CheckConstraint(
            "grade IN ('low', 'medium', 'high', 'exceptional')",
            name="ck_ea_result_grade",
        ),
        CheckConstraint(
            f"submission_request_digest {_DIGEST_CHECK}",
            name="ck_ea_result_submission_digest",
        ),
        CheckConstraint(
            f"comparison_key {_DIGEST_CHECK}",
            name="ck_ea_result_comparison_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    attempt_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    session_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    test_id: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    submission_key: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    submission_request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    grade: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    comparison_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssessmentReport(Base):
    __tablename__ = "esports_assessment_reports"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_ea_report_session"),
        Index("ix_ea_report_created", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("esports_assessment_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssessmentPracticeRun(Base):
    __tablename__ = "esports_assessment_practice_runs"
    __table_args__ = (
        UniqueConstraint(
            "created_by_user_id",
            "creation_key",
            name="uq_ea_practice_creator_creation",
        ),
        UniqueConstraint(
            "created_by_user_id",
            "submission_key",
            name="uq_ea_practice_creator_submission",
        ),
        UniqueConstraint(
            "created_by_user_id",
            "interrupt_key",
            name="uq_ea_practice_creator_interrupt",
        ),
        Index(
            "ix_ea_practice_person_suite_state_created",
            "person_id",
            "suite_id",
            "state",
            "created_at",
            "id",
        ),
        Index(
            "ix_ea_practice_creator_state_deadline",
            "created_by_user_id",
            "state",
            "run_until",
            "id",
        ),
        Index(
            "ix_ea_practice_comparison_created",
            "person_id",
            "suite_id",
            "test_id",
            "comparison_key",
            "created_at",
            "id",
        ),
        CheckConstraint(_SUITE_CHECK, name="ck_ea_practice_suite"),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_ea_practice_difficulty"),
        CheckConstraint(_RUN_STATE_CHECK, name="ck_ea_practice_state"),
        CheckConstraint(
            "(state = 'submitted' AND submitted_at IS NOT NULL "
            "AND interrupted_at IS NULL AND submission_key IS NOT NULL "
            "AND submission_request_digest IS NOT NULL AND raw_result IS NOT NULL "
            "AND metrics IS NOT NULL AND quality_flags IS NOT NULL "
            "AND comparison_key IS NOT NULL) "
            "OR (state = 'interrupted' AND submitted_at IS NULL "
            "AND interrupted_at IS NOT NULL) "
            "OR (state IN ('running', 'awaiting_submission', 'expired') "
            "AND submitted_at IS NULL AND interrupted_at IS NULL)",
            name="ck_ea_practice_state_times",
        ),
        CheckConstraint(
            "run_until > created_at AND submit_until > run_until",
            name="ck_ea_practice_deadlines",
        ),
        CheckConstraint("seed BETWEEN 0 AND 4294967295", name="ck_ea_practice_seed"),
        CheckConstraint(
            f"creation_request_digest {_DIGEST_CHECK}",
            name="ck_ea_practice_creation_digest",
        ),
        CheckConstraint(
            "(submission_key IS NULL AND submission_request_digest IS NULL) OR "
            f"(submission_key IS NOT NULL AND submission_request_digest {_DIGEST_CHECK})",
            name="ck_ea_practice_submission_pair",
        ),
        CheckConstraint(
            f"comparison_key IS NULL OR comparison_key {_DIGEST_CHECK}",
            name="ck_ea_practice_comparison_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    suite_id: Mapped[str] = mapped_column(String(32), nullable=False)
    test_id: Mapped[str] = mapped_column(String(32), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    environment_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="running", server_default="running"
    )
    creation_key: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    creation_request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    submission_key: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    submission_request_digest: Mapped[str | None] = mapped_column(String(64))
    interrupt_key: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    run_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submit_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quality_flags: Mapped[list[str] | None] = mapped_column(JSONB)
    comparison_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
