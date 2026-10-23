from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class UserAccount(TimestampMixin, Base):
    __tablename__ = "user_accounts"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_user_accounts_status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    login_phone_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    force_password_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    __table_args__ = (Index("ix_auth_rate_limits_updated_at", "updated_at"),)

    key_digest: Mapped[str] = mapped_column(Text, primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RegistrationRequest(TimestampMixin, Base):
    """A public registration application awaiting email verification.

    The verification code is never persisted in plaintext.  Verified requests
    are retained as a small audit trail and point at the person/account they
    created; this makes duplicate submissions and support investigations
    deterministic without putting raw credentials in the database.
    """

    __tablename__ = "registration_requests"
    __table_args__ = (
        CheckConstraint(
            "requested_person_type IN ('student', 'guardian', 'staff')",
            name="ck_registration_requests_person_type",
        ),
        CheckConstraint(
            "requested_person_type <> 'staff' OR requested_staff_type IS NOT NULL",
            name="ck_registration_requests_staff_type",
        ),
        CheckConstraint(
            "status IN ('pending_verification', 'completed', 'expired', 'rejected')",
            name="ck_registration_requests_status",
        ),
        Index("ix_registration_requests_email", "email_normalized"),
        Index("ix_registration_requests_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    full_name: Mapped[str] = mapped_column(String(80), nullable=False)
    identity_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    phone_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    sex: Mapped[str] = mapped_column(String(16), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    requested_person_type: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_staff_type: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    verification_code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_code_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_verification")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT")
    )
    account_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
