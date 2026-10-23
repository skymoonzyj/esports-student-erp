"""Closed request contracts for camp evaluation surveys."""

from __future__ import annotations

from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


def _trim_optional(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    return trimmed or None


def _trim_required(value: object) -> object:
    if not isinstance(value, str):
        return value
    return value.strip()


def _trim_dishes(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return tuple(
            item.strip() if isinstance(item, str) else item
            for item in value
        )
    return value


class CampSurveyInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CampSurveyStaffRatingInput(CampSurveyInputModel):
    staff_snapshot_id: UUID = Field(strict=False)
    score: StrictInt = Field(ge=1, le=10)
    reason: str | None = Field(default=None, max_length=2000)
    suggestion: str | None = Field(default=None, max_length=2000)

    _trim_reason = field_validator("reason", mode="before")(_trim_optional)
    _trim_suggestion = field_validator("suggestion", mode="before")(_trim_optional)


class CampSurveyCuisineSnapshotInput(CampSurveyInputModel):
    """Closed snapshot of one catalog cuisine used by a camp survey."""

    cuisine_option_id: UUID = Field(strict=False)
    name: str = Field(min_length=1, max_length=40)
    custom_name: str | None = Field(default=None, max_length=40)

    _trim_name = field_validator("name", mode="before")(_trim_required)
    _trim_custom_name = field_validator("custom_name", mode="before")(
        _trim_optional
    )


CampSurveyDishName = Annotated[str, Field(min_length=1, max_length=40)]


class CampSurveySubmissionInput(CampSurveyInputModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    expected_snapshot_version: StrictInt = Field(ge=1)
    expected_food_preference_version: StrictInt = Field(ge=0)
    favorite_cuisines: tuple[CampSurveyCuisineSnapshotInput, ...] = Field(
        min_length=1,
        max_length=3,
        strict=False,
    )
    favorite_dishes: tuple[CampSurveyDishName, ...] = Field(
        min_length=1,
        max_length=3,
        strict=False,
    )
    course_training_score: StrictInt = Field(ge=1, le=10)
    course_training_reason: str | None = Field(default=None, max_length=2000)
    life_service_score: StrictInt = Field(ge=1, le=10)
    life_service_reason: str | None = Field(default=None, max_length=2000)
    service_management_score: StrictInt = Field(ge=1, le=10)
    service_management_reason: str | None = Field(default=None, max_length=2000)
    overall_suggestion: str | None = Field(default=None, max_length=2000)
    staff_ratings: tuple[CampSurveyStaffRatingInput, ...] = Field(
        min_length=1,
        max_length=200,
        strict=False,
    )

    _trim_course_reason = field_validator("course_training_reason", mode="before")(
        _trim_optional
    )
    _trim_life_reason = field_validator("life_service_reason", mode="before")(
        _trim_optional
    )
    _trim_service_reason = field_validator("service_management_reason", mode="before")(
        _trim_optional
    )
    _trim_suggestion = field_validator("overall_suggestion", mode="before")(
        _trim_optional
    )
    _trim_favorite_dishes = field_validator("favorite_dishes", mode="before")(
        _trim_dishes
    )

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def trim_idempotency_key(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_unique_staff_snapshots(self) -> Self:
        ids = [item.staff_snapshot_id for item in self.staff_ratings]
        if len(ids) != len(set(ids)):
            raise ValueError("staff snapshot IDs must be unique")
        return self


class CampSurveyRevisionInput(CampSurveySubmissionInput):
    """A revision has the same complete payload and a distinct route resource."""


class CampSurveySupplementInput(CampSurveyInputModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    expected_snapshot_version: StrictInt = Field(ge=1)
    change_reason: str = Field(
        default="新增工作人员评价",
        min_length=1,
        max_length=500,
    )
    staff_ratings: tuple[CampSurveyStaffRatingInput, ...] = Field(
        min_length=1,
        max_length=200,
        strict=False,
    )

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def trim_idempotency_key(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    _trim_change_reason = field_validator("change_reason", mode="before")(
        _trim_required
    )

    @model_validator(mode="after")
    def require_unique_staff_snapshots(self) -> Self:
        ids = [item.staff_snapshot_id for item in self.staff_ratings]
        if len(ids) != len(set(ids)):
            raise ValueError("staff snapshot IDs must be unique")
        return self
