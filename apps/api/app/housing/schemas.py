from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.housing.room_status_export_schemas import (
    HousingRoomStatusExportRequest as HousingRoomStatusExportRequest,  # noqa: PLC0414
)

RoomType = Literal["single", "double", "triple", "custom", "functional"]
VerificationStatus = Literal["pending_verification", "verified"]
ServiceStatus = Literal["available", "problem", "maintenance", "disabled"]
CleaningStatus = Literal["ready", "pending"]
StayPurpose = Literal["permanent", "camp", "temporary"]
StayDurationMode = Literal["long_term", "fixed_term"]
StayStatus = Literal[
    "planned",
    "checked_in",
    "checkout_pending",
    "checked_out",
    "cancelled",
]
PortalStayStatus = Literal["planned", "checked_in", "checkout_pending"]
ResidentPersonType = Literal[
    "student",
    "player",
    "student_player",
    "coach",
    "life_teacher",
    "customer_service",
    "administration",
    "other",
    "visitor",
]
Sex = Literal["male", "female"]
CohortStatus = Literal["draft", "open", "upcoming", "active", "completed", "archived"]
AccessRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def validate_room_type_capacity(room_type: RoomType, capacity: int) -> None:
    expected = {"single": 1, "double": 2, "triple": 3, "functional": 0}
    if room_type == "custom":
        if not 1 <= capacity <= 100:
            raise ValueError("custom room capacity must be between 1 and 100")
        return
    if capacity != expected[room_type]:
        raise ValueError(f"{room_type} room capacity is invalid")


class HousingInputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("notes", "visit_reason", mode="before", check_fields=False)
    @classmethod
    def normalize_blank_optional_text(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class HousingOccupancyExportRequest(HousingInputModel):
    """Filters and audit context for the sensitive room-state export."""

    format: Literal["xlsx", "csv"] = "xlsx"
    purpose: str = Field(min_length=2, max_length=200)
    building_id: UUID | None = None
    floor_id: UUID | None = None
    query: str | None = Field(default=None, max_length=100)
    room_types: list[RoomType] = Field(default_factory=list, max_length=5)
    verification_statuses: list[VerificationStatus] = Field(
        default_factory=list,
        max_length=2,
    )
    service_statuses: list[ServiceStatus] = Field(default_factory=list, max_length=4)
    current_gender: Literal["male", "female", "unspecified"] | None = None
    occupancy: Literal["occupied", "unoccupied"] | None = None
    assignability: Literal["assignable", "unassignable"] | None = None

    @model_validator(mode="after")
    def normalize_filters(self) -> Self:
        if len(set(self.room_types)) != len(self.room_types):
            raise ValueError("room_types must be unique")
        if len(set(self.verification_statuses)) != len(self.verification_statuses):
            raise ValueError("verification_statuses must be unique")
        if len(set(self.service_statuses)) != len(self.service_statuses):
            raise ValueError("service_statuses must be unique")
        if self.query is not None:
            normalized_query = self.query.strip()
            self.query = normalized_query or None
        self.purpose = self.purpose.strip()
        return self


class HousingResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BuildingCreate(HousingInputModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=80)
    display_order: StrictInt = Field(default=0, ge=0, le=100_000)
    notes: str | None = Field(default=None, max_length=500)


class BuildingUpdate(HousingInputModel):
    code: str | None = Field(default=None, min_length=1, max_length=16)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    display_order: StrictInt | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_mutation(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("building update requires at least one field")
        return self


class FloorCreate(HousingInputModel):
    building_id: UUID
    number: StrictInt = Field(ge=-20, le=200)
    name: str = Field(min_length=1, max_length=40)
    display_order: StrictInt = Field(default=0, ge=0, le=100_000)
    notes: str | None = Field(default=None, max_length=500)


class FloorUpdate(HousingInputModel):
    number: StrictInt | None = Field(default=None, ge=-20, le=200)
    name: str | None = Field(default=None, min_length=1, max_length=40)
    display_order: StrictInt | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_mutation(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("floor update requires at least one field")
        return self


class RoomCreate(HousingInputModel):
    floor_id: UUID
    room_number: str = Field(min_length=1, max_length=32)
    room_type: RoomType
    capacity: StrictInt = Field(ge=0, le=100)
    daily_cleaning_enabled: bool = False
    area_square_meters: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=8,
        decimal_places=2,
    )
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        validate_room_type_capacity(self.room_type, self.capacity)
        return self


class RoomUpdate(HousingInputModel):
    version: StrictInt = Field(ge=1)
    floor_id: UUID | None = None
    room_number: str | None = Field(default=None, min_length=1, max_length=32)
    room_type: RoomType | None = None
    capacity: StrictInt | None = Field(default=None, ge=0, le=100)
    daily_cleaning_enabled: bool | None = None
    area_square_meters: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=8,
        decimal_places=2,
    )
    service_status: Literal["available", "maintenance", "disabled"] | None = None
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not (self.model_fields_set - {"version"}):
            raise ValueError("room update requires at least one mutable field")
        if self.room_type is not None and self.capacity is not None:
            validate_room_type_capacity(self.room_type, self.capacity)
        return self


class RoomVersionInput(HousingInputModel):
    room_id: UUID
    version: StrictInt = Field(ge=1)


class RoomCleaningComplete(HousingInputModel):
    version: StrictInt = Field(ge=1)


class RoomVerifyRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)


class RoomBatchVerifyRequest(HousingInputModel):
    rooms: Annotated[list[RoomVersionInput], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def require_unique_rooms(self) -> Self:
        room_ids = [item.room_id for item in self.rooms]
        if len(room_ids) != len(set(room_ids)):
            raise ValueError("room IDs must be unique")
        return self


class StayCreate(HousingInputModel):
    person_id: UUID
    stay_purpose: StayPurpose
    duration_mode: StayDurationMode
    cohort_id: UUID | None = None
    visit_reason: str | None = Field(default=None, max_length=500)
    planned_check_in_at: AwareDatetime
    planned_check_out_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_stay_details(self) -> Self:
        if self.duration_mode == "fixed_term":
            if (
                self.planned_check_out_at is None
                or self.planned_check_out_at <= self.planned_check_in_at
            ):
                raise ValueError("fixed-term stay requires a planned checkout after check-in")
        elif self.planned_check_out_at is not None:
            raise ValueError("long-term stay cannot have a planned checkout")
        if self.stay_purpose == "camp" and self.cohort_id is None:
            raise ValueError("camp stay requires a cohort")
        if self.stay_purpose == "temporary" and (
            self.duration_mode != "fixed_term"
            or self.planned_check_out_at is None
            or self.visit_reason is None
        ):
            raise ValueError("temporary stay requires a fixed term and visit reason")
        return self


class StayUpdate(HousingInputModel):
    version: StrictInt = Field(ge=1)
    planned_check_in_at: AwareDatetime
    planned_check_out_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_planned_range(self) -> Self:
        if (
            self.planned_check_out_at is not None
            and self.planned_check_out_at <= self.planned_check_in_at
        ):
            raise ValueError("planned checkout must be after planned check-in")
        return self


class StayPlannedDepartureUpdate(HousingInputModel):
    version: StrictInt = Field(ge=1)
    duration_mode: StayDurationMode
    planned_check_out_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_duration(self) -> Self:
        if (self.duration_mode == "fixed_term") != (self.planned_check_out_at is not None):
            raise ValueError("planned checkout must match the selected duration mode")
        return self


class ConfirmCheckInRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)
    actual_check_in_at: AwareDatetime


class CancelReservationRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class TransferStayRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)
    target_room_id: UUID
    transferred_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=500)


class CheckoutRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)
    actual_check_out_at: AwareDatetime


class CheckoutConfirmation(HousingInputModel):
    version: StrictInt = Field(ge=1)


class RoomIssueCreate(HousingInputModel):
    room_version: StrictInt = Field(ge=1)
    description: str = Field(min_length=1, max_length=1000)


class RoomIssueResolve(HousingInputModel):
    room_version: StrictInt = Field(ge=1)
    resolution_note: str = Field(min_length=1, max_length=1000)


class BuildingResponse(HousingResponseModel):
    id: UUID
    code: str
    name: str
    display_order: int
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class FloorResponse(HousingResponseModel):
    id: UUID
    building_id: UUID
    number: int
    name: str
    display_order: int
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class RoomResponse(HousingResponseModel):
    id: UUID
    building_id: UUID
    floor_id: UUID
    room_number: str
    room_type: RoomType
    capacity: int
    area_square_meters: Decimal | None
    verification_status: VerificationStatus
    service_status: ServiceStatus
    daily_cleaning_enabled: bool = False
    cleaning_status: CleaningStatus = "ready"
    cleaning_pending_at: datetime | None = None
    cleaning_completed_at: datetime | None = None
    photo_count: int = Field(default=0, ge=0, le=3)
    notes: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class ResidentOption(HousingResponseModel):
    person_id: UUID
    full_name: str
    person_type: ResidentPersonType
    sex: Sex | None
    masked_phone: str | None = None
    account_ready: bool
    housing_ready: bool = False
    account_status: Literal["active", "disabled", "deleted", "not_activated"] = "not_activated"
    profile_ready: bool
    guardian_ready: bool


class ResidentOptionsResponse(HousingResponseModel):
    access_revision: AccessRevision
    items: list[ResidentOption]


class CohortStayOption(HousingResponseModel):
    id: UUID
    name: str
    status: CohortStatus
    default_check_in_at: AwareDatetime
    default_check_out_at: AwareDatetime


class CohortOptionsResponse(HousingResponseModel):
    access_revision: AccessRevision
    items: list[CohortStayOption]


class TransferRoomOption(HousingResponseModel):
    room_id: UUID
    building_label: str
    floor_label: str
    room_label: str
    capacity: int = Field(ge=0)
    current_occupants: int = Field(ge=0)
    current_gender: Sex | None
    advisory_compatible: bool
    status_text: str


class TransferRoomOptionsResponse(HousingResponseModel):
    access_revision: AccessRevision
    items: list[TransferRoomOption]


class StayAllowedActions(BaseModel):
    void_history: bool = False
    edit_planned_departure: bool = False
    edit_reservation: bool
    confirm_check_in: bool
    cancel_reservation: bool
    transfer: bool
    request_checkout: bool
    confirm_checkout: bool


class StayVoidInfo(BaseModel):
    voided_at: AwareDatetime
    voided_by_user_id: UUID
    reason: str


class StaySummary(HousingResponseModel):
    void_info: StayVoidInfo | None = None
    id: UUID
    person_id: UUID
    room_id: UUID
    stay_purpose: StayPurpose
    duration_mode: StayDurationMode
    cohort_id: UUID | None
    visit_reason: str | None
    planned_check_in_at: AwareDatetime
    planned_check_out_at: AwareDatetime | None
    actual_check_in_at: AwareDatetime | None
    actual_check_out_at: AwareDatetime | None
    status: StayStatus
    room_name_snapshot: str
    floor_name_snapshot: str
    building_name_snapshot: str
    notes: str | None
    cancellation_reason: str | None
    checkout_requested_actual_at: AwareDatetime | None
    checkout_requested_at: AwareDatetime | None
    person_name: str
    person_type: ResidentPersonType
    sex: Sex | None
    version: int = Field(ge=1)
    allowed_actions: StayAllowedActions
    created_at: AwareDatetime
    updated_at: AwareDatetime


class IssueAllowedActions(BaseModel):
    resolve: bool = False


class RoomIssueSummary(HousingResponseModel):
    id: UUID
    room_id: UUID
    description: str
    previous_service_status: ServiceStatus
    reported_at: AwareDatetime
    status: Literal["open", "resolved"]
    resolved_at: AwareDatetime | None
    resolution_note: str | None
    allowed_actions: IssueAllowedActions = Field(default_factory=IssueAllowedActions)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TransferSummary(HousingResponseModel):
    from_stay_voided: bool = False
    to_stay_voided: bool = False
    id: UUID
    person_id: UUID
    from_stay_id: UUID
    to_stay_id: UUID
    transferred_at: AwareDatetime
    reason: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class HousingPortalResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HousingPortalStay(HousingPortalResponseModel):
    building_name: str
    floor_name: str
    room_name: str
    stay_purpose: StayPurpose
    planned_check_in_at: AwareDatetime
    planned_check_out_at: AwareDatetime | None
    actual_check_in_at: AwareDatetime | None
    actual_check_out_at: AwareDatetime | None
    status: PortalStayStatus
    roommates: list[str] | None


class HousingPortalSubject(HousingPortalResponseModel):
    subject_person_id: UUID
    subject_name: str
    current_stay: HousingPortalStay | None = None
    planned_stays: list[HousingPortalStay] = Field(default_factory=list)


class HousingPortalResponse(HousingPortalResponseModel):
    as_of: AwareDatetime
    subjects: list[HousingPortalSubject]


class HousingAllowedActions(BaseModel):
    manage_care: bool = False
    complete_cleaning: bool = False
    manage_photos: bool = False
    edit: bool
    delete: bool
    verify: bool
    create_stay: bool = False
    report_issue: bool = False
    view_detail: bool = False


class RoomPhotoSummary(HousingResponseModel):
    id: UUID
    slot: int = Field(ge=1, le=3)
    mime_type: str
    byte_size: int = Field(ge=1, le=10485760)
    updated_at: datetime
    url: str
    thumbnail_url: str


class HousingBuildingItem(BuildingResponse):
    allowed_actions: list[Literal["edit", "delete", "create_floor"]]


class HousingFloorItem(FloorResponse):
    building_name: str
    allowed_actions: list[Literal["edit", "delete", "create_room", "manage_care"]]


class HousingCatalogResponse(BaseModel):
    access_revision: AccessRevision
    buildings: list[HousingBuildingItem]
    floors: list[HousingFloorItem]
    can_create_building: bool


class HousingRoomCard(RoomResponse):
    building_code: str
    building_name: str
    building_is_active: bool
    floor_number: int
    floor_name: str
    floor_is_active: bool
    current_occupants: int = Field(ge=0)
    remaining_capacity: int = Field(ge=0)
    current_gender: Sex | None
    has_future_reservation: bool
    occupancy_status: Literal["occupied", "unoccupied"]
    is_assignable: bool
    status_text: str
    allowed_actions: HousingAllowedActions
    occupants: list[StaySummary] = Field(default_factory=list)
    future_reservation_count: int = Field(default=0, ge=0)
    open_issue_summary: RoomIssueSummary | None = None
    pending_transfer_count: int = Field(default=0, ge=0)


class HousingRoomDetail(HousingResponseModel):
    photos: list[RoomPhotoSummary] = Field(default_factory=list)
    voided_history: list[StaySummary] = Field(default_factory=list)
    can_view_voided_history: bool = False
    access_revision: AccessRevision
    as_of: AwareDatetime
    room: HousingRoomCard
    current_stays: list[StaySummary]
    future_reservations: list[StaySummary]
    history: list[StaySummary]
    transfers: list[TransferSummary]
    issues: list[RoomIssueSummary]


class HousingSummary(BaseModel):
    pending_cleaning_rooms: int = 0
    total_rooms: int
    occupied_rooms: int
    unoccupied_rooms: int
    assignable_rooms: int
    unassignable_rooms: int
    problem_rooms: int
    pending_verification_rooms: int
    total_capacity: int
    current_occupants: int
    remaining_capacity: int
    occupancy_rate: Decimal
    single_rooms: int
    double_rooms: int
    triple_rooms: int
    custom_rooms: int
    functional_rooms: int


class HousingOverviewResponse(BaseModel):
    access_revision: AccessRevision
    as_of: datetime
    summary: HousingSummary
    rooms: list[HousingRoomCard]
    page: int
    page_size: int
    total: int
