from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.asset_management.rental_models import (
    RENTAL_FACTS_CONSTRAINT,
    AssetRentalOperation,
    AssetRentalRenewal,
)
from app.core.models import Base, TimestampMixin

__all__ = ["Asset", "AssetAssignment", "AssetPhoto", "AssetRentalOperation", "AssetRentalRenewal"]

ASSET_STATUSES = ("available", "assigned", "maintenance", "retired")


class Asset(TimestampMixin, Base):
    """A durable item tracked by the ERP.

    ``asset_code`` is intentionally separate from the database id. It is the
    stable, human-readable identifier printed on the QR label and must never
    be recycled after an asset is retired.
    """

    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('available', 'assigned', 'maintenance', 'retired')",
            name="ck_assets_status",
        ),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 120", name="ck_assets_name"),
        CheckConstraint(
            "purchase_date IS NULL OR warranty_expires_on IS NULL "
            "OR warranty_expires_on >= purchase_date",
            name="ck_assets_warranty_range",
        ),
        Index("ix_assets_status_updated", "status", "updated_at", "id"),
        Index("ix_assets_name", "name"),
        CheckConstraint(
            "warranty_channel IS NULL OR warranty_channel IN ('online', 'offline')",
            name="ck_assets_warranty_channel",
        ),
        Index("ix_assets_source_purchase", "source_purchase_id", "id"),
        CheckConstraint(RENTAL_FACTS_CONSTRAINT, name="ck_assets_rental_facts"),
        CheckConstraint("rental_revision >= 0", name="ck_assets_rental_revision"),
        Index("ix_assets_rental_expiry", "ownership_type", "rental_returned_on", "rental_expires_on", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    brand: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    cpu_model: Mapped[str | None] = mapped_column(String(120))
    memory_capacity: Mapped[str | None] = mapped_column(String(80))
    gpu_model: Mapped[str | None] = mapped_column(String(120))
    disk_capacity: Mapped[str | None] = mapped_column(String(120))
    serial_number: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="available", server_default="available"
    )
    location: Mapped[str | None] = mapped_column(String(160))
    ownership_type: Mapped[str | None] = mapped_column(String(16))
    acquisition_method: Mapped[str | None] = mapped_column(String(16))
    acquisition_channel: Mapped[str | None] = mapped_column(String(16))
    acquisition_note: Mapped[str | None] = mapped_column(String(500))
    rental_started_on: Mapped[date | None] = mapped_column(Date)
    rental_expires_on: Mapped[date | None] = mapped_column(Date)
    rental_returned_on: Mapped[date | None] = mapped_column(Date)
    rental_revision: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    purchase_date: Mapped[date | None] = mapped_column(Date)
    warranty_expires_on: Mapped[date | None] = mapped_column(Date)
    warranty_channel: Mapped[str | None] = mapped_column(String(20))
    warranty_service_location: Mapped[str | None] = mapped_column(String(200))
    warranty_contact_name: Mapped[str | None] = mapped_column(String(80))
    warranty_contact_phone: Mapped[str | None] = mapped_column(String(40))
    warranty_product_url: Mapped[str | None] = mapped_column(String(2000))
    warranty_order_number: Mapped[str | None] = mapped_column(String(120))
    source_purchase_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("daily_purchase_records.id", ondelete="SET NULL"),
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    photo: Mapped[AssetPhoto | None] = relationship(
        "AssetPhoto",
        back_populates="asset",
        uselist=False,
        cascade="all, delete-orphan",
    )


class AssetBatchOperation(TimestampMixin, Base):
    __tablename__ = "asset_batch_operations"
    __table_args__ = (
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_asset_batch_hash"),
        CheckConstraint("quantity BETWEEN 1 AND 100", name="ck_asset_batch_quantity"),
        CheckConstraint("jsonb_typeof(asset_ids) = 'array' AND jsonb_array_length(asset_ids) = quantity", name="ck_asset_batch_result"),
    )

    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_accounts.id", ondelete="RESTRICT"), primary_key=True)
    operation_key: Mapped[UUID] = mapped_column(primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    asset_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


class AssetPhoto(TimestampMixin, Base):
    """The optional identification photo for an asset.

    Keeping the photo in its own row prevents the inventory listing from
    carrying image bytes while still making replacement transactional.
    """

    __tablename__ = "asset_photos"
    __table_args__ = (
        CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_asset_photos_mime_type",
        ),
        CheckConstraint(
            "byte_size BETWEEN 1 AND 10485760",
            name="ck_asset_photos_byte_size",
        ),
        CheckConstraint(
            "char_length(sha256) = 64 AND sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_asset_photos_sha256",
        ),
        Index("ix_asset_photos_updated", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    extension: Mapped[str] = mapped_column(String(8), nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset: Mapped[Asset] = relationship("Asset", back_populates="photo")


class AssetAssignment(TimestampMixin, Base):
    """An assignment history row; an open row represents current custody."""

    __tablename__ = "asset_assignments"
    __table_args__ = (
        CheckConstraint(
            "returned_at IS NULL OR returned_at >= assigned_at",
            name="ck_asset_assignments_return_range",
        ),
        Index("ix_asset_assignments_person_active", "person_id", "returned_at", "id"),
        Index(
            "uq_asset_assignments_active_asset",
            "asset_id",
            unique=True,
            postgresql_where=text("returned_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_return_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    condition_on_assign: Mapped[str | None] = mapped_column(String(500))
    condition_on_return: Mapped[str | None] = mapped_column(String(500))
    note: Mapped[str | None] = mapped_column(String(500))
    assigned_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    returned_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
