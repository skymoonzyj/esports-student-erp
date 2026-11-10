from __future__ import annotations

from datetime import date
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoomCardSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issued_on: date | None
    expires_on: date | None
    expected_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if (self.issued_on is None) != (self.expires_on is None):
            raise ValueError("开卡日和到期日必须同时填写或同时清空")
        if (
            self.issued_on is not None
            and self.expires_on is not None
            and self.expires_on < self.issued_on
        ):
            raise ValueError("到期日不能早于开卡日")
        return self


class RoomCardItem(BaseModel):
    stay_id: UUID
    person_id: UUID
    person_name: str
    nickname: str | None
    issued_on: date | None
    expires_on: date | None
    version: int
    can_edit: bool


class RoomCardList(BaseModel):
    items: list[RoomCardItem]
