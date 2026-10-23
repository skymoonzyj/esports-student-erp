from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class TravelReportConversion(TimestampMixin, Base):
    __tablename__ = "travel_report_conversions"
    __table_args__ = (
        CheckConstraint(
            "source_byte_size BETWEEN 1 AND 20971520",
            name="ck_travel_report_source_size",
        ),
        CheckConstraint(
            "converted_byte_size BETWEEN 1 AND 52428800",
            name="ck_travel_report_converted_size",
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_travel_report_source_sha256",
        ),
        CheckConstraint(
            "converted_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_travel_report_converted_sha256",
        ),
        CheckConstraint(
            "billing_period_end >= billing_period_start",
            name="ck_travel_report_billing_period",
        ),
        CheckConstraint(
            "domestic_air_count >= 0 AND international_air_count >= 0 "
            "AND train_count >= 0",
            name="ck_travel_report_counts",
        ),
        Index(
            "ix_travel_report_conversions_created",
            "created_at",
            "id",
        ),
        Index(
            "ix_travel_report_conversions_period",
            "billing_period_start",
            "billing_period_end",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    converted_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    converted_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    converted_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    converted_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cost_center: Mapped[str] = mapped_column(String(255), nullable=False)
    billing_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    billing_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    domestic_air_count: Mapped[int] = mapped_column(nullable=False)
    international_air_count: Mapped[int] = mapped_column(nullable=False)
    train_count: Mapped[int] = mapped_column(nullable=False)
    source_total_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    converted_total_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    warnings: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    imported_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
