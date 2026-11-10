from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.team_management.person_staff_schemas import StaffTeamOption

QuickIdentity = Literal["student", "team_member", "team_manager", "coach", "assistant_coach",
                        "administration", "customer_service", "life_teacher", "property", "staff"]


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: SecretStr = Field(min_length=1, max_length=200000)
    default_arrival_date: date | None = None
    team_choices: dict[int, UUID | Literal["pending"]] = Field(default_factory=dict, max_length=100)


class PreviewRow(BaseModel):
    row_number: int
    full_name: str
    room_label: str
    arrival_date: date | None
    departure_date: date | None
    identity_label: str = "待确认"
    project_label: str = ""
    team_options: list[StaffTeamOption] = Field(default_factory=list)
    selected_team_id: UUID | None = None
    needs_team_choice: bool = False
    birth_date: date | None = None
    sex: Literal["male", "female"] | None = None
    masked_identity: str
    masked_phone: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ticket: str | None = Field(default=None, repr=False)


class PreviewResult(BaseModel):
    import_date: date
    rows: list[PreviewRow]


class ImportTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: UUID
    issued_at: datetime
    expires_at: datetime
    room_id: UUID
    room_type: str
    full_name: str = Field(min_length=1, max_length=80)
    identity_number: str = Field(repr=False)
    phone: str = Field(repr=False)
    birth_date: date
    sex: Literal["male", "female"]
    arrival_date: date
    departure_date: date | None
    identity: QuickIdentity
    job_text: str = Field(max_length=120)
    team_id: UUID | None = None


class CommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: SecretStr = Field(min_length=1, max_length=20000)
    idempotency_key: UUID


class CommitResult(BaseModel):
    person_id: UUID
    stay_id: UUID
    stay_status: Literal["planned", "checked_in"]
    role_pending: bool
