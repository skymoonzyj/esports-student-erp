from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

TeamAction = Literal["read", "manage", "transfer", "assign_managers"]
TeamAllocationStatus = Literal["untracked", "pending", "assigned", "not_required"]
StaffRole = Literal["manager", "coach", "assistant_coach"]


class TeamDto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamAllocationProjection(TeamDto):
    visibility: Literal["allowed", "denied"]
    status: TeamAllocationStatus | None
    version: int | None
    can_update: bool = False


class TeamAllocationInput(TeamDto):
    status: Literal["pending", "not_required"]
    expected_version: int = Field(ge=1)
    idempotency_key: UUID
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class TeamPage[T](TeamDto):
    items: list[T]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class TeamCandidateQuery(TeamDto):
    query: str | None = Field(default=None, max_length=120)
    cursor: str | None = None
    page_size: int = Field(default=20, ge=1, le=20)


class TeamCandidate(TeamDto):
    account_id: UUID | None
    person_id: UUID
    full_name: str
    nickname: str | None
    age: int | None = Field(ge=0)
    is_minor: bool | None


class TeamCandidatePage(TeamDto):
    items: list[TeamCandidate]
    next_cursor: str | None


class ManagerCandidate(TeamCandidate):
    account_id: UUID


class ManagerCandidatePage(TeamDto):
    items: list[ManagerCandidate]
    next_cursor: str | None


class TeamSummary(TeamDto):
    id: UUID
    project_id: UUID
    project_name: str
    name: str
    is_active: bool
    member_count: int = Field(ge=0)
    allowed_actions: list[TeamAction]


class TeamMemberView(TeamDto):
    id: UUID
    person_id: UUID
    full_name: str
    nickname: str | None
    age: int | None = Field(ge=0)
    is_minor: bool | None
    role: Literal["team_member"]
    project_id: UUID
    project_name: str
    team_id: UUID
    team_name: str
    status: Literal["current", "left"]
    joined_on: date
    left_on: date | None
    end_kind: Literal["left", "transferred", "account_deleted"] | None
    account_status: Literal["active", "disabled", "deleted", "not_activated"]
    transfer_reason: str | None


class TeamAssignmentView(TeamDto):
    id: UUID
    account_id: UUID
    full_name: str
    nickname: str | None
    account_status: Literal["active", "disabled", "deleted"]
    started_on: date
    ended_on: date | None


class TeamStaffAssignmentView(TeamAssignmentView):
    staff_role: StaffRole


class TeamAffiliation(TeamDto):
    membership_id: UUID
    project_id: UUID
    project_name: str
    team_id: UUID
    team_name: str
    joined_on: date


class MyTeamResponse(TeamDto):
    current: TeamAffiliation | None
    history: TeamPage[TeamMemberView]


class PersonTeamProjection(TeamDto):
    visibility: Literal["allowed", "denied"]
    current: TeamAffiliation | None


class TeamContext(TeamDto):
    can_open_management: bool
    can_read_self: bool
    can_manage_catalog: bool
    can_assign_managers: bool
    can_transfer: bool


class TeamMutationInput(TeamDto):
    idempotency_key: UUID


class JoinTeamInput(TeamMutationInput):
    person_id: UUID


class LeaveTeamInput(TeamMutationInput):
    pass


class TransferTeamInput(TeamMutationInput):
    target_team_id: UUID
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AssignManagerInput(TeamMutationInput):
    account_id: UUID


class UnassignManagerInput(TeamMutationInput):
    pass


class AssignStaffInput(TeamMutationInput):
    account_id: UUID
    staff_role: StaffRole


class UnassignStaffInput(TeamMutationInput):
    pass


class TeamMutationResult(TeamDto):
    operation_id: UUID
    membership_id: UUID | None = None
    closed_membership_id: UUID | None = None
    assignment_id: UUID | None = None
