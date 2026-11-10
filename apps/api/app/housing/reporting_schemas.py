from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.core.privacy import contains_phone_or_identity
from app.housing.reporting_types import ResidentPersonType

ReportScopeMode = Literal["authorized", "buildings", "floors"]
ReportBatchStatus = Literal["generated", "marked_submitted", "voided", "failed"]


def _reject_sensitive_business_text(value: str | None) -> str | None:
    if value is not None and contains_phone_or_identity(value):
        raise ValueError("business text cannot contain a complete phone or identity")
    return value


def _public_business_text(value: str | None) -> str | None:
    if value is not None and contains_phone_or_identity(value):
        return "敏感内容已隐藏"
    return value


class HousingReportInputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class GenerateHousingReportRequest(HousingReportInputModel):
    range_start: AwareDatetime
    range_end: AwareDatetime
    scope_mode: ReportScopeMode
    building_ids: list[UUID] = Field(default_factory=list, max_length=100)
    floor_ids: list[UUID] = Field(default_factory=list, max_length=500)
    cohort_ids: list[UUID] = Field(default_factory=list, max_length=100)
    person_types: list[ResidentPersonType] = Field(default_factory=list, max_length=11)
    purpose: str = Field(min_length=2, max_length=200)
    corrects_batch_id: UUID | None = None

    _safe_purpose = field_validator("purpose")(_reject_sensitive_business_text)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.range_start >= self.range_end:
            raise ValueError("report range must be half-open and non-empty")
        for field_name in ("building_ids", "floor_ids", "cohort_ids", "person_types"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} contains duplicates")
        if self.scope_mode == "authorized" and (
            self.building_ids or self.floor_ids
        ):
            raise ValueError("authorized scope cannot include explicit IDs")
        if self.scope_mode == "buildings" and (
            not self.building_ids or self.floor_ids
        ):
            raise ValueError("building scope requires only building IDs")
        if self.scope_mode == "floors" and (
            not self.floor_ids or self.building_ids
        ):
            raise ValueError("floor scope requires only floor IDs")
        return self


class MarkHousingReportSubmittedRequest(HousingReportInputModel):
    submitted_at: AwareDatetime
    note: str | None = Field(default=None, max_length=500)
    version: StrictInt = Field(ge=1)

    @field_validator("note", mode="before")
    @classmethod
    def normalize_blank_note(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    _safe_note = field_validator("note")(_reject_sensitive_business_text)


class VoidHousingReportRequest(HousingReportInputModel):
    reason: str = Field(min_length=2, max_length=500)
    version: StrictInt = Field(ge=1)

    _safe_reason = field_validator("reason")(_reject_sensitive_business_text)


class HousingReportResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HousingReportBatchActionsResponse(HousingReportResponseModel):
    download: bool
    mark_submitted: bool
    void: bool
    correct: bool


class HousingReportBatchScopeResponse(HousingReportResponseModel):
    building_id: UUID
    building_name: str
    floor_id: UUID
    floor_name: str


class HousingReportBuildingOptionResponse(HousingReportResponseModel):
    id: UUID
    name: str


class HousingReportFloorOptionResponse(HousingReportResponseModel):
    id: UUID
    building_id: UUID
    name: str


class HousingReportCohortOptionResponse(HousingReportResponseModel):
    id: UUID
    name: str


class HousingReportFilterOptionsResponse(HousingReportResponseModel):
    buildings: tuple[HousingReportBuildingOptionResponse, ...]
    floors: tuple[HousingReportFloorOptionResponse, ...]
    cohorts: tuple[HousingReportCohortOptionResponse, ...]


class HousingReportBatchSummaryResponse(HousingReportResponseModel):
    id: UUID
    batch_number: str
    status: ReportBatchStatus
    version: int
    range_start: AwareDatetime
    range_end: AwareDatetime
    cohort_filter: tuple[UUID, ...]
    person_type_filter: tuple[ResidentPersonType, ...]
    purpose: str
    resident_count: int
    stay_row_count: int
    guardian_row_count: int
    scopes: tuple[HousingReportBatchScopeResponse, ...]
    created_at: AwareDatetime
    created_by_user_id: UUID
    created_by_name: str
    submitted_at: AwareDatetime | None
    submitted_by_user_id: UUID | None
    submitted_by_name: str | None
    voided_at: AwareDatetime | None
    voided_by_user_id: UUID | None
    voided_by_name: str | None
    corrects_batch_id: UUID | None
    failure_code: str | None
    allowed_actions: HousingReportBatchActionsResponse

    _public_purpose = field_validator("purpose")(_public_business_text)


class HousingReportFailureSummaryResponse(HousingReportResponseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    issue_count: int = Field(ge=0)
    field_counts: dict[str, int]


class HousingReportCorrectionLinkResponse(HousingReportResponseModel):
    id: UUID
    batch_number: str
    status: ReportBatchStatus
    created_at: AwareDatetime


class HousingReportBatchDetailResponse(HousingReportBatchSummaryResponse):
    submission_note: str | None
    void_reason: str | None
    failure_summary: HousingReportFailureSummaryResponse | None
    correction_chain: tuple[HousingReportCorrectionLinkResponse, ...]

    _public_detail_text = field_validator("submission_note", "void_reason")(
        _public_business_text
    )


class HousingReportStatusCountsResponse(HousingReportResponseModel):
    total: int = Field(ge=0)
    generated: int = Field(ge=0)
    marked_submitted: int = Field(ge=0)
    voided: int = Field(ge=0)
    failed: int = Field(ge=0)


class HousingReportBatchPageResponse(HousingReportResponseModel):
    items: tuple[HousingReportBatchSummaryResponse, ...]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    status_counts: HousingReportStatusCountsResponse
    access_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    filter_options: HousingReportFilterOptionsResponse
