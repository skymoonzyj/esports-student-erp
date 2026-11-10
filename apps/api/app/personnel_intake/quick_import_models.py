from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class QuickIntakeReceipt(Base):
    """Successful atomic imports only; no plaintext identity or contact data."""

    __tablename__ = "quick_intake_receipts"
    __table_args__ = (
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_quick_intake_fingerprint"),
        CheckConstraint("stay_status IN ('planned','checked_in')", name="ck_quick_intake_status"),
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("user_accounts.id", ondelete="RESTRICT"), primary_key=True)
    operation_key: Mapped[UUID] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    person_id: Mapped[UUID] = mapped_column(ForeignKey("people.id", ondelete="RESTRICT"))
    stay_id: Mapped[UUID] = mapped_column(ForeignKey("housing_stays.id", ondelete="RESTRICT"))
    room_id: Mapped[UUID] = mapped_column(ForeignKey("housing_rooms.id", ondelete="RESTRICT"))
    stay_status: Mapped[str] = mapped_column(String(16))
    role_pending: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
