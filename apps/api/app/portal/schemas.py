from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.identity.readiness import AgeStatus

PortalLearningMode = Literal["self", "family", "self_and_family"]
PortalAccessSource = Literal["self", "family"]
ClassType = Literal["standard", "elite"]
AssignmentType = Literal["primary", "assistant"]
EnrollmentStatus = Literal["active", "cancelled", "completed"]
PortalPaymentStatus = Literal["paid", "partially_refunded", "refunded"]
PortalRefundType = Literal["manual", "withdrawal_prorated", "withdrawal_full"]


class PortalContextResponse(BaseModel):
    coach_available: bool
    learning_available: bool
    payments_available: bool
    housing_management_available: bool
    housing_available: bool
    learning_mode: PortalLearningMode | None


class CoachStartOption(BaseModel):
    anchor_cohort_id: UUID
    starts_at: datetime
    cohort_names: list[str]
    class_count: int = Field(ge=1)


class CoachClassSummary(BaseModel):
    cohort_id: UUID
    cohort_name: str
    starts_at: datetime
    ends_at: datetime
    class_id: UUID
    class_name: str
    project_name: str
    class_type: ClassType
    professional_team_name: str | None
    assignment_type: AssignmentType
    student_count: int = Field(ge=0)


class CoachRosterStudent(BaseModel):
    enrollment_id: UUID
    student_person_id: UUID
    student_name: str
    student_number: str
    age_status: AgeStatus
    cohort_id: UUID
    cohort_name: str
    project_name: str
    class_id: UUID
    class_name: str
    class_type: ClassType
    professional_team_name: str | None
    assignment_type: AssignmentType
    enrollment_status: Literal["active"]


class CoachNextCohortsResponse(BaseModel):
    observed_at: datetime
    selected_starts_at: datetime | None
    options: list[CoachStartOption]
    classes: list[CoachClassSummary]
    items: list[CoachRosterStudent]
    page: int
    page_size: int
    total: int


class LearningEnrollmentSummary(BaseModel):
    enrollment_id: UUID
    cohort_id: UUID
    cohort_name: str
    starts_at: datetime
    ends_at: datetime
    project_name: str
    class_id: UUID
    class_name: str
    class_type: ClassType
    professional_team_name: str | None
    status: EnrollmentStatus
    confirmed_at: datetime
    cancelled_at: datetime | None
    completed_at: datetime | None


class LearningStudentSummary(BaseModel):
    student_person_id: UUID
    student_name: str
    student_number: str
    age_status: AgeStatus
    access_source: PortalAccessSource
    current_enrollment: LearningEnrollmentSummary | None


class LearningStudentsResponse(BaseModel):
    items: list[LearningStudentSummary]
    total: int


class LearningTeachingResponse(BaseModel):
    student: LearningStudentSummary
    items: list[LearningEnrollmentSummary]
    page: int
    page_size: int
    total: int


class PortalRefundSummary(BaseModel):
    refund_id: UUID
    amount_cents: int = Field(gt=0)
    refunded_at: datetime
    refund_type: PortalRefundType


class PortalPaymentSummary(BaseModel):
    payment_id: UUID
    source_kind: Literal["api", "manual"]
    amount_cents: int = Field(gt=0)
    currency: Literal["CNY"]
    paid_at: datetime
    refunded_cents: int = Field(ge=0)
    net_cents: int = Field(ge=0)
    status: PortalPaymentStatus
    related_student_name: str | None
    refunds: list[PortalRefundSummary]


class SelfPaymentsResponse(BaseModel):
    items: list[PortalPaymentSummary]
    page: int
    page_size: int
    total: int
