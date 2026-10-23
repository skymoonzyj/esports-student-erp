"""Exact-person housing views, intentionally excluding room occupants and history."""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.housing.schemas import (
    AccessRevision,
    ResidentOption,
    StayDurationMode,
    StayPurpose,
    StayStatus,
    StaySummary,
)


class PersonHousingStay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    building_name: str
    floor_name: str
    room_name: str
    stay_purpose: StayPurpose
    duration_mode: StayDurationMode
    planned_check_in_at: AwareDatetime
    planned_check_out_at: AwareDatetime | None
    actual_check_in_at: AwareDatetime | None
    status: StayStatus


class PersonHousingActions(BaseModel):
    create_stay: bool = False


class PersonHousingResponse(BaseModel):
    access_revision: AccessRevision
    housing_access_revision: AccessRevision
    current: PersonHousingStay | None
    planned: PersonHousingStay | None
    management_stay: StaySummary | None
    allowed_actions: PersonHousingActions


class PersonCheckInRoom(BaseModel):
    id: UUID
    room_number: str
    building_name: str
    floor_name: str
    allowed_actions: PersonHousingActions


class PersonCheckInOptions(BaseModel):
    access_revision: AccessRevision
    housing_access_revision: AccessRevision
    items: list[PersonCheckInRoom]
    resident: ResidentOption
