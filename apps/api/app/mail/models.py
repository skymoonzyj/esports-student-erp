from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class MailDeliverySettings(TimestampMixin, Base):
    """The single administrator-managed SMTP account.

    Credential-bearing values are encrypted before they reach this table.  The
    API only exposes masked identities and never returns the authorization code.
    """

    __tablename__ = "mail_delivery_settings"
    __table_args__ = (
        CheckConstraint(
            "smtp_port BETWEEN 1 AND 65535",
            name="ck_mail_delivery_settings_port",
        ),
        CheckConstraint(
            "tls_mode IN ('implicit_tls', 'starttls')",
            name="ck_mail_delivery_settings_tls_mode",
        ),
        CheckConstraint(
            "last_test_status IN ('never', 'passed', 'failed')",
            name="ck_mail_delivery_settings_test_status",
        ),
        UniqueConstraint("singleton_key", name="uq_mail_delivery_settings_singleton"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    singleton_key: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="default",
        server_default="default",
    )
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False)
    tls_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    username_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    password_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    sender_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    from_name: Mapped[str] = mapped_column(String(120), nullable=False)
    reply_to_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    last_test_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="never",
        server_default="never",
    )
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
