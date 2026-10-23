from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.teaching.state import AutomationIssue, CohortStatus, TransitionTrigger


class TeachingInputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ProjectCreate(TeachingInputModel):
    name: str = Field(min_length=2, max_length=80)
    sort_order: int = Field(default=0, ge=0, le=100_000)


class ProjectUpdate(TeachingInputModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None


class ProfessionalTeamCreate(TeachingInputModel):
    project_id: UUID
    name: str = Field(min_length=2, max_length=120)
    notes: str | None = Field(default=None, max_length=500)


class ProfessionalTeamUpdate(TeachingInputModel):
    project_id: UUID | None = None
    name: str | None = Field(default=None, min_length=2, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class CohortCreate(TeachingInputModel):
    name: str = Field(min_length=2, max_length=120)
    starts_at: datetime
    ends_at: datetime
    default_check_in_at: datetime
    default_check_out_at: datetime
    auto_start_enabled: bool = False
    auto_complete_enabled: bool = False

    @field_validator(
        "starts_at",
        "ends_at",
        "default_check_in_at",
        "default_check_out_at",
    )
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cohort datetimes must include an offset")
        return value

    @model_validator(mode="after")
    def validate_schedule(self) -> CohortCreate:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.default_check_out_at < self.default_check_in_at:
            raise ValueError("default_check_out_at must not precede default_check_in_at")
        return self


class CohortUpdate(TeachingInputModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    default_check_in_at: datetime | None = None
    default_check_out_at: datetime | None = None
    auto_start_enabled: bool | None = None
    auto_complete_enabled: bool | None = None

    @field_validator(
        "starts_at",
        "ends_at",
        "default_check_in_at",
        "default_check_out_at",
    )
    @classmethod
    def require_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("cohort datetimes must include an offset")
        return value


class CohortTransitionRequest(TeachingInputModel):
    target: CohortStatus


class TeachingClassCreate(TeachingInputModel):
    cohort_id: UUID
    project_id: UUID
    name: str = Field(min_length=2, max_length=120)
    class_type: Literal["standard", "elite"]
    professional_team_id: UUID | None = None
    capacity: StrictInt = Field(ge=1, le=10_000)
    default_tuition_cents: StrictInt = Field(ge=0, le=1_000_000_000)


class TeachingClassUpdate(TeachingInputModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    class_type: Literal["standard", "elite"] | None = None
    professional_team_id: UUID | None = None
    capacity: StrictInt | None = Field(default=None, ge=1, le=10_000)
    default_tuition_cents: StrictInt | None = Field(
        default=None,
        ge=0,
        le=1_000_000_000,
    )
    is_active: bool | None = None


class CoachAssignmentCreate(TeachingInputModel):
    coach_person_id: UUID
    assignment_type: Literal["primary", "assistant"]
    valid_from: datetime
    valid_until: datetime | None = None

    @field_validator("valid_from", "valid_until")
    @classmethod
    def require_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("assignment datetimes must include an offset")
        return value

    @model_validator(mode="after")
    def validate_validity(self) -> CoachAssignmentCreate:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        return self


class CoachAssignmentEndRequest(TeachingInputModel):
    reason: str = Field(min_length=2, max_length=200)


class TeachingResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectResponse(TeachingResponseModel):
    id: UUID
    name: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProfessionalTeamResponse(TeachingResponseModel):
    id: UUID
    project_id: UUID
    name: str
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CohortResponse(TeachingResponseModel):
    id: UUID
    name: str
    starts_at: datetime
    ends_at: datetime
    default_check_in_at: datetime
    default_check_out_at: datetime
    status: CohortStatus
    auto_start_enabled: bool
    auto_complete_enabled: bool
    automation_issues: list[AutomationIssue]
    enabled_class_count: int
    missing_primary_class_count: int
    created_at: datetime
    updated_at: datetime


class CohortStatusEventResponse(TeachingResponseModel):
    id: UUID
    cohort_id: UUID
    old_status: CohortStatus
    new_status: CohortStatus
    trigger: TransitionTrigger
    actor_user_id: UUID | None
    request_id: str
    occurred_at: datetime


class TeachingClassResponse(TeachingResponseModel):
    id: UUID
    cohort_id: UUID
    project_id: UUID
    name: str
    class_type: Literal["standard", "elite"]
    professional_team_id: UUID | None
    capacity: int
    default_tuition_cents: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CoachAssignmentResponse(TeachingResponseModel):
    id: UUID
    class_id: UUID
    coach_person_id: UUID
    coach_full_name: str
    assignment_type: Literal["primary", "assistant"]
    valid_from: datetime
    valid_until: datetime | None
    ended_at: datetime | None
    ended_by_user_id: UUID | None
    end_reason: str | None
    created_at: datetime
    updated_at: datetime


class EligibleCoachResponse(TeachingResponseModel):
    id: UUID
    full_name: str


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int
