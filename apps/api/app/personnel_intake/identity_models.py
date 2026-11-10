"""Immutable initial identity selection evidence, not a live role plan."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class IntakeIdentitySelection(Base):
    __tablename__ = "intake_identity_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["origin_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            ondelete="RESTRICT",
            name="fk_intake_identity_selection_approval_person",
        ),
        CheckConstraint(
            "operation_digest ~ '^[0-9a-f]{64}$'", name="ck_intake_identity_selection_digest"
        ),
    )
    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), primary_key=True
    )
    origin_approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    primary_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    concurrent_identities: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    primary_staff_identity: Mapped[str | None] = mapped_column(String(64))
    resolved_staff_type: Mapped[str | None] = mapped_column(String(32))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    operation_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    operation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
