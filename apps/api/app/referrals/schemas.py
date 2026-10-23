from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

InvitedHousingStatus = Literal[
    "unarranged", "reserved", "check_in_pending", "checked_in", "checkout_pending", "checked_out"
]
SourceKind = Literal["all", "person", "organization"]


class PrivateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvitationSource(PrivateDto):
    source_kind: Literal["person", "organization"]
    organization_id: UUID | None
    name: str


class InvitationSourcesResponse(PrivateDto):
    sources: list[InvitationSource]


class InvitedHousingStay(PrivateDto):
    building_name: str
    floor_name: str
    room_name: str
    duration_mode: Literal["long_term", "fixed_term"]
    planned_check_in_at: AwareDatetime
    planned_check_out_at: AwareDatetime | None
    actual_check_in_at: AwareDatetime | None
    actual_check_out_at: AwareDatetime | None


class InvitedHousingSummary(PrivateDto):
    status: InvitedHousingStatus
    stay: InvitedHousingStay | None
    next_planned_stay: InvitedHousingStay | None


class InvitedPersonHousing(PrivateDto):
    person_id: UUID
    full_name: str
    nickname: str | None
    source: InvitationSource
    housing: InvitedHousingSummary


class InvitedHousingPage(PrivateDto):
    as_of: datetime
    items: list[InvitedPersonHousing]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
