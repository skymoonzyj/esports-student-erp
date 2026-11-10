from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.asset_management.hardware import AssetHardware

ProcurementExportFormat = Literal["xlsx", "csv"]
ProcurementExportScope = Literal[
    "current",
    "all",
    "unreimbursed",
    "reimbursed",
    "unsettled",
    "settled",
]
ProcurementSettlementStatus = Literal["settled", "unsettled"]
ProcurementReimbursementStatus = Literal["reimbursed", "unreimbursed"]
ProcurementInvoiceStatus = Literal["invoiced", "uninvoiced"]
ProcurementPurchaseChannel = Literal["online", "offline"]
MAX_ASSETS_PER_PURCHASE = 100
_PURCHASE_TOTAL_LIMIT = Decimal("9223372036854775807.5")


def calculate_purchase_total(quantity: Decimal, unit_price_cents: int) -> int:
    try:
        if (
            not quantity.is_finite()
            or quantity <= 0
            or unit_price_cents <= 0
            or quantity >= _PURCHASE_TOTAL_LIMIT
        ):
            raise ValueError("purchase total must fit signed-bigint cents")
        total = quantity * Decimal(unit_price_cents)
        # The half-up boundary is exclusive: max cents + 0.5 rounds out of range.
        if total >= _PURCHASE_TOTAL_LIMIT:
            raise ValueError("purchase total must fit signed-bigint cents")
        return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except DecimalException as exc:
        raise ValueError("purchase total must fit signed-bigint cents") from exc


def _validate_product_url(value: str | None) -> str | None:
    if value is None:
        return None
    if any(character.isspace() for character in value):
        raise ValueError("product URL cannot contain whitespace")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("product URL must use http or https")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("product URL contains an invalid port") from exc
    return value


class ProcurementInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SettlementCenterCreate(ProcurementInputModel):
    name: str = Field(min_length=1, max_length=120)


class SettlementCenterUpdate(ProcurementInputModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_mutation(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("settlement center update requires at least one field")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("settlement center name cannot be null")
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("settlement center status cannot be null")
        return self


class SettlementCenterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_active: bool
    reserve_fund_total_cents: int
    reserve_fund_used_cents: int
    reserve_fund_remaining_cents: int
    created_at: datetime
    updated_at: datetime


class ReserveFundEntryCreate(ProcurementInputModel):
    # HTTP routes require this; optional only for existing internal service callers.
    operation_key: UUID | None = None
    amount_cents: int = Field(gt=0, le=9_000_000_000_000)
    note: str | None = Field(default=None, min_length=1, max_length=500)


class PurchaseCreate(ProcurementInputModel):
    operation_key: UUID | None = None
    purchase_date: date
    item_name: str = Field(min_length=1, max_length=120)
    purchase_channel: ProcurementPurchaseChannel = "offline"
    product_url: str | None = Field(default=None, min_length=1, max_length=2048)
    quantity: Decimal = Field(gt=0, le=Decimal("999999999.999"), decimal_places=3)
    unit_price_cents: int = Field(gt=0, le=9_000_000_000_000)
    settlement_center_id: UUID
    uses_reserve_fund: bool = False
    is_reimbursed: bool = False
    is_settled: bool = False
    settled_on: date | None = None
    add_to_assets: bool = False
    asset_category: str | None = Field(default=None, max_length=80)
    asset_hardware: AssetHardware | None = None
    asset_warranty_expires_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)

    _product_url_is_http = field_validator("product_url")(_validate_product_url)

    @model_validator(mode="after")
    def validate_settlement(self) -> Self:
        calculate_purchase_total(self.quantity, self.unit_price_cents)
        if self.is_settled != (self.settled_on is not None):
            raise ValueError("settled purchases require a settlement date")
        if self.add_to_assets:
            # Completeness is checked only after durable operation lookup so a
            # pre-feature computer purchase can replay its original payload.
            if self.quantity != self.quantity.to_integral_value():
                raise ValueError("assets can only be created from an integer quantity")
            if self.quantity > MAX_ASSETS_PER_PURCHASE:
                raise ValueError(
                    f"no more than {MAX_ASSETS_PER_PURCHASE} assets can be created at once"
                )
            if (
                self.asset_warranty_expires_on is not None
                and self.asset_warranty_expires_on < self.purchase_date
            ):
                raise ValueError("asset warranty expiry cannot precede purchase date")
        elif self.asset_category is not None or self.asset_warranty_expires_on is not None or self.asset_hardware is not None:
            raise ValueError("asset details require add_to_assets")
        return self


class PurchaseUpdate(ProcurementInputModel):
    purchase_date: date | None = None
    item_name: str | None = Field(default=None, min_length=1, max_length=120)
    purchase_channel: ProcurementPurchaseChannel | None = None
    product_url: str | None = Field(default=None, min_length=1, max_length=2048)
    quantity: Decimal | None = Field(
        default=None,
        gt=0,
        le=Decimal("999999999.999"),
        decimal_places=3,
    )
    unit_price_cents: int | None = Field(default=None, gt=0, le=9_000_000_000_000)
    settlement_center_id: UUID | None = None
    uses_reserve_fund: bool | None = None
    note: str | None = Field(default=None, max_length=2000)

    _product_url_is_http = field_validator("product_url")(_validate_product_url)

    @model_validator(mode="after")
    def require_mutation(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("purchase update requires at least one field")
        required_fields = {
            "purchase_date",
            "item_name",
            "quantity",
            "unit_price_cents",
            "settlement_center_id",
            "purchase_channel",
            "uses_reserve_fund",
        }
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in required_fields
        ):
            raise ValueError("purchase fields cannot be null")
        if self.quantity is not None and self.unit_price_cents is not None:
            calculate_purchase_total(self.quantity, self.unit_price_cents)
        return self


class PurchaseSettlementUpdate(ProcurementInputModel):
    is_settled: bool
    settled_on: date | None = None

    @model_validator(mode="after")
    def validate_settlement(self) -> Self:
        if self.is_settled != (self.settled_on is not None):
            raise ValueError("settled purchases require a settlement date")
        return self


class PurchaseReimbursementUpdate(ProcurementInputModel):
    is_reimbursed: bool


class InvoiceSummary(BaseModel):
    id: UUID
    amount_cents: int
    invoice_date: date
    invoice_number: str | None
    original_filename: str
    mime_type: str
    byte_size: int
    created_at: datetime


class PurchaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purchase_date: date
    item_name: str
    purchase_channel: ProcurementPurchaseChannel
    product_url: str | None
    quantity: Decimal
    unit_price_cents: int
    total_price_cents: int
    settlement_center_id: UUID
    settlement_center_name: str
    uses_reserve_fund: bool
    is_reimbursed: bool
    is_settled: bool
    settled_on: date | None
    has_invoice: bool
    invoice: InvoiceSummary | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class PurchasePage(BaseModel):
    items: list[PurchaseResponse]
    page: int
    page_size: int
    total: int
    total_amount_cents: int
    reimbursed_amount_cents: int
    unreimbursed_amount_cents: int
    settled_amount_cents: int
    unsettled_amount_cents: int


class PurchaseExportRequest(ProcurementInputModel):
    format: ProcurementExportFormat = "xlsx"
    scope: ProcurementExportScope = "current"
    purpose: str = Field(min_length=4, max_length=200)
    query: str | None = Field(default=None, max_length=120)
    settlement_center_id: UUID | None = None
    reimbursement_status: ProcurementReimbursementStatus | None = None
    settlement_status: ProcurementSettlementStatus | None = None
    invoice_status: ProcurementInvoiceStatus | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end date cannot precede start date")
        if self.scope == "unreimbursed" and self.format != "xlsx":
            raise ValueError("pending reimbursement exports require xlsx")
        return self


class InvoiceMatchCandidate(BaseModel):
    id: UUID
    purchase_date: date
    item_name: str
    total_price_cents: int
    settlement_center_id: UUID
    settlement_center_name: str


class InvoiceMatchSuggestionResponse(BaseModel):
    amount_cents: int
    matched_total_cents: int
    suggested_purchase_ids: list[UUID]
    candidates: list[InvoiceMatchCandidate]


class InvoiceResponse(InvoiceSummary):
    note: str | None
    purchase_ids: list[UUID]
    updated_at: datetime
