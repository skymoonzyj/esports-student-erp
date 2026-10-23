from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

BeneficiaryKind = Literal["person", "organization"]
RevenueShareRole = Literal[
    "inviter",
    "subject_coach",
    "platform_developer",
    "assistant",
    "additional",
]
CalculationType = Literal["percentage", "fixed"]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RevenueShareOrganizationCreate(StrictInput):
    name: str = Field(min_length=1, max_length=120)


class RevenueShareOrganizationResponse(BaseModel):
    id: UUID
    name: str
    is_active: bool


class ReferralCodeCreate(StrictInput):
    code: str | None = Field(default=None, min_length=4, max_length=24)
    label: str = Field(min_length=1, max_length=120)
    source_kind: BeneficiaryKind
    referrer_person_id: UUID | None = None
    referrer_organization_id: UUID | None = None

    @model_validator(mode="after")
    def validate_source(self) -> ReferralCodeCreate:
        person = self.referrer_person_id is not None
        organization = self.referrer_organization_id is not None
        if person == organization or (self.source_kind == "person") != person:
            raise ValueError("referral source does not match source kind")
        return self


class ReferralCodeResponse(BaseModel):
    id: UUID
    code: str
    label: str
    source_kind: BeneficiaryKind
    source_name: str
    is_active: bool
    registration_url: str


class RevenueShareRuleCreate(StrictInput):
    project_id: UUID | None = None
    role_kind: RevenueShareRole
    recipient_person_id: UUID | None = None
    recipient_organization_id: UUID | None = None
    label: str | None = Field(default=None, max_length=120)
    calculation_type: CalculationType
    percentage: str | None = None
    fixed_amount: str | None = None
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> RevenueShareRuleCreate:
        explicit = self.role_kind in {"platform_developer", "additional"}
        recipient_count = int(self.recipient_person_id is not None) + int(
            self.recipient_organization_id is not None
        )
        if (explicit and recipient_count != 1) or (not explicit and recipient_count != 0):
            raise ValueError("rule recipient does not match role")
        if self.calculation_type == "percentage":
            if self.percentage is None or self.fixed_amount is not None:
                raise ValueError("percentage rule requires percentage only")
        elif self.fixed_amount is None or self.percentage is not None:
            raise ValueError("fixed rule requires fixed amount only")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")
        return self


class RevenueShareRuleResponse(BaseModel):
    id: UUID
    project_id: UUID | None
    project_name: str | None
    role_kind: RevenueShareRole
    recipient_person_id: UUID | None
    recipient_organization_id: UUID | None
    recipient_name: str | None
    label: str | None
    calculation_type: CalculationType
    percentage_basis_points: int | None
    fixed_amount_cents: int | None
    effective_from: date
    effective_to: date | None
    is_active: bool


class RevenueShareRuleDisableResponse(BaseModel):
    id: UUID
    is_active: Literal[False]


class RevenueShareBeneficiaryOption(BaseModel):
    id: UUID
    name: str


class RevenueShareProjectOption(BaseModel):
    id: UUID
    name: str


class RevenueShareConfigurationResponse(BaseModel):
    organizations: list[RevenueShareOrganizationResponse]
    referral_codes: list[ReferralCodeResponse]
    rules: list[RevenueShareRuleResponse]
    people: list[RevenueShareBeneficiaryOption]
    projects: list[RevenueShareProjectOption]


class RevenueShareBalanceItem(BaseModel):
    beneficiary_kind: BeneficiaryKind
    beneficiary_id: UUID
    beneficiary_name: str
    earned_cents: int
    settled_cents: int
    outstanding_cents: int


class RevenueShareFinanceSummary(BaseModel):
    earned_cents: int
    settled_cents: int
    outstanding_cents: int
    pending_payment_count: int
    items: list[RevenueShareBalanceItem]


class RevenueShareSettlementCreate(StrictInput):
    idempotency_key: UUID
    beneficiary_kind: BeneficiaryKind
    beneficiary_id: UUID
    amount_cents: int = Field(gt=0)
    settled_on: date
    reference: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class RevenueShareSettlementResponse(BaseModel):
    id: UUID
    beneficiary_kind: BeneficiaryKind
    beneficiary_id: UUID
    beneficiary_name: str
    amount_cents: int
    settled_on: date
    reference: str
    note: str | None
    created_at: datetime


class RevenueShareExportRequest(StrictInput):
    purpose: str = Field(min_length=2, max_length=200)


RevenueShareEntryKind = Literal[
    "earning",
    "refund_adjustment",
    "manual_adjustment",
]


class RevenueShareLedgerItem(BaseModel):
    id: UUID
    payment_id: UUID
    entry_kind: RevenueShareEntryKind
    role_kind: RevenueShareRole
    amount_cents: int
    occurred_at: datetime


class RevenueShareSelfResponse(BaseModel):
    earned_cents: int
    settled_cents: int
    outstanding_cents: int
    items: list[RevenueShareLedgerItem]
    referral_codes: list[ReferralCodeResponse]
    total: int
    page_size: int
    has_more: bool
    next_cursor: str | None


class RevenueShareReconcileRequest(StrictInput):
    batch_size: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=768)


class RevenueShareReconcileResponse(BaseModel):
    processed: int
    allocated: int
    pending: int
    has_more: bool
    next_cursor: str | None
