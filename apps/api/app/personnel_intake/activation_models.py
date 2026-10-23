from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class AssistedActivationSession(TimestampMixin, Base):
    __tablename__ = "assisted_activation_sessions"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_assisted_activation_token"),
        UniqueConstraint("challenge_id", name="uq_assisted_activation_challenge"),
        ForeignKeyConstraint(
            ["approval_id", "person_id", "revision_version"],
            [
                "assisted_approvals.id",
                "assisted_approvals.person_id",
                "assisted_approvals.revision_version",
            ],
            name="fk_assisted_activation_approval",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "state IN ('requested', 'verified', 'completed')", name="ck_assisted_activation_state"
        ),
        CheckConstraint(
            "token_digest ~ '^[0-9a-f]{64}$' AND email_digest ~ '^[0-9a-f]{64}$'",
            name="ck_assisted_activation_digests",
        ),
        CheckConstraint("expires_at > created_at", name="ck_assisted_activation_expiry"),
        CheckConstraint(
            "(state='requested' AND verified_at IS NULL AND completed_at IS NULL) OR "
            "(state='verified' AND verified_at IS NOT NULL AND completed_at IS NULL) OR "
            "(state='completed' AND verified_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND completed_at >= verified_at)",
            name="ck_assisted_activation_times",
        ),
        Index("ix_assisted_activation_expiry", "state", "expires_at", "id"),
        Index("ix_assisted_activation_person", "person_id", "created_at", "id"),
        Index("ix_assisted_activation_email", "email_digest", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    challenge_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("registration_verification_challenges.id", ondelete="RESTRICT"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    email_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
