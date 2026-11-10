"""Minimal, source-discriminated departure reminder response contracts."""

from datetime import date as Date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.portal.departure_dates import DepartureWindow

_UUID_PATH = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
HousingDetailPath = Annotated[str, Field(pattern=rf"^/housing/rooms/{_UUID_PATH}$")]
EnrollmentDetailPath = Annotated[str, Field(pattern=rf"^/teaching/enrollments/{_UUID_PATH}$")]


class _DepartureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannedCheckoutEvent(_DepartureModel):
    kind: Literal["planned_checkout"]
    source_id: UUID
    date: Date
    detail_path: HousingDetailPath | None = None
    building_name: str
    room_number: str


class EnrollmentEndEvent(_DepartureModel):
    kind: Literal["enrollment_end"]
    source_id: UUID
    date: Date
    detail_path: EnrollmentDetailPath | None = None
    project_name: str
    cohort_name: str
    enrollment_status: Literal["active", "completed"]


DepartureEvent = Annotated[PlannedCheckoutEvent | EnrollmentEndEvent, Field(discriminator="kind")]


class DeparturePerson(_DepartureModel):
    person_id: UUID
    full_name: str
    nickname: str | None
    events: list[DepartureEvent]


class DepartureRemindersResponse(_DepartureModel):
    available: bool
    observed_at: AwareDatetime
    business_date: Date
    timezone: Literal["Asia/Shanghai"]
    next_refresh_at: AwareDatetime
    window: DepartureWindow
    today_count: int = Field(ge=0)
    three_day_count: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    total: int = Field(ge=0)
    items: list[DeparturePerson]

    @model_validator(mode="after")
    def unavailable_has_no_people(self) -> Self:
        if not self.available and (
            self.today_count or self.three_day_count or self.total or self.items
        ):
            raise ValueError("unavailable departure reminders must have no people or counts")
        return self
