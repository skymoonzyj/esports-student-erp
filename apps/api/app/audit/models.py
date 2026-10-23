from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('allowed', 'denied', 'failed')",
            name="ck_audit_events_outcome",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    purpose: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
