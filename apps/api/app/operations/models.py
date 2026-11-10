from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class OperationsServiceHeartbeat(Base):
    __tablename__ = "operations_service_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "status IN ('healthy', 'failed')",
            name="ck_operations_service_heartbeats_status",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_operations_service_heartbeats_failures",
        ),
        CheckConstraint(
            "last_result_count IS NULL OR last_result_count >= 0",
            name="ck_operations_service_heartbeats_result_count",
        ),
        CheckConstraint(
            "last_error_code IS NULL OR last_error_code ~ '^[A-Za-z][A-Za-z0-9_]{0,79}$'",
            name="ck_operations_service_heartbeats_error_code",
        ),
    )

    service_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_result_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OperationsMetricBatch(Base):
    """Receipt retained as long as metrics, making a retried flush harmless."""

    __tablename__ = "operations_metric_batches"

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OperationsRequestMinute(Base):
    __tablename__ = "operations_request_minutes"

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    method: Mapped[str] = mapped_column(String(16), primary_key=True)
    route: Mapped[str] = mapped_column(String(300), primary_key=True)
    status_code: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_sum_ms: Mapped[float] = mapped_column(Float, nullable=False)
    max_latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    latency_histogram: Mapped[list[int]] = mapped_column(JSONB, nullable=False)


class OperationsRequestError(Base):
    __tablename__ = "operations_request_errors"
    __table_args__ = (
        CheckConstraint("status_code BETWEEN 400 AND 599", name="ck_operations_request_errors_status"),
        Index("ix_operations_request_errors_occurred", "occurred_at"),
        Index("ix_operations_request_errors_account_time", "account_id", "occurred_at"),
        Index("ix_operations_request_errors_source_time", "source_ip", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Deliberately not a foreign key: attribution survives account deletion.
    account_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_ip: Mapped[str | None] = mapped_column(String(45))
    request_id: Mapped[str | None] = mapped_column(String(128))
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    route: Mapped[str] = mapped_column(String(300), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)


class OperationsAlert(Base):
    __tablename__ = "operations_alerts"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'resolved')", name="ck_operations_alert_status"),
        CheckConstraint(
            "severity IN ('warning', 'critical')", name="ck_operations_alert_severity"
        ),
        Index(
            "uq_operations_alert_active_key", "key", unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_operations_alert_first_seen", "first_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="SET NULL")
    )
