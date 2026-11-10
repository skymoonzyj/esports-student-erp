"""Persist exact renewal results and immutable per-asset lease history."""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base

RENTAL_FACTS_CONSTRAINT = """COALESCE((
(ownership_type IS NULL AND acquisition_method IS NULL AND acquisition_channel IS NULL
 AND acquisition_note IS NULL AND rental_started_on IS NULL AND rental_expires_on IS NULL
 AND rental_returned_on IS NULL)
OR (ownership_type = 'owned' AND acquisition_method IN ('purchase', 'other')
 AND rental_started_on IS NULL AND rental_expires_on IS NULL AND rental_returned_on IS NULL
 AND (acquisition_channel IS NULL OR acquisition_channel IN ('online', 'offline'))
 AND ((acquisition_method = 'purchase' AND acquisition_channel IS NOT NULL) OR (acquisition_method = 'other' AND acquisition_channel IS NULL
 AND acquisition_note IS NOT NULL AND length(btrim(acquisition_note)) > 0)))
OR (ownership_type = 'rented' AND acquisition_method IS NULL AND acquisition_channel IS NULL
 AND acquisition_note IS NULL AND rental_started_on IS NOT NULL AND rental_expires_on IS NOT NULL
 AND rental_started_on <= rental_expires_on
 AND rental_started_on >= DATE '0001-01-01' AND rental_expires_on <= DATE '9999-12-31'
 AND (rental_returned_on IS NULL OR (rental_returned_on >= rental_started_on AND rental_returned_on <= DATE '9999-12-31')))
), FALSE)"""


class AssetRentalOperation(Base):
    __tablename__ = "asset_rental_operations"
    operation_key: Mapped[UUID] = mapped_column(primary_key=True)
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_accounts.id", ondelete="RESTRICT"))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetRentalRenewal(Base):
    __tablename__ = "asset_rental_renewals"
    __table_args__ = (
        CheckConstraint(
            "period IN ('week', 'month', 'quarter')", name="ck_asset_rental_renewals_period"
        ),
        CheckConstraint(
            "rental_expires_on > previous_expires_on", name="ck_asset_rental_renewals_range"
        ),
        Index("ix_asset_rental_renewals_asset_time", "asset_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_key: Mapped[UUID] = mapped_column(
        ForeignKey("asset_rental_operations.operation_key", ondelete="RESTRICT")
    )
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_accounts.id", ondelete="RESTRICT"))
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    previous_expires_on: Mapped[date] = mapped_column(Date, nullable=False)
    rental_expires_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
