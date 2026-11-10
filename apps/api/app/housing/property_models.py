from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class HousingPropertyChange(Base):
    __tablename__ = "housing_property_changes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','withdrawn')",
            name="ck_housing_property_changes_status",
        ),
        CheckConstraint("base_version >= 1", name="ck_housing_property_changes_version"),
        CheckConstraint("btrim(reason) <> ''", name="ck_housing_property_changes_reason"),
        CheckConstraint(
            "(status = 'pending' AND reviewed_at IS NULL AND reviewed_by_user_id IS NULL) OR (status <> 'pending' AND reviewed_at IS NOT NULL AND reviewed_by_user_id IS NOT NULL)",
            name="ck_housing_property_changes_review",
        ),
        CheckConstraint(
            "status <> 'approved' OR reviewed_by_user_id <> submitted_by_user_id",
            name="ck_housing_property_changes_not_self",
        ),
        CheckConstraint(
            "status <> 'rejected' OR (review_note IS NOT NULL AND btrim(review_note) <> '')",
            name="ck_housing_property_changes_rejection",
        ),
        Index(
            "uq_housing_property_changes_pending_room",
            "room_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_housing_property_changes_status_submitted", "status", "submitted_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        ForeignKey("housing_rooms.id", ondelete="RESTRICT"), nullable=False
    )
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    submitted_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
