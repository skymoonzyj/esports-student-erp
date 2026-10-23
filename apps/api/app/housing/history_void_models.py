from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class HousingStayVoidOperation(Base):
    __tablename__ = "housing_stay_void_operations"
    __table_args__ = (
        UniqueConstraint(
            "actor_account_id", "idempotency_key", name="uq_housing_stay_void_actor_key"
        ),
        UniqueConstraint("stay_id", name="uq_housing_stay_void_stay"),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_housing_stay_void_fingerprint"
        ),
        CheckConstraint("result_version >= 1", name="ck_housing_stay_void_result_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT", name="fk_housing_stay_void_actor"),
        nullable=False,
    )
    idempotency_key: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    stay_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("housing_stays.id", ondelete="RESTRICT", name="fk_housing_stay_void_stay"),
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
