from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)

AssetStatus = Literal["available", "assigned", "maintenance", "retired"]
AssetPhotoMime = Literal["image/jpeg", "image/png", "image/webp"]
AssetExportFormat = Literal["xlsx", "csv"]


class AssetInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssetWarrantyDetails(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    warranty_channel: Literal["online", "offline"] | None = None
    warranty_service_location: str | None = Field(default=None, max_length=200)
    warranty_contact_name: str | None = Field(default=None, max_length=80)
    warranty_contact_phone: str | None = Field(default=None, max_length=40)
    warranty_product_url: str | None = Field(default=None, max_length=2000)
    warranty_order_number: str | None = Field(default=None, max_length=120)

    @field_validator("warranty_product_url")
    @classmethod
    def validate_product_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        url = TypeAdapter(HttpUrl).validate_python(value.strip())
        if url.username is not None or url.password is not None:
            raise ValueError("产品链接不能包含登录凭据")
        return str(url)


class AssetCreate(AssetInputModel, AssetWarrantyDetails):
    name: str = Field(min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=160)
    purchase_date: date | None = None
    warranty_expires_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_warranty_range(self) -> Self:
        if (
            self.purchase_date is not None
            and self.warranty_expires_on is not None
            and self.warranty_expires_on < self.purchase_date
        ):
            raise ValueError("warranty expiry cannot precede purchase date")
        return self


class AssetUpdate(AssetInputModel, AssetWarrantyDetails):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    status: AssetStatus | None = None
    location: str | None = Field(default=None, max_length=160)
    purchase_date: date | None = None
    warranty_expires_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_mutation(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("asset update requires at least one field")
        return self


class AssetAssignRequest(AssetInputModel):
    person_id: UUID
    assigned_at: AwareDatetime
    expected_return_at: AwareDatetime | None = None
    condition_on_assign: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.expected_return_at is not None and self.expected_return_at <= self.assigned_at:
            raise ValueError("expected return must be after assignment")
        return self


class AssetReturnRequest(AssetInputModel):
    returned_at: AwareDatetime
    condition_on_return: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class AssetAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    person_id: UUID
    person_name: str
    assigned_at: AwareDatetime
    expected_return_at: AwareDatetime | None
    returned_at: AwareDatetime | None
    condition_on_assign: str | None
    condition_on_return: str | None
    note: str | None


class AssetPhotoResponse(BaseModel):
    id: UUID
    mime_type: AssetPhotoMime
    extension: Literal["jpg", "png", "webp"]
    size_bytes: int
    updated_at: datetime
    url: str


class AssetResponse(AssetWarrantyDetails):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_code: str
    name: str
    category: str | None
    brand: str | None
    model: str | None
    serial_number: str | None
    status: AssetStatus
    location: str | None
    purchase_date: date | None
    warranty_expires_on: date | None
    source_purchase_id: UUID | None
    note: str | None
    created_at: datetime
    updated_at: datetime
    current_assignment: AssetAssignmentResponse | None = None
    photo: AssetPhotoResponse | None = None


class AssetSelfResponse(AssetWarrantyDetails):
    id: UUID
    asset_code: str
    name: str
    category: str | None
    brand: str | None
    model: str | None
    status: AssetStatus
    location: str | None
    purchase_date: date | None
    warranty_expires_on: date | None
    assigned_at: AwareDatetime
    expected_return_at: AwareDatetime | None
    note: str | None
    photo: AssetPhotoResponse | None = None


class AssetPage(BaseModel):
    items: list[AssetResponse]
    page: int
    page_size: int
    total: int


class AssetScanResponse(BaseModel):
    asset_code: str
    name: str
    category: str | None
    brand: str | None
    model: str | None
    status: AssetStatus
    location: str | None
    purchase_date: date | None
    warranty_expires_on: date | None
    scanned_at: datetime


class AssetExportRequest(AssetInputModel):
    format: AssetExportFormat = "xlsx"
    purpose: str = Field(min_length=4, max_length=200)
    query: str | None = Field(default=None, max_length=120)
    status: AssetStatus | None = None
    category: str | None = Field(default=None, max_length=80)
