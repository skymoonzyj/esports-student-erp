from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin
from app.personnel_intake import models as personnel_intake_models  # noqa: F401


class UserAccount(TimestampMixin, Base):
    __tablename__ = "user_accounts"
    __table_args__ = (
        CheckConstraint(
            "(account_access_origin = 'legacy' AND assisted_approval_id IS NULL) OR "
            "(account_access_origin = 'assisted_activation' AND assisted_approval_id IS NOT NULL)",
            name="ck_user_accounts_access_origin",
        ),
        ForeignKeyConstraint(
            ["assisted_approval_id", "person_id"],
            ["assisted_approvals.id", "assisted_approvals.person_id"],
            name="fk_user_accounts_assisted_approval_person",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_user_accounts_status"),
        CheckConstraint(
            "self_service_primary_purpose IS NULL OR "
            "self_service_primary_purpose IN "
            "('student', 'guardian', 'staff_applicant', 'visitor')",
            name="ck_user_accounts_self_service_primary_purpose",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    login_phone_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    email_normalized: Mapped[str | None] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    force_password_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    food_preferences_onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    self_service_primary_purpose: Mapped[str | None] = mapped_column(String(24))
    account_access_origin: Mapped[str] = mapped_column(
        String(24), nullable=False, default="legacy", server_default="legacy"
    )
    assisted_approval_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))


class AccountLoginIdentifier(TimestampMixin, Base):
    __tablename__ = "account_login_identifiers"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('phone', 'email')",
            name="ck_account_login_identifiers_kind",
        ),
        UniqueConstraint(
            "identifier_digest",
            name="uq_account_login_identifiers_identifier_digest",
        ),
        Index(
            "ix_account_login_identifiers_account_kind",
            "account_id",
            "kind",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    identifier_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verification_source: Mapped[str] = mapped_column(String(40), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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
