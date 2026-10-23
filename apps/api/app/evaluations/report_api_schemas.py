from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TeachingReportKind = Literal["daily", "weekly"]
TeachingReportStatus = Literal[
    "draft",
    "pending_review",
    "rejected",
    "correction_draft",
    "published",
    "revision_pending_review",
]
TeachingReportVersionSource = Literal["initial", "correction", "revision"]
TeachingReportAction = Literal[
    "save_draft",
    "submit",
    "start_correction",
    "start_revision",
]


class TeachingReportContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author_available: bool
    reviewer_available: bool
    family_available: bool


class TeachingReportTargetItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    enrollment_id: UUID
    student_person_id: UUID
    student_name: str
    student_number: str
    cohort_id: UUID
    cohort_name: str
    cohort_starts_at: datetime
    cohort_ends_at: datetime
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str


class TeachingReportCohortFacetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    name: str
    starts_at: datetime
    ends_at: datetime


class TeachingReportProjectFacetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    name: str
    cohort_ids: list[UUID]


class TeachingReportClassFacetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    name: str
    cohort_id: UUID
    project_id: UUID


class TeachingReportTargetFacetsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    cohorts: list[TeachingReportCohortFacetResponse]
    projects: list[TeachingReportProjectFacetResponse]
    classes: list[TeachingReportClassFacetResponse]


class TeachingReportTargetPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    items: list[TeachingReportTargetItemResponse]
    facets: TeachingReportTargetFacetsResponse
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class TeachingReportVersionReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)
    reviewed_at: datetime

    @model_validator(mode="after")
    def require_decision_reason_pair(self) -> Self:
        if self.decision == "approved" and self.reason is not None:
            raise ValueError("approved review reason must be null")
        if self.decision == "rejected" and (
            self.reason is None or not self.reason.strip()
        ):
            raise ValueError("rejected review reason is required")
        return self


class TeachingReportVersionItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    report_id: UUID
    version_id: UUID
    version: int = Field(ge=1)
    source: TeachingReportVersionSource
    body: str = Field(min_length=1, max_length=5000)
    dimension_values: dict[str, Any]
    submitted_at: datetime
    is_current: bool
    is_published: bool
    review: TeachingReportVersionReviewResponse | None

    @field_validator("dimension_values")
    @classmethod
    def require_empty_dimensions(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value:
            raise ValueError("dimension_values must be empty")
        return value


class TeachingReportVersionPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    items: list[TeachingReportVersionItemResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class TeachingReportSlotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enrollment_id: UUID
    student_person_id: UUID
    cohort_id: UUID
    cohort_name: str
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str
    report_kind: TeachingReportKind
    period_sequence: int = Field(ge=1)
    period_start_date: date
    period_end_date: date
    submission_opens_at: datetime
    initial_submission_deadline: datetime
    report_id: UUID | None
    status: TeachingReportStatus | None
    lock_version: int | None = Field(default=None, ge=1)
    draft_body: str | None
    draft_dimension_values: dict[str, Any] | None
    draft_revision: int | None = Field(default=None, ge=1)
    allowed_actions: list[TeachingReportAction]


class TeachingReportSlotListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TeachingReportSlotResponse]


class TeachingReportPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TeachingReportSlotResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class TeachingReportReviewQueueItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    report_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    cohort_name: str
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str
    report_kind: TeachingReportKind
    period_sequence: int = Field(ge=1)
    period_start_date: date
    period_end_date: date
    status: TeachingReportStatus
    lock_version: int = Field(ge=1)
    author_name: str
    role_name: str
    version_id: UUID
    version: int = Field(ge=1)
    source: TeachingReportVersionSource
    body: str
    dimension_values: dict[str, Any]
    submitted_at: datetime


class TeachingReportReviewQueuePageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    items: list[TeachingReportReviewQueueItemResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class TeachingReportPublishedItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    cohort_name: str
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str
    report_kind: TeachingReportKind
    period_sequence: int = Field(ge=1)
    period_start_date: date
    period_end_date: date
    author_name: str
    role_name: str
    version_id: UUID
    version: int = Field(ge=1)
    body: str
    dimension_values: dict[str, Any]
    published_at: datetime


class TeachingReportPublishedPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TeachingReportPublishedItemResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class TeachingReportDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: UUID
    status: TeachingReportStatus
    lock_version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)
    body: str
    dimension_values: dict[str, Any]
    idempotent: bool
    allowed_actions: list[TeachingReportAction]


class TeachingReportDraftStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: UUID
    status: TeachingReportStatus
    lock_version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)
    body: str
    dimension_values: dict[str, Any]
    idempotent: bool


class TeachingReportSubmitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: UUID
    status: TeachingReportStatus
    lock_version: int = Field(ge=1)
    version_id: UUID
    version: int = Field(ge=1)
    source: TeachingReportVersionSource
    published_version_id: UUID | None
    idempotent: bool


class TeachingReportReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: UUID
    status: TeachingReportStatus
    lock_version: int = Field(ge=1)
    published_version_id: UUID | None
    review_id: UUID
    decision: Literal["approved", "rejected"]
    idempotent: bool


class TeachingReportReopenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: UUID
    status: TeachingReportStatus
    lock_version: int = Field(ge=1)
    grant_id: UUID
    grant_kind: Literal["correction", "revision"]
    opens_at: datetime
    expires_at: datetime
    idempotent: bool


class TeachingReportProblemFieldError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    message: str


class TeachingReportProblemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["about:blank"]
    title: str
    status: int
    detail: str
    code: str
    request_id: str | None = None
    errors: list[TeachingReportProblemFieldError] | None = None
