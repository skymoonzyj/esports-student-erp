from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.identity.document_identity import DocumentType
from app.identity.readiness import AgeStatus, GuardianIssueCode, GuardianStatus
from app.team_management.schemas import PersonTeamProjection, TeamAllocationProjection

PersonType = Literal["student", "guardian", "staff", "visitor"]
AssistedKind = Literal["student", "player", "both", "staff"]
IntakeMode = Literal["strict", "pending"]
TeamAllocationStatus = Literal["untracked", "pending", "assigned", "not_required"]
AccountStatus = Literal["active", "disabled", "deleted", "not_activated"]
StaffType = Literal["coach", "life_teacher", "customer_service", "administration", "other"]
Sex = Literal["male", "female"]
StudentStatus = Literal["draft", "active", "inactive"]
GuardianRelationshipStatus = Literal["scheduled", "active", "expired", "ended"]


class PersonBaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str = Field(min_length=2, max_length=80)
    identity_number: str
    document_type: DocumentType = "cn_id"
    document_issuing_country: str | None = None
    sex: Sex
    date_of_birth: date
    phone: str | None = None
    email: EmailStr | None = None


class StudentCreate(PersonBaseInput):
    student_number: str = Field(min_length=3, max_length=32)


class GuardianCreate(PersonBaseInput):
    phone: str
    email: EmailStr


class StaffCreate(PersonBaseInput):
    phone: str
    email: EmailStr
    staff_type: StaffType


class VisitorCreate(PersonBaseInput):
    phone: str
    visit_reason: str = Field(min_length=2, max_length=200)
    linked_student_id: UUID | None = None


class PersonUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str | None = Field(default=None, min_length=2, max_length=80)
    nickname: str | None = Field(default=None, max_length=40)
    profile_notes: str | None = Field(default=None, max_length=2000)
    identity_number: str | None = None
    document_type: DocumentType | None = None
    document_issuing_country: str | None = None
    sex: Sex | None = None
    date_of_birth: date | None = None
    phone: str | None = None
    email: EmailStr | None = None
    student_number: str | None = Field(default=None, min_length=3, max_length=32)
    student_status: StudentStatus | None = None
    staff_type: StaffType | None = None
    visit_reason: str | None = Field(default=None, min_length=2, max_length=200)
    linked_student_id: UUID | None = None


class SelfProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    nickname: str | None = Field(default=None, max_length=40)


class StaffPersonProfile(BaseModel):
    person_id: UUID
    nickname: str | None
    profile_notes: str | None


class PersonRole(BaseModel):
    key: str
    name: str


class PersonSummary(BaseModel):
    id: UUID
    full_name: str
    nickname: str | None
    person_types: list[PersonType]
    assisted_kind: AssistedKind | None = None
    account_status: AccountStatus = "not_activated"
    masked_identity: str
    masked_phone: str | None
    email: EmailStr | None
    sex: Sex | None
    date_of_birth: date | None
    document_type: DocumentType = "cn_id"
    document_issuing_country: str | None = None
    intake_mode: IntakeMode = "strict"
    team_allocation_status: TeamAllocationStatus = Field(default="untracked", exclude=True)
    team_allocation_version: int = Field(default=1, exclude=True)
    team_allocation: TeamAllocationProjection = Field(
        default_factory=lambda: TeamAllocationProjection(
            visibility="denied", status=None, version=None
        )
    )
    staff_type: StaffType | None = None
    created_at: datetime
    age_status: AgeStatus
    guardian_status: GuardianStatus
    roles: list[PersonRole] = Field(default_factory=list)
    team: PersonTeamProjection = Field(
        default_factory=lambda: PersonTeamProjection(visibility="denied", current=None)
    )


class SelfProfile(BaseModel):
    person_id: UUID
    full_name: str
    nickname: str | None
    person_types: list[PersonType]
    staff_type: StaffType | None
    sex: Sex | None
    date_of_birth: date | None
    age_status: AgeStatus
    masked_identity: str
    masked_phone: str | None
    masked_email: str | None


class PersonCounts(BaseModel):
    total: int
    student: int
    guardian: int
    staff: int
    coach: int
    life_teacher: int
    customer_service: int
    administration: int
    other_staff: int
    visitor: int
    team_member: int = 0
    team_manager: int = 0
    in_team: int = 0
    pending_team_allocation: int | None = None
    assigned_team_allocation: int | None = None


class PersonDetail(PersonSummary):
    student_number: str | None = None
    student_status: StudentStatus | None = None
    visit_reason: str | None = None
    linked_student_id: UUID | None = None


class GuardianSummary(PersonSummary):
    relationship_id: UUID
    relationship: str
    is_primary: bool
    valid_from: date
    valid_until: date | None
    status: GuardianRelationshipStatus
    ended_at: datetime | None
    end_reason: str | None


class GuardianEndRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=2, max_length=200)


class GuardianIssueSummary(BaseModel):
    relationship_id: UUID
    guardian_person_id: UUID
    account_id: UUID | None
    reason_codes: list[GuardianIssueCode]


class ReadinessResponse(BaseModel):
    ready: bool
    missing: list[str]
    age_status: AgeStatus
    guardian_status: GuardianStatus
    guardian_issues: list[GuardianIssueSummary] | None = None


class AccountReadinessCandidate(BaseModel):
    phone: str | None = None
    email: EmailStr | None = None


class AccountCandidateSummary(BaseModel):
    id: UUID
    full_name: str
    person_types: list[PersonType]
    masked_phone: str


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int
