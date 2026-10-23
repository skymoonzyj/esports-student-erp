from __future__ import annotations

from datetime import date
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)


class TeachingReportDraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enrollment_id: UUID
    report_kind: Literal["daily", "weekly"]
    period_start_date: date
    body: str = Field(min_length=1, max_length=5000)
    dimension_values: dict[str, Any] = Field(default_factory=dict)
    expected_draft_revision: StrictInt = Field(ge=0)

    @field_validator("body", mode="before")
    @classmethod
    def trim_body(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("dimension_values")
    @classmethod
    def require_reserved_dimensions_to_be_empty(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        if value:
            raise ValueError("dimension_values is reserved for a future version")
        return value


class TeachingReportSubmitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lock_version: StrictInt = Field(ge=1)
    expected_draft_revision: StrictInt = Field(ge=1)


class TeachingReportReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lock_version: StrictInt = Field(ge=1)
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_decision_reason(self) -> Self:
        if self.decision == "approved" and self.reason is not None:
            raise ValueError("approved reports cannot include a rejection reason")
        if self.decision == "rejected" and not self.reason:
            raise ValueError("rejected reports require a reason")
        return self


class TeachingReportDraftStartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lock_version: StrictInt = Field(ge=1)


class TeachingReportReopenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lock_version: StrictInt = Field(ge=1)
    grant_kind: Literal["correction", "revision"]
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reopen_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
