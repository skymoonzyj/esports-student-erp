from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, field_validator

from app.evaluations.errors import InvalidGameId
from app.identity.normalization import normalize_phone

EvaluationToolMode = Literal["standalone", "embedded"]
EvaluationApiVersion = Literal["v1"]


class EvaluationAdminSections(BaseModel):
    cohort_staff: bool
    student_matching: bool
    analysis_profiles: bool


class EvaluationCenterContextResponse(BaseModel):
    accessible: Literal[True] = True
    system_evaluation_available: Literal[True] = True
    embedded_start_url: str
    standalone_start_url: str
    upload_available: Literal[False] = False
    video_available: Literal[False] = False
    reports_available: Literal[False] = False
    surveys_available: Literal[False] = False
    game_accounts_available: bool
    admin_available: bool
    admin_sections: EvaluationAdminSections
    integration_api_version: EvaluationApiVersion = "v1"


class EvaluationIntegrationCapabilities(BaseModel):
    api_version: EvaluationApiVersion = "v1"
    features: list[Literal["auth_bridge", "page_shell"]]
    legacy_writes_enabled: Literal[False] = False


class EvaluationLoginRequestCreate(BaseModel):
    client_id: Literal["game-score-tool"]
    mode: EvaluationToolMode
    state: str = Field(
        min_length=32,
        max_length=1024,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )


class EvaluationLoginRequestCreated(BaseModel):
    request_id: UUID
    authorize_url: str
    expires_at: datetime


class EvaluationTicketCreate(BaseModel):
    request_id: UUID


class EvaluationTicketIssued(BaseModel):
    callback_url: str
    ticket: str
    state: str
    expires_at: datetime


class EvaluationTicketExchange(BaseModel):
    ticket: str = Field(min_length=32, max_length=256)


class EvaluationDelegatedSessionRequest(BaseModel):
    session_id: UUID


class EvaluationToolSessionContext(BaseModel):
    api_version: EvaluationApiVersion = "v1"
    account_id: UUID
    person_id: UUID
    display_name: str
    session_id: UUID
    expires_at: datetime
    page_shell_available: Literal[True] = True
    upload_available: Literal[False] = False


class StaffRoleCreate(BaseModel):
    key: str = Field(
        min_length=2,
        max_length=40,
        pattern=r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
    )
    name: str = Field(min_length=2, max_length=80)
    category: Literal["teaching", "life_service", "operations"]

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class StaffAssignmentCreate(BaseModel):
    role_id: UUID
    staff_person_id: UUID
    scope_kind: Literal["project", "class"]
    scope_id: UUID
    valid_from: datetime
    valid_until: datetime | None = None
    permission_keys: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("valid_from", "valid_until")
    @classmethod
    def require_aware_assignment_datetime(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("assignment datetimes must include an offset")
        return value


class StaffAssignmentEnd(BaseModel):
    reason: str = Field(min_length=2, max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class StaffServiceItem(BaseModel):
    id: UUID
    source: Literal["coach_assignment", "cohort_staff_assignment"]
    staff_person_id: UUID
    staff_name: str
    role_name: str
    role_category: Literal["teaching", "life_service", "operations"]
    scope_kind: Literal["project", "class"]
    project_id: UUID
    project_name: str
    class_id: UUID | None
    class_name: str | None
    permission_keys: list[str] = Field(default_factory=list)
    valid_from: datetime
    valid_until: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    manage_allowed: bool


class _SensitiveInput(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)


AnalysisWarningLevel = Literal["info", "warning", "blocking"]


class AnalysisProfileCreate(_SensitiveInput):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    expected_current_version: StrictInt = Field(ge=1)
    mode: Literal["manual_only", "automatic"]
    adapter_id: str | None = Field(default=None, max_length=80, repr=False)
    adapter_version: str | None = Field(default=None, max_length=40, repr=False)
    scoring_rule_version: str | None = Field(default=None, max_length=40, repr=False)
    required_fields: list[str] = Field(default_factory=list, max_length=50, repr=False)
    confidence_thresholds: dict[str, StrictFloat] = Field(
        default_factory=dict,
        repr=False,
    )
    auto_publish_warning_levels: list[AnalysisWarningLevel] = Field(
        default_factory=list,
        repr=False,
    )
    publish_mode: Literal["auto_publish", "review_required"]
    effective_from: datetime
    change_reason: str = Field(min_length=4, max_length=300, repr=False)

    @field_validator("effective_from")
    @classmethod
    def require_aware_effective_from(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_from must include an offset")
        return value

    @field_validator("change_reason", mode="before")
    @classmethod
    def trim_change_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def normalize_game_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not 2 <= len(normalized) <= 64:
        raise InvalidGameId("game id must contain 2 to 64 characters")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise InvalidGameId("game id cannot contain control characters")
    return normalized


class GameAccountBind(_SensitiveInput):
    project_id: UUID
    game_id: str = Field(min_length=2, max_length=64, repr=False)

    @field_validator("game_id", mode="before")
    @classmethod
    def normalize_game_id_before_constraints(cls, value: object) -> object:
        return normalize_game_id(value) if isinstance(value, str) else value


class _ReasonInput(_SensitiveInput):
    reason: str = Field(min_length=1, max_length=200, repr=False)

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be blank")
        return normalized


class GameAccountUnbind(_ReasonInput):
    pass


class GameAccountLimitUpdate(_ReasonInput):
    max_active_accounts: int = Field(ge=3)


class StudentGameAccountItem(BaseModel):
    id: UUID
    project_id: UUID
    project_name: str
    game_id: str = Field(repr=False)
    status: Literal["active", "unbound"]
    valid_from: datetime
    unbound_at: datetime | None
    unbind_reason: str | None


class AuxiliaryPhoneCreate(_SensitiveInput):
    phone: str = Field(min_length=1, max_length=32, repr=False)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone_before_constraints(cls, value: object) -> object:
        return normalize_phone(value) if isinstance(value, str) else value


class AuxiliaryPhoneReplace(_ReasonInput):
    phone: str = Field(min_length=1, max_length=32, repr=False)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone_before_constraints(cls, value: object) -> object:
        return normalize_phone(value) if isinstance(value, str) else value


class AuxiliaryPhoneDeactivate(_ReasonInput):
    pass


class AuxiliaryPhoneItem(BaseModel):
    id: UUID
    masked_phone: str
    status: Literal["active", "inactive"]
    created_at: datetime
    disabled_at: datetime | None


class AuxiliaryPhoneMatch(BaseModel):
    student_person_id: UUID | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    match_count: int = Field(ge=0)
    ambiguous: bool


class GameAccountTarget(BaseModel):
    student_person_id: UUID
    student_name: str
    student_number: str
    access_source: Literal["self", "family"]
    active_count: int = Field(ge=0)
    active_limit: int = Field(ge=3)


class EvaluationProjectDirectoryItem(BaseModel):
    id: UUID
    name: str
    is_active: bool


class EvaluationProjectDirectoryPage(BaseModel):
    items: list[EvaluationProjectDirectoryItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class EvaluationCohortDirectoryItem(BaseModel):
    id: UUID
    name: str


class EvaluationCohortDirectoryPage(BaseModel):
    items: list[EvaluationCohortDirectoryItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class EvaluationStaffScopeDirectoryItem(BaseModel):
    id: UUID
    name: str
    project_id: UUID
    project_name: str
    is_active: bool


class EvaluationStaffScopeDirectoryPage(BaseModel):
    items: list[EvaluationStaffScopeDirectoryItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class StudentDirectoryItem(BaseModel):
    student_person_id: UUID
    student_name: str
    student_number: str


class StudentDirectoryPage(BaseModel):
    items: list[StudentDirectoryItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class GameAccountLimitResponse(BaseModel):
    student_person_id: UUID
    active_count: int = Field(ge=0)
    active_limit: int = Field(ge=3)


class StudentMatchingProfileResponse(BaseModel):
    student_person_id: UUID
    student_name: str
    student_number: str
    active_count: int = Field(ge=0)
    active_limit: int = Field(ge=3)
    game_accounts: list[StudentGameAccountItem]
    game_accounts_total: int = Field(ge=0)
    game_accounts_truncated: bool
    auxiliary_phones: list[AuxiliaryPhoneItem]
    auxiliary_phones_total: int = Field(ge=0)
    auxiliary_phones_truncated: bool


class StaffRoleItem(BaseModel):
    id: UUID
    key: str
    name: str
    category: Literal["teaching", "life_service", "operations"]
    is_builtin: bool
    is_active: bool


class StaffDirectoryItem(BaseModel):
    staff_person_id: UUID
    staff_name: str
    staff_type: Literal[
        "coach",
        "life_teacher",
        "customer_service",
        "administration",
        "other",
    ]


class StaffDirectoryPage(BaseModel):
    items: list[StaffDirectoryItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class AnalysisProfileItem(BaseModel):
    id: UUID
    project_id: UUID
    project_name: str
    version: int = Field(ge=1)
    mode: Literal["manual_only", "automatic"]
    adapter_id: str | None
    adapter_version: str | None
    scoring_rule_version: str | None
    required_fields: list[str]
    confidence_thresholds: dict[str, float]
    auto_publish_warning_levels: list[str]
    publish_mode: Literal["auto_publish", "review_required"]
    effective_from: datetime
    configured_by_name: str | None
    change_reason: str
    is_current: bool


class MediaStorageStatusResponse(BaseModel):
    ready: bool
    media_writable: bool
    quarantine_writable: bool
    max_bytes: int = Field(gt=0)
    max_pixels: int = Field(gt=0)
    preview_max_edge: int = Field(gt=0)
