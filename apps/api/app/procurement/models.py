from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import Base, TimestampMixin


class ProcurementSettlementCenter(TimestampMixin, Base):
    __tablename__ = "procurement_settlement_centers"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_procurement_settlement_centers_name",
        ),
        Index(
            "ix_procurement_settlement_centers_active_name",
            "is_active",
            "name",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    purchases: Mapped[list[DailyPurchaseRecord]] = relationship(
        "DailyPurchaseRecord",
        back_populates="settlement_center",
    )
    reserve_fund_entries: Mapped[list[ProcurementReserveFundEntry]] = relationship(
        "ProcurementReserveFundEntry",
        back_populates="settlement_center",
    )


Index(
    "uq_procurement_settlement_centers_name_ci",
    func.lower(ProcurementSettlementCenter.name),
    unique=True,
)


class ProcurementReserveFundEntry(TimestampMixin, Base):
    __tablename__ = "procurement_reserve_fund_entries"
    __table_args__ = (
        UniqueConstraint(
            "created_by_user_id", "operation_key", name="uq_procurement_reserve_operation"
        ),
        CheckConstraint(
            "(operation_key IS NULL AND request_fingerprint IS NULL) OR "
            "(operation_key IS NOT NULL AND request_fingerprint IS NOT NULL "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$')",
            name="ck_procurement_reserve_operation",
        ),
        CheckConstraint(
            "amount_cents > 0",
            name="ck_procurement_reserve_fund_entries_amount",
        ),
        Index(
            "ix_procurement_reserve_fund_entries_center_created",
            "settlement_center_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    settlement_center_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("procurement_settlement_centers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_key: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    settlement_center: Mapped[ProcurementSettlementCenter] = relationship(
        "ProcurementSettlementCenter",
        back_populates="reserve_fund_entries",
    )


class DailyPurchaseRecord(TimestampMixin, Base):
    __tablename__ = "daily_purchase_records"
    __table_args__ = (
        UniqueConstraint(
            "created_by_user_id", "operation_key", name="uq_procurement_purchase_operation"
        ),
        CheckConstraint(
            "(operation_key IS NULL AND request_fingerprint IS NULL) OR "
            "(operation_key IS NOT NULL AND request_fingerprint IS NOT NULL "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$')",
            name="ck_procurement_purchase_operation",
        ),
        CheckConstraint(
            "char_length(btrim(item_name)) BETWEEN 1 AND 120",
            name="ck_daily_purchase_records_item_name",
        ),
        CheckConstraint(
            "purchase_channel IN ('online', 'offline')",
            name="ck_daily_purchase_records_purchase_channel",
        ),
        CheckConstraint(
            "(purchase_channel = 'offline' AND product_url IS NULL) OR "
            "(purchase_channel = 'online' AND (product_url IS NULL OR "
            "char_length(product_url) BETWEEN 1 AND 2048))",
            name="ck_daily_purchase_records_product_url",
        ),
        CheckConstraint(
            "quantity > 0 AND quantity <= 999999999.999",
            name="ck_daily_purchase_records_quantity",
        ),
        CheckConstraint(
            "unit_price_cents > 0",
            name="ck_daily_purchase_records_unit_price",
        ),
        CheckConstraint(
            "total_price_cents = round(quantity * unit_price_cents)",
            name="ck_daily_purchase_records_total_price",
        ),
        CheckConstraint(
            "(is_settled AND settled_on IS NOT NULL AND settled_by_user_id IS NOT NULL) "
            "OR (NOT is_settled AND settled_on IS NULL AND settled_by_user_id IS NULL)",
            name="ck_daily_purchase_records_settlement",
        ),
        Index(
            "ix_daily_purchase_records_date",
            "purchase_date",
            "created_at",
            "id",
        ),
        Index(
            "ix_daily_purchase_records_center_date",
            "settlement_center_id",
            "purchase_date",
            "id",
        ),
        Index(
            "ix_daily_purchase_records_settled_date",
            "is_settled",
            "purchase_date",
            "id",
        ),
        Index(
            "ix_daily_purchase_records_reimbursed_date",
            "is_reimbursed",
            "purchase_date",
            "id",
        ),
        Index(
            "ix_daily_purchase_records_reserve_fund_center",
            "uses_reserve_fund",
            "settlement_center_id",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    operation_key: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    item_name: Mapped[str] = mapped_column(String(120), nullable=False)
    purchase_channel: Mapped[Literal["online", "offline"]] = mapped_column(
        String(16), nullable=False, default="offline", server_default="offline"
    )
    product_url: Mapped[str | None] = mapped_column(String(2048))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settlement_center_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("procurement_settlement_centers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    is_reimbursed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    uses_reserve_fund: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_settled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    settled_on: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    settled_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    settlement_center: Mapped[ProcurementSettlementCenter] = relationship(
        "ProcurementSettlementCenter",
        back_populates="purchases",
    )
    invoice_link: Mapped[ProcurementInvoicePurchase | None] = relationship(
        "ProcurementInvoicePurchase",
        back_populates="purchase",
        uselist=False,
    )


class ProcurementInvoice(TimestampMixin, Base):
    __tablename__ = "procurement_invoices"
    __table_args__ = (
        CheckConstraint(
            "amount_cents > 0",
            name="ck_procurement_invoices_amount",
        ),
        CheckConstraint(
            "mime_type IN ('application/pdf', 'image/jpeg', 'image/png', 'image/webp')",
            name="ck_procurement_invoices_mime_type",
        ),
        CheckConstraint(
            "extension IN ('pdf', 'jpg', 'png', 'webp')",
            name="ck_procurement_invoices_extension",
        ),
        CheckConstraint(
            "byte_size > 0 AND byte_size <= 20971520",
            name="ck_procurement_invoices_byte_size",
        ),
        CheckConstraint(
            "char_length(sha256) = 64",
            name="ck_procurement_invoices_sha256",
        ),
        CheckConstraint(
            "char_length(btrim(original_filename)) BETWEEN 1 AND 255",
            name="ck_procurement_invoices_filename",
        ),
        Index(
            "ix_procurement_invoices_date",
            "invoice_date",
            "created_at",
            "id",
        ),
        Index("ix_procurement_invoices_sha256", "sha256"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    invoice_number: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(40), nullable=False)
    extension: Mapped[str] = mapped_column(String(8), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    purchase_links: Mapped[list[ProcurementInvoicePurchase]] = relationship(
        "ProcurementInvoicePurchase",
        back_populates="invoice",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ProcurementInvoicePurchase(Base):
    __tablename__ = "procurement_invoice_purchases"
    __table_args__ = (
        UniqueConstraint(
            "purchase_id",
            name="uq_procurement_invoice_purchases_purchase",
        ),
        Index(
            "ix_procurement_invoice_purchases_invoice",
            "invoice_id",
            "purchase_id",
        ),
    )

    invoice_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("procurement_invoices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    purchase_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("daily_purchase_records.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    invoice: Mapped[ProcurementInvoice] = relationship(
        "ProcurementInvoice",
        back_populates="purchase_links",
    )
    purchase: Mapped[DailyPurchaseRecord] = relationship(
        "DailyPurchaseRecord",
        back_populates="invoice_link",
    )
