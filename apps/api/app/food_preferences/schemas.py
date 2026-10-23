from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from app.food_preferences.normalization import (
    normalize_food_display,
    normalize_food_key,
)

FoodPopulation = Literal["students", "all_people"]
FoodExportFormat = Literal["xlsx", "csv_zip"]
FoodPersonType = Literal["student", "guardian", "staff", "visitor"]
FoodPersonStatus = Literal["active", "draft", "inactive", "deleted"]
ProfileAllowedAction = Literal["update"]
CatalogAllowedAction = Literal["create", "update", "disable"]
AccessRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _normalize_food_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    return normalize_food_display(value)


def _normalize_optional_food_text(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    if not value.strip():
        return None
    return normalize_food_display(value)


def _normalize_food_texts(value: object) -> object:
    if isinstance(value, (str, bytes)) or value is None:
        return value
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        return value
    return tuple(normalize_food_display(item) for item in value)


class FoodPreferenceInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FoodPreferenceResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, strict=True)


class CuisineSelectionInput(FoodPreferenceInputModel):
    cuisine_option_id: UUID = Field(strict=False)
    custom_name: str | None = None

    _normalize_custom_name = field_validator("custom_name", mode="before")(
        _normalize_optional_food_text
    )


class FoodPreferenceWriteRequest(FoodPreferenceInputModel):
    expected_version: StrictInt = Field(ge=0)
    cuisines: tuple[CuisineSelectionInput, ...] = Field(
        default=(), max_length=3, strict=False
    )
    dishes: tuple[str, ...] = Field(default=(), max_length=3, strict=False)

    _normalize_dishes = field_validator("dishes", mode="before")(_normalize_food_texts)

    @model_validator(mode="after")
    def reject_duplicates(self) -> Self:
        custom_cuisine_keys = [
            normalize_food_key(selection.custom_name)
            for selection in self.cuisines
            if selection.custom_name is not None
        ]
        if len(custom_cuisine_keys) != len(set(custom_cuisine_keys)):
            raise ValueError("custom cuisine names must be unique")
        dish_keys = [normalize_food_key(dish) for dish in self.dishes]
        if len(dish_keys) != len(set(dish_keys)):
            raise ValueError("dish names must be unique")
        return self


class CuisineCreateRequest(FoodPreferenceInputModel):
    name: str = Field(min_length=1, max_length=40)
    sort_order: StrictInt = Field(default=0, ge=0, le=100_000)

    _normalize_name = field_validator("name", mode="before")(_normalize_food_text)


class CuisinePatchRequest(FoodPreferenceInputModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    sort_order: StrictInt | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None

    _normalize_name = field_validator("name", mode="before")(_normalize_food_text)

    @model_validator(mode="after")
    def require_mutation(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("cuisine patch requires at least one field")
        return self


class FoodPreferenceExportRequest(FoodPreferenceInputModel):
    population: FoodPopulation
    include_inactive: bool = False
    format: FoodExportFormat
    purpose: str = Field(min_length=4, max_length=200)

    @field_validator("purpose", mode="before")
    @classmethod
    def normalize_purpose(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return normalize_food_display(value, max_length=200)


class CuisineOptionResponse(FoodPreferenceResponseModel):
    id: UUID
    name: str
    sort_order: StrictInt = Field(ge=0)
    is_active: bool
    is_other: bool


class CuisineCatalogResponse(FoodPreferenceResponseModel):
    items: tuple[CuisineOptionResponse, ...]
    allowed_actions: tuple[CatalogAllowedAction, ...]
    access_revision: AccessRevision


class FoodPreferenceCuisineResponse(FoodPreferenceResponseModel):
    cuisine_option_id: UUID
    name: str
    custom_name: str | None


class FoodPreferenceProfileResponse(FoodPreferenceResponseModel):
    person_id: UUID
    version: StrictInt = Field(ge=0)
    cuisines: tuple[FoodPreferenceCuisineResponse, ...]
    dishes: tuple[str, ...]
    updated_at: AwareDatetime | None
    allowed_actions: tuple[ProfileAllowedAction, ...]
    access_revision: AccessRevision


class FoodPreferenceSelfResponse(FoodPreferenceResponseModel):
    profile: FoodPreferenceProfileResponse
    onboarding_required: bool
    onboarding_completed_at: AwareDatetime | None


class DishSuggestionResponse(FoodPreferenceResponseModel):
    name: str


class DishSuggestionPageResponse(FoodPreferenceResponseModel):
    items: tuple[DishSuggestionResponse, ...]
    access_revision: AccessRevision


class FoodPreferenceRankResponse(FoodPreferenceResponseModel):
    label: str
    count: StrictInt = Field(ge=0)
    ratio: FiniteFloat = Field(ge=0, le=1)


class FoodPreferencePersonResponse(FoodPreferenceResponseModel):
    person_id: UUID
    full_name: str
    person_types: tuple[FoodPersonType, ...]
    status: FoodPersonStatus
    cuisines: tuple[str, ...]
    dishes: tuple[str, ...]
    updated_at: AwareDatetime | None


class FoodPreferencePersonPageResponse(FoodPreferenceResponseModel):
    items: tuple[FoodPreferencePersonResponse, ...]
    page: StrictInt = Field(ge=1)
    page_size: StrictInt = Field(ge=1, le=100)
    total: StrictInt = Field(ge=0)


class FoodPreferenceStatisticsResponse(FoodPreferenceResponseModel):
    population: FoodPopulation
    include_inactive: bool
    total_people: StrictInt = Field(ge=0)
    filled_people: StrictInt = Field(ge=0)
    unfilled_people: StrictInt = Field(ge=0)
    filled_ratio: FiniteFloat = Field(ge=0, le=1)
    cuisines: tuple[FoodPreferenceRankResponse, ...]
    dishes: tuple[FoodPreferenceRankResponse, ...]
    people: FoodPreferencePersonPageResponse
    access_revision: AccessRevision


# Stable short aliases for callers that use the domain names rather than the
# HTTP response suffix.  They refer to the same closed models and add no fields.
CuisineCatalogEnvelope = CuisineCatalogResponse
FoodPreferenceCuisine = FoodPreferenceCuisineResponse
FoodPreferenceProfile = FoodPreferenceProfileResponse
DishSuggestion = DishSuggestionResponse
FoodPreferenceRank = FoodPreferenceRankResponse
FoodPreferencePerson = FoodPreferencePersonResponse
FoodPreferencePersonPage = FoodPreferencePersonPageResponse
FoodPreferenceStatistics = FoodPreferenceStatisticsResponse
FoodPreferenceStatisticsPage = FoodPreferencePersonPageResponse
FoodPreferenceStatisticsPageResponse = FoodPreferencePersonPageResponse
