from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.referrals.schemas import InvitedHousingStatus, InvitedHousingSummary

PersonKind = Literal["student", "player", "both"]
IntakeStatus = Literal["pending", "returned", "approved", "withdrawn"]
IntakeQueueView = Literal["mine", "review"]


class AssistedGuardianInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str = Field(min_length=1, max_length=80)
    identity_number: SecretStr
    phone: SecretStr
    email: SecretStr | None = None
    relationship: str = Field(min_length=2, max_length=40)
    sex: Literal["male", "female"]
    date_of_birth: date
    is_primary: bool = False


class AssistedPersonFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str = Field(min_length=1, max_length=80)
    identity_number: SecretStr
    phone: SecretStr
    email: SecretStr
    sex: Literal["male", "female"]
    date_of_birth: date
    guardians: list[AssistedGuardianInput] = Field(default_factory=list)


class IntakeInput(AssistedPersonFields):
    kind: PersonKind
    coach_person_ids: list[UUID] = Field(default_factory=list)
    team_id: UUID | None = None


class AssistedProfileCorrection(AssistedPersonFields):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: UUID


class AssistedGuardianView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str
    identity_number: str
    phone: str
    email: str | None
    relationship: str
    sex: Literal["male", "female"]
    date_of_birth: date
    is_primary: bool


class IntakePayloadView(BaseModel):
    """Decrypted only after detail authorization; never serialize an input model."""

    model_config = ConfigDict(extra="forbid")
    kind: PersonKind
    full_name: str
    identity_number: str
    phone: str
    email: str
    sex: Literal["male", "female"]
    date_of_birth: date
    guardians: list[AssistedGuardianView]
    coach_person_ids: list[UUID]
    team_id: UUID | None


class IntakeSubmitter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    label: str


class IntakeListItem(BaseModel):
    id: UUID
    version: int
    status: IntakeStatus
    kind: PersonKind
    allowed_actions: list[str]
    submitted_by: IntakeSubmitter
    approved_person_id: UUID | None = None


class IntakeView(IntakeListItem):
    scoped_payload: IntakePayloadView | None
    returned_reason: str | None
    profile_version: int | None = None


class ApprovalResult(BaseModel):
    request_id: UUID | None
    person_id: UUID
    approval_id: UUID


class AssistedOption(BaseModel):
    id: UUID
    label: str


AssistedEditorAction = Literal["correct_profile", "replace_coaches"]


class AssistedEditorContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: UUID
    kind: PersonKind
    profile_version: int = Field(ge=1)
    current_coach_person_ids: list[UUID]
    eligible_coaches: list[AssistedOption]
    allowed_actions: list[AssistedEditorAction]


class AssistedContext(BaseModel):
    can_submit: bool
    can_review: bool
    can_direct_create: bool
    can_read_managed: bool
    allowed_team_ids: list[UUID]
    eligible_coaches: list[AssistedOption] = Field(default_factory=list)
    eligible_teams: list[AssistedOption] = Field(default_factory=list)


class ManagedPeopleFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str | None = Field(default=None, max_length=80)
    kind: PersonKind | None = None
    team_id: UUID | None = None
    housing_status: InvitedHousingStatus | None = None
    cursor: str | None = Field(default=None, max_length=1024)
    page_size: int = Field(default=20, ge=1, le=100)


class ManagedAffiliation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["coach", "team"]
    id: UUID
    label: str


class ManagedPerson(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: UUID
    full_name: str
    nickname: str | None
    kinds: list[Literal["student", "player"]]
    authorized_affiliations: list[ManagedAffiliation]
    housing: InvitedHousingSummary


class ManagedPeoplePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ManagedPerson]
    next_cursor: str | None
    total: int = Field(ge=0)


class AssistedActivationView(BaseModel):
    state: Literal["requested", "verified", "completed"]
    expires_at: datetime


class PersonnelIntakeRevisionDTO(BaseModel):
    request_id: UUID
    version: int
    created_at: datetime


class PersonnelCredentialReservationDTO(BaseModel):
    id: UUID
    kind: Literal["identity", "email"]
    request_id: UUID
    person_id: UUID | None
    ended_at: datetime | None


class AssistedApprovalDTO(BaseModel):
    id: UUID
    request_id: UUID
    revision_version: int
    person_id: UUID
    approved_at: datetime
    supersedes_approval_id: UUID | None


class AssistedPersonProfileDTO(BaseModel):
    person_id: UUID
    current_approval_id: UUID
    version: int


class CoachResponsibilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coach_person_ids: list[UUID]
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CoachPersonResponsibilityDTO(BaseModel):
    id: UUID
    coach_person_id: UUID
    person_id: UUID
    created_at: datetime
    ended_at: datetime | None


PersonnelIntakeRequestDTO = IntakeView
AssistedActivationSessionDTO = AssistedActivationView
