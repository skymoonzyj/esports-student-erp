from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HousingRoomStatusExportRequest(BaseModel):
    """Unpaged current-room snapshot filters, independent of historical exports."""

    format: Literal["xlsx", "csv"] = "xlsx"
    purpose: str = Field(min_length=2, max_length=200)
    building_ids: list[UUID] = Field(default_factory=list)
    floor_ids: list[UUID] = Field(default_factory=list)
    query: str | None = Field(default=None, max_length=100)
    room_types: list[Literal["single", "double", "triple", "custom", "functional"]] = Field(
        default_factory=list
    )
    verification_statuses: list[Literal["pending_verification", "verified"]] = Field(
        default_factory=list
    )
    service_statuses: list[Literal["available", "problem", "maintenance", "disabled"]] = Field(
        default_factory=list
    )
    current_gender: Literal["male", "female", "unspecified"] | None = None
    occupancy: Literal["occupied", "unoccupied"] | None = None
    assignability: Literal["assignable", "unassignable"] | None = None
    cleaning_statuses: list[Literal["ready", "pending"]] = Field(default_factory=list)
    daily_cleaning_enabled: bool | None = None

    @model_validator(mode="after")
    def normalize_filters(self) -> Self:
        for field in (
            "building_ids",
            "floor_ids",
            "room_types",
            "verification_statuses",
            "service_statuses",
            "cleaning_statuses",
        ):
            setattr(self, field, list(dict.fromkeys(getattr(self, field))))
        self.query = self.query.strip() or None if self.query is not None else None
        return self

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
