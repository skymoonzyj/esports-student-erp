from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.identity.readiness import AgeStatus, GuardianIssueCode, GuardianStatus

PersonType = Literal["student", "guardian", "staff", "visitor"]
StaffType = Literal["coach", "life_teacher", "customer_service", "administration", "other"]
Sex = Literal["male", "female"]
StudentStatus = Literal["draft", "active", "inactive"]
GuardianRelationshipStatus = Literal["scheduled", "active", "expired", "ended"]


class PersonBaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str = Field(min_length=2, max_length=80)
    identity_number: str
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
    identity_number: str | None = None
    sex: Sex | None = None
    date_of_birth: date | None = None
    phone: str | None = None
    email: EmailStr | None = None
    student_number: str | None = Field(default=None, min_length=3, max_length=32)
    student_status: StudentStatus | None = None
    staff_type: StaffType | None = None
    visit_reason: str | None = Field(default=None, min_length=2, max_length=200)
    linked_student_id: UUID | None = None


class PersonSummary(BaseModel):
    id: UUID
    full_name: str
    person_types: list[PersonType]
    masked_identity: str
    masked_phone: str | None
    email: EmailStr | None
    sex: Sex
    date_of_birth: date
    created_at: datetime
    age_status: AgeStatus
    guardian_status: GuardianStatus


class PersonDetail(PersonSummary):
    student_number: str | None = None
    student_status: StudentStatus | None = None
    staff_type: StaffType | None = None
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
