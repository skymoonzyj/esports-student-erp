from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TravelReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    converted_filename: str
    source_sha256: str
    converted_sha256: str
    source_byte_size: int
    converted_byte_size: int
    company_name: str
    cost_center: str
    billing_period_start: date
    billing_period_end: date
    domestic_air_count: int
    international_air_count: int
    train_count: int
    source_total_amount: Decimal
    converted_total_amount: Decimal
    warnings: list[str]
    imported_by_user_id: UUID
    imported_by_name: str
    created_at: datetime


class TravelReportBillingPeriodOption(BaseModel):
    billing_period_start: date
    billing_period_end: date


class TravelReportFilterOptions(BaseModel):
    billing_periods: list[TravelReportBillingPeriodOption]
    cost_centers: list[str]


class TravelReportPage(BaseModel):
    items: list[TravelReportResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    filter_options: TravelReportFilterOptions
