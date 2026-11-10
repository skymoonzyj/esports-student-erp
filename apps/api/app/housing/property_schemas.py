from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.housing.schemas import RoomType, validate_room_type_capacity


class PropertyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PropertyChanges(PropertyInput):
    service_status: Literal["available", "problem", "maintenance", "disabled"] | None = None
    room_type: RoomType | None = None
    capacity: StrictInt | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("至少选择一项变更")
        for key in self.model_fields_set - {"notes"}:
            if getattr(self, key) is None:
                raise ValueError("变更值不能为空")
        if self.room_type is not None and self.capacity is not None:
            validate_room_type_capacity(self.room_type, self.capacity)
        return self


class PropertyChangeCreate(PropertyInput):
    expected_version: StrictInt = Field(ge=1)
    changes: PropertyChanges
    reason: str = Field(min_length=1, max_length=500)


class PropertyReview(PropertyInput):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def reject_requires_note(self) -> Self:
        if self.decision == "reject" and not self.note:
            raise ValueError("驳回必须填写原因")
        return self


class PropertyChangeItem(BaseModel):
    id: UUID
    room_id: UUID
    room_number: str
    building_name: str
    base_version: int
    changes: dict[str, Any]
    before: dict[str, Any]
    reason: str
    status: str
    submitted_by_name: str
    submitted_at: datetime
    reviewed_at: datetime | None
    review_note: str | None
    can_review: bool
    can_withdraw: bool


class PropertyChangeList(BaseModel):
    items: list[PropertyChangeItem]
    total: int
    page: int
    page_size: int


class PropertyExportRequest(PropertyInput):
    kind: Literal["occupants", "problems"]
    format: Literal["xlsx", "csv"] = "xlsx"
    purpose: str = Field(min_length=2, max_length=200)
    building_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("building_ids")
    @classmethod
    def unique_buildings(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))
