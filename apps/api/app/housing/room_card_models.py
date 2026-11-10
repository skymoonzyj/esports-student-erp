from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class HousingRoomCardRecord(TimestampMixin, Base):
    __tablename__ = "housing_room_cards"
    __table_args__ = (
        CheckConstraint(
            "(issued_on IS NULL AND expires_on IS NULL) OR "
            "(issued_on IS NOT NULL AND expires_on IS NOT NULL AND expires_on >= issued_on "
            "AND issued_on >= DATE '0001-01-01' AND expires_on <= DATE '9999-12-31')",
            name="ck_housing_room_cards_dates",
        ),
        CheckConstraint("version >= 1", name="ck_housing_room_cards_version"),
        Index("ix_housing_room_cards_expiry", "expires_on", "stay_id"),
    )

    stay_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("housing_stays.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    issued_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
