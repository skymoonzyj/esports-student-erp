from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

ResidentPersonType = Literal[
    "student",
    "player",
    "student_player",
    "team_manager",
    "coach",
    "assistant_coach",
    "life_teacher",
    "customer_service",
    "administration",
    "other",
    "visitor",
]
ReportStayPurpose = Literal["permanent", "camp", "temporary"]
ReportStayStatus = Literal["checked_in", "checkout_pending", "checked_out"]
ReportSex = Literal["male", "female"]


def _require_aware(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("report timestamps must be timezone-aware")
    return value


class ResidentReportRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    row_id: str
    person_id: UUID
    full_name: str
    phone: str
    identity_number: str
    sex: ReportSex
    date_of_birth: date
    person_type: ResidentPersonType
    building_id: UUID
    building_name: str
    building_order: int
    floor_id: UUID
    floor_name: str
    floor_order: int
    room_id: UUID
    room_number: str
    stay_id: UUID
    stay_purpose: ReportStayPurpose
    actual_check_in_at: datetime
    actual_check_out_at: datetime | None
    stay_status: ReportStayStatus
    cohort_id: UUID | None
    cohort_name: str | None
    visit_reason: str | None
    is_minor_at_check_in: bool
    guardian_count: int
    phone_verified: bool = True
    phone_evidence: Literal["verified_identifier", "approved_profile"] = "verified_identifier"

    _aware_check_in = field_validator("actual_check_in_at")(_require_aware)
    _aware_check_out = field_validator("actual_check_out_at")(
        lambda value: _require_aware(value) if value is not None else value
    )

    @model_validator(mode="after")
    def validate_stay(self) -> ResidentReportRow:
        if not self.row_id.strip():
            raise ValueError("row_id must be non-empty")
        if self.guardian_count < 0:
            raise ValueError("guardian_count must be non-negative")
        if self.stay_status == "checked_out" and self.actual_check_out_at is None:
            raise ValueError("checked-out rows require actual_check_out_at")
        if self.stay_status != "checked_out" and self.actual_check_out_at is not None:
            raise ValueError("active rows cannot have actual_check_out_at")
        if (
            self.actual_check_out_at is not None
            and self.actual_check_out_at <= self.actual_check_in_at
        ):
            raise ValueError("actual check-out must follow actual check-in")
        if self.stay_purpose == "camp" and self.cohort_id is None:
            raise ValueError("camp rows require a cohort")
        if self.stay_purpose == "temporary" and not (self.visit_reason or "").strip():
            raise ValueError("temporary rows require a visit reason")
        return self


class GuardianReportRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resident_row_id: str
    student_id: UUID
    student_name: str
    guardian_id: UUID
    guardian_name: str
    guardian_phone: str
    guardian_identity_number: str


class HousingReportProjectionV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    generated_at: datetime
    range_start: datetime
    range_end: datetime
    residents: tuple[ResidentReportRow, ...]
    guardians: tuple[GuardianReportRow, ...]

    _aware_generated = field_validator("generated_at")(_require_aware)
    _aware_start = field_validator("range_start")(_require_aware)
    _aware_end = field_validator("range_end")(_require_aware)

    @model_validator(mode="after")
    def validate_range_and_links(self) -> HousingReportProjectionV1:
        if self.range_start >= self.range_end:
            raise ValueError("report range must be half-open and non-empty")
        resident_ids = {resident.row_id for resident in self.residents}
        if len(resident_ids) != len(self.residents):
            raise ValueError("resident row IDs must be unique")
        if any(guardian.resident_row_id not in resident_ids for guardian in self.guardians):
            raise ValueError("guardian row references an unknown resident row")
        return self

    @property
    def resident_count(self) -> int:
        return len({row.person_id for row in self.residents})

    @property
    def stay_row_count(self) -> int:
        return len(self.residents)

    @property
    def guardian_row_count(self) -> int:
        return len(self.guardians)


class ProjectionIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    person_id: UUID
    person_name: str
    masked_phone: str | None
    stay_id: UUID
    missing_fields: tuple[str, ...]


class ProjectionBuildResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    projection: HousingReportProjectionV1 | None
    issues: tuple[ProjectionIssue, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self) -> ProjectionBuildResult:
        if self.projection is None and not self.issues:
            raise ValueError("projection failure requires safe issues")
        if self.projection is not None and self.issues:
            raise ValueError("successful projection cannot contain issues")
        return self
