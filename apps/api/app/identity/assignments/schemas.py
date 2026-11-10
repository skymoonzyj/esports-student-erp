from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

BusinessKind = Literal["student", "player", "both"]


class AssignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: str = Field(min_length=1, max_length=128)
    idempotency_key: UUID
    reason: str = Field(min_length=1, max_length=200)
    business_kind: BusinessKind | None
    student_project_ids: list[UUID] = Field(max_length=200)
    coach_person_ids: list[UUID] = Field(max_length=200)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("变更原因不能为空")
        return value.strip()

    @field_validator("student_project_ids", "coach_person_ids")
    @classmethod
    def unique_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("选择项不能重复")
        return sorted(values)


class AssignmentProject(BaseModel):
    id: UUID
    name: str


class AssignmentCoach(BaseModel):
    id: UUID
    full_name: str
    project_ids: list[UUID]


class AssignmentCapabilities(BaseModel):
    can_edit: bool
    can_manage_account_roles: bool
    can_remove_identity: Literal[False] = False


class AssignmentView(BaseModel):
    person_id: UUID
    version: str
    business_kind: BusinessKind | None
    source_intake_kind: Literal["student", "player", "both", "staff"] | None
    student_project_ids: list[UUID]
    coach_person_ids: list[UUID]
    capabilities: AssignmentCapabilities
    eligible_projects: list[AssignmentProject]
    eligible_coaches: list[AssignmentCoach]
    current_projects: list[AssignmentProject]
    current_coaches: list[AssignmentCoach]
    account_id: UUID | None
    account_status: Literal["active", "disabled", "deleted", "not_activated"]
