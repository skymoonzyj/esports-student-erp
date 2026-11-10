from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import Base

_TEST_IDS = (
    "reactiontime",
    "aim",
    "sequence",
    "number-memory",
    "verbal-memory",
    "chimp",
    "memory",
    "dynamic-vision",
)


class TalentTestSession(Base):
    __tablename__ = "talent_test_sessions"
    __table_args__ = (
        UniqueConstraint(
            "person_id",
            "creation_key",
            name="uq_talent_sessions_person_creation",
        ),
        Index(
            "uq_talent_sessions_active_person",
            "person_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
        ),
        Index(
            "ix_talent_sessions_person_created",
            "person_id",
            "created_at",
            "id",
        ),
        CheckConstraint(
            "(status = 'in_progress' AND completed_at IS NULL AND abandoned_at IS NULL) "
            "OR (status = 'completed' AND completed_at IS NOT NULL "
            "AND abandoned_at IS NULL) "
            "OR (status = 'abandoned' AND completed_at IS NULL "
            "AND abandoned_at IS NOT NULL)",
            name="ck_talent_sessions_state_times",
        ),
        CheckConstraint(
            "(status = 'abandoned' AND abandon_request_id IS NOT NULL "
            "AND abandon_request_digest IS NOT NULL "
            "AND abandon_request_digest ~ '^[0-9a-f]{64}$') "
            "OR (status IN ('in_progress', 'completed') "
            "AND abandon_request_id IS NULL AND abandon_request_digest IS NULL)",
            name="ck_talent_sessions_abandon_pair",
        ),
        CheckConstraint(
            "creation_request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_talent_sessions_creation_digest",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
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
    creation_key: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    creation_request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    abandon_request_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    abandon_request_digest: Mapped[str | None] = mapped_column(String(64))
    catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_version: Mapped[str] = mapped_column(String(64), nullable=False)
    report_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
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

    results: Mapped[list[TalentTestResult]] = relationship(
        back_populates="session",
        order_by="TalentTestResult.created_at",
    )
    report: Mapped[TalentTestReport | None] = relationship(
        back_populates="session",
        uselist=False,
    )


class TalentTestResult(Base):
    __tablename__ = "talent_test_results"
    __table_args__ = (
        Index(
            "ix_talent_results_item_conditions",
            "test_id",
            "engine_version",
            "locale",
            text("(device_snapshot->>'input_kind')"),
            text("(normalized_result->>'scoring_version')"),
            "session_id",
            postgresql_where=text(
                "quality_flags = '[]'::jsonb AND background_interrupted IS FALSE"
            ),
        ),
        UniqueConstraint(
            "session_id",
            "test_id",
            name="uq_talent_results_session_test",
        ),
        UniqueConstraint(
            "session_id",
            "submission_id",
            name="uq_talent_results_session_submission",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_talent_results_score",
        ),
        CheckConstraint(
            "test_id IN (" + ", ".join(f"'{test_id}'" for test_id in _TEST_IDS) + ")",
            name="ck_talent_results_test_id",
        ),
        CheckConstraint(
            "submission_digest ~ '^[0-9a-f]{64}$'",
            name="ck_talent_results_submission_digest",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("talent_test_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    test_id: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    device_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    duration_ms: Mapped[float] = mapped_column(nullable=False)
    background_interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    submission_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    submission_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped[TalentTestSession] = relationship(back_populates="results")


class TalentTestReport(Base):
    __tablename__ = "talent_test_reports"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_talent_reports_session"),
        Index("ix_talent_reports_created", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("talent_test_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped[TalentTestSession] = relationship(back_populates="report")
