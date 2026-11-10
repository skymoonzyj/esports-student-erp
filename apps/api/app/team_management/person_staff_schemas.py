"""Personnel staff contracts and keyed proof (not account-only assignments)."""

import json
from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.personnel_intake.identity_selection import (
    STAFF_ROLE_IDENTITIES,
    StaffRole,
    StaffTeamSelection,
)
from app.security.encryption import LookupDigest
from app.team_management.schemas import TeamDto, TeamMutationInput

__all__ = [
    "StaffAuthorizationEvidence",
    "StaffRole",
    "StaffTeamSelection",
    "canonical_staff_authorization",
    "staff_authorization_digest",
]


class StaffAuthorizationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    position_id: UUID
    person_id: UUID
    team_id: UUID
    origin_approval_id: UUID
    staff_role: StaffRole
    version: int = Field(ge=1, strict=True)
    authorizer_user_id: UUID
    authorized_role_key: Literal["team_manager", "coach", "assistant_coach"]
    role_plan_id: UUID | None
    operation_id: UUID

    @model_validator(mode="after")
    def role_matches_position(self) -> Self:
        if self.authorized_role_key != STAFF_ROLE_IDENTITIES[self.staff_role]:
            raise ValueError("队内岗位与授权角色不一致")
        return self


def canonical_staff_authorization(evidence: StaffAuthorizationEvidence) -> str:
    evidence = StaffAuthorizationEvidence.model_validate(evidence.model_dump())
    return json.dumps(
        evidence.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )


def staff_authorization_digest(lookup: LookupDigest, evidence: StaffAuthorizationEvidence) -> str:
    return lookup.digest_domain(
        "person-team-staff-authorization", canonical_staff_authorization(evidence)
    )


class PersonStaffAssignInput(TeamMutationInput):
    person_id: UUID
    staff_role: StaffRole


class PersonStaffEndInput(TeamMutationInput):
    expected_version: int = Field(ge=1, strict=True)
    # Public notes are retained in AuditEvent.purpose without truncation.
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PersonStaffReconfirmInput(PersonStaffEndInput):
    pass


class PersonStaffRow(TeamDto):
    row_key: str
    source_kind: Literal["person", "account"]
    person_position_id: UUID | None
    account_assignment_id: UUID | None
    person_id: UUID
    account_id: UUID | None
    full_name: str
    nickname: str | None
    staff_role: StaffRole
    account_status: Literal["not_activated", "active", "disabled", "deleted"]
    authorization_status: Literal["valid", "reconfirmation_required", "pending_role_approval"]
    started_on: date
    ended_on: date | None
    version: int | None
    allowed_actions: list[Literal["end", "reconfirm"]]


class PersonStaffCandidate(TeamDto):
    person_id: UUID
    account_id: None = None
    full_name: str
    nickname: str | None


class PersonStaffCandidatePage(TeamDto):
    items: list[PersonStaffCandidate]
    next_cursor: str | None


class StaffTeamOption(TeamDto):
    id: UUID
    project_id: UUID
    project_name: str
    name: str


class StaffTeamOptions(TeamDto):
    can_assign: bool
    reason: str | None
    items: list[StaffTeamOption]
