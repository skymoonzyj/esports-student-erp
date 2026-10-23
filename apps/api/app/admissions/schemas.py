from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.admissions.money import InvalidMoney, RefundPolicy, parse_cny
from app.identity.normalization import IdentityValidationError, normalize_phone

if TYPE_CHECKING:
    from app.admissions.payment_service import PaymentImportResult

NonBlank80 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
NonBlank120 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
NonBlank500 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
PaymentStatus = Literal["unpaid", "partial", "paid", "overpaid", "refunded"]
AdmissionCaseStatus = Literal["pending_match", "ready", "enrolled", "closed"]
EnrollmentStatus = Literal["active", "cancelled", "completed"]
EnrollmentScheduleMode = Literal["cohort", "custom"]
EnrollmentProgramCategory = Literal["weekend", "experience", "family", "summer"]
EnrollmentPaymentPreference = Literal["offline"]
EnrollmentApplicantAccessSource = Literal["self", "family"]


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int


class _CnyAmountModel(BaseModel):
    amount: str

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: str) -> str:
        try:
            parse_cny(value)
        except InvalidMoney as exc:
            raise ValueError(str(exc)) from exc
        return value

    @property
    def amount_cents(self) -> int:
        return parse_cny(self.amount)


class PaymentImportRequest(_CnyAmountModel):
    source_system: NonBlank80
    external_order_id: NonBlank120
    idempotency_key: NonBlank120
    payer_phone: str
    currency: Literal["CNY"]
    paid_at: AwareDatetime
    raw_payload: dict[str, JsonValue]

    @field_validator("payer_phone")
    @classmethod
    def validate_payer_phone(cls, value: str) -> str:
        try:
            return normalize_phone(value)
        except IdentityValidationError as exc:
            raise ValueError(str(exc)) from exc


class PaymentImportResponse(BaseModel):
    import_event_id: UUID
    payment_id: UUID
    admission_case_id: UUID
    processing_status: Literal["processed", "pending_match"]
    created: bool

    @classmethod
    def from_result(cls, result: PaymentImportResult) -> PaymentImportResponse:
        status: Literal["processed", "pending_match"] = (
            "processed" if result.admission_case.status == "ready" else "pending_match"
        )
        return cls(
            import_event_id=result.event.id,
            payment_id=result.payment.id,
            admission_case_id=result.admission_case.id,
            processing_status=status,
            created=result.created,
        )


class ManualPaymentCreate(_CnyAmountModel):
    idempotency_key: UUID
    student_person_id: UUID | None = None
    admission_case_id: UUID | None = None
    paid_at: AwareDatetime
    note: NonBlank500

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if (self.student_person_id is None) == (self.admission_case_id is None):
            raise ValueError("choose exactly one student or admission case")
        return self


class RefundCreate(_CnyAmountModel):
    idempotency_key: UUID
    refunded_at: AwareDatetime
    reason: NonBlank500


class ConfirmEnrollment(BaseModel):
    class_id: UUID
    agreed_tuition_cents: int | None = Field(default=None, ge=0)
    tuition_override_reason: str | None = Field(default=None, max_length=500)
    capacity_override_reason: str | None = Field(default=None, max_length=500)
    schedule_mode: EnrollmentScheduleMode = "cohort"
    custom_starts_at: AwareDatetime | None = None
    custom_ends_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        if self.schedule_mode == "cohort":
            if self.custom_starts_at is not None or self.custom_ends_at is not None:
                raise ValueError("跟随营期时不能填写单独的开始或结束时间")
        elif self.custom_starts_at is None or self.custom_ends_at is None:
            raise ValueError("单独时间模式必须填写开始和结束时间")
        elif self.custom_ends_at <= self.custom_starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class EnrollmentScheduleUpdate(BaseModel):
    schedule_mode: EnrollmentScheduleMode
    custom_starts_at: AwareDatetime | None = None
    custom_ends_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        if self.schedule_mode == "cohort":
            if self.custom_starts_at is not None or self.custom_ends_at is not None:
                raise ValueError("跟随营期时不能填写单独的开始或结束时间")
        elif self.custom_starts_at is None or self.custom_ends_at is None:
            raise ValueError("单独时间模式必须填写开始和结束时间")
        elif self.custom_ends_at <= self.custom_starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class TransferEnrollment(BaseModel):
    target_class_id: UUID
    reason: NonBlank500
    capacity_override_reason: str | None = Field(default=None, max_length=500)


class CancelEnrollment(BaseModel):
    reason: NonBlank500


class WithdrawalPreviewRequest(BaseModel):
    withdrawal_date: date
    policy: RefundPolicy


class RefundAllocationSchema(BaseModel):
    payment_id: UUID
    amount_cents: int = Field(gt=0)


class WithdrawalPreview(BaseModel):
    withdrawal_date: date
    policy: RefundPolicy
    total_days: int = Field(gt=0)
    remaining_days: int = Field(ge=0)
    agreed_tuition_cents: int = Field(ge=0)
    available_paid_cents: int = Field(ge=0)
    refund_cents: int = Field(gt=0)
    allocations: list[RefundAllocationSchema] = Field(min_length=1)


class WithdrawalConfirmRequest(BaseModel):
    withdrawal_date: date
    policy: RefundPolicy
    total_days: int = Field(gt=0)
    remaining_days: int = Field(ge=0)
    agreed_tuition_cents: int = Field(ge=0)
    available_paid_cents: int = Field(ge=0)
    refund_cents: int = Field(gt=0)
    allocations: list[RefundAllocationSchema] = Field(min_length=1)
    reason: NonBlank500


class PaymentResponse(BaseModel):
    id: UUID
    admission_case_id: UUID
    order_number: str
    source_kind: Literal["api", "manual"]
    source_system: str | None
    external_order_id: str | None
    payer_person_id: UUID | None
    amount_cents: int | None
    refunded_cents: int | None
    available_refund_cents: int | None
    currency: Literal["CNY"]
    paid_at: AwareDatetime
    created_at: AwareDatetime


class RefundResponse(BaseModel):
    id: UUID
    payment_id: UUID
    order_number: str
    amount_cents: int
    refunded_at: AwareDatetime
    refund_type: Literal["manual", "withdrawal_prorated", "withdrawal_full"]
    reason: str
    created_at: AwareDatetime


class AdmissionCaseResponse(BaseModel):
    id: UUID
    order_number: str
    student_person_id: UUID | None
    student_name: str | None
    status: AdmissionCaseStatus
    current_enrollment_id: UUID | None
    payment_status: PaymentStatus
    paid_cents: int | None
    refunded_cents: int | None
    net_paid_cents: int | None
    agreed_tuition_cents: int | None
    due_cents: int | None
    overpaid_cents: int | None
    application_source: Literal["self_service"] | None = None
    selected_project_id: UUID | None = None
    selected_project_name: str | None = None
    selected_program_id: UUID | None = None
    selected_program_name: str | None = None
    selected_coach_person_id: UUID | None = None
    selected_coach_name: str | None = None
    payment_preference: EnrollmentPaymentPreference | None = None
    manage_allowed: bool
    read_only_reason: Literal["archived"] | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EnrollmentProgramScheduleItem(BaseModel):
    time: str
    item: str
    note: str


class EnrollmentProgramResponse(BaseModel):
    id: UUID
    code: str
    name: str
    category: EnrollmentProgramCategory
    duration_label: str
    price_cents: int = Field(gt=0)
    price_note: str
    inclusion_note: str
    summary: str
    target_audience: str
    highlights: list[str]
    schedule: list[EnrollmentProgramScheduleItem]
    eligibility_note: str | None


class EnrollmentApplicantStudentResponse(BaseModel):
    student_person_id: UUID
    student_name: str
    student_number: str
    age_status: Literal["adult", "minor"]
    access_source: EnrollmentApplicantAccessSource


class EnrollmentCoachResponse(BaseModel):
    person_id: UUID
    full_name: str


class EnrollmentProjectResponse(BaseModel):
    id: UUID
    name: str
    coaches: list[EnrollmentCoachResponse]


class EnrollmentApplicationCreate(BaseModel):
    student_person_id: UUID
    project_id: UUID
    program_id: UUID
    coach_person_id: UUID | None = None
    payment_preference: EnrollmentPaymentPreference


class EnrollmentApplicationResponse(BaseModel):
    id: UUID
    order_number: str
    student_person_id: UUID
    student_name: str
    status: AdmissionCaseStatus
    project_id: UUID
    project_name: str
    program_id: UUID
    program_name: str
    coach_person_id: UUID | None
    coach_name: str | None
    price_cents: int = Field(gt=0)
    payment_preference: EnrollmentPaymentPreference
    payment_status: PaymentStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EnrollmentCatalogResponse(BaseModel):
    students: list[EnrollmentApplicantStudentResponse]
    projects: list[EnrollmentProjectResponse]
    programs: list[EnrollmentProgramResponse]
    applications: list[EnrollmentApplicationResponse]


class AdmissionStudentCandidateResponse(BaseModel):
    id: UUID
    full_name: str
    student_number: str
    student_status: Literal["draft", "active", "inactive"]
    masked_phone: str | None


class AdmissionEnrollmentOptionResponse(BaseModel):
    cohort_id: UUID
    cohort_name: str
    cohort_starts_at: AwareDatetime
    cohort_ends_at: AwareDatetime
    cohort_status: Literal["open"]
    class_id: UUID
    class_name: str
    class_is_active: Literal[True]
    capacity: int = Field(gt=0)
    active_enrollment_count: int = Field(ge=0)
    is_full: bool
    default_tuition_cents: int = Field(ge=0)
    readiness_at_start: Literal["ready", "minor_guardian_required"]
    manage_allowed: bool
    capacity_override_allowed: bool


class EnrollmentResponse(BaseModel):
    id: UUID
    admission_case_id: UUID
    order_number: str
    student_person_id: UUID
    student_name: str
    cohort_id: UUID
    cohort_name: str
    class_id: UUID
    class_name: str
    schedule_mode: EnrollmentScheduleMode
    custom_starts_at: AwareDatetime | None
    custom_ends_at: AwareDatetime | None
    effective_starts_at: AwareDatetime
    effective_ends_at: AwareDatetime
    agreed_tuition_cents: int | None
    payment_status: PaymentStatus
    paid_cents: int | None
    refunded_cents: int | None
    net_paid_cents: int | None
    due_cents: int | None
    overpaid_cents: int | None
    status: EnrollmentStatus
    confirmed_at: AwareDatetime
    cancelled_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EnrollmentActionContextResponse(BaseModel):
    manage_allowed: bool
    refund_allowed: bool
    read_only_reason: Literal["archived"] | None


class EnrollmentTransferOptionResponse(BaseModel):
    class_id: UUID
    class_name: str
    capacity: int = Field(gt=0)
    active_enrollment_count: int = Field(ge=0)
    is_full: bool
    capacity_override_allowed: bool


class ServiceAccountCreate(BaseModel):
    name: NonBlank120
    source_system: NonBlank80


class CredentialCreate(BaseModel):
    name: NonBlank120


class CredentialResponse(BaseModel):
    id: UUID
    service_account_id: UUID
    public_id: str
    name: str
    is_active: bool
    disabled_at: AwareDatetime | None


class CreatedCredentialResponse(BaseModel):
    id: UUID
    service_account_id: UUID
    public_id: str
    name: str
    secret: str


class ServiceAccountResponse(BaseModel):
    id: UUID
    name: str
    source_system: str
    is_active: bool
    credentials: list[CredentialResponse]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PaymentConflictResolve(BaseModel):
    action: Literal["resolved", "dismissed"]
    note: NonBlank500


class PaymentConflictResponse(BaseModel):
    id: UUID
    original_import_event_id: UUID
    conflict_type: Literal[
        "external_order_payload_mismatch",
        "idempotency_payload_mismatch",
        "ambiguous_payer",
    ]
    status: Literal["open", "resolved", "dismissed"]
    resolution_note: str | None
    resolved_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MatchStudentRequest(BaseModel):
    student_person_id: UUID


class CloseAdmissionCaseRequest(BaseModel):
    reason: NonBlank500
