from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from app.identity.schemas import StaffType
from app.registration.models import IdentifierKind, RegistrationPurpose, RegistrationState
from app.registration.privacy import contains_sensitive_registration_text
from app.security.passwords import PasswordPolicyError, PasswordService


class RegistrationApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    primary_purpose: RegistrationPurpose
    identifier_kind: IdentifierKind
    identifier: str = Field(min_length=1, max_length=320)
    referral_code: str | None = Field(default=None, min_length=4, max_length=24)


class RegistrationChannelSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_enabled: bool
    email_enabled: bool


class RegistrationChannelSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_enabled: bool
    email_enabled: bool
    phone_signup_allowed_by_deployment: bool
    updated_at: datetime | None
    request_id: str


class RegistrationApplicationStarted(BaseModel):
    application_id: UUID
    state: Literal["draft"]
    masked_identifier: str
    expires_at: datetime
    request_id: str


class RegistrationChallengeResponse(BaseModel):
    challenge_id: UUID
    expires_at: datetime
    retry_after_seconds: int
    masked_destination: str
    request_id: str


class RegistrationVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")


class RegistrationContactChallengeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    identifier_kind: IdentifierKind
    identifier: str = Field(min_length=1, max_length=320)


class RegistrationContactVerifyRequest(RegistrationContactChallengeCreate):
    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")


class RegistrationCsrfResponse(BaseModel):
    csrf_token: str


class RegistrationStateResponse(BaseModel):
    state: RegistrationState
    primary_purpose: RegistrationPurpose
    masked_identifier: str
    next_action: str
    expires_at: datetime
    request_id: str


class PurposeDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requested_staff_type: StaffType | None = None
    staff_application_reason: str | None = Field(
        default=None,
        min_length=2,
        max_length=500,
    )
    visit_reason: str | None = Field(default=None, min_length=2, max_length=200)

    @field_validator("staff_application_reason", "visit_reason")
    @classmethod
    def reject_sensitive_business_text(cls, value: str | None) -> str | None:
        if value is not None and contains_sensitive_registration_text(value):
            raise ValueError("business text must not contain sensitive data")
        return value


class RegistrationProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    password: SecretStr
    full_name: str = Field(min_length=1, max_length=80)
    identity_number: str = Field(min_length=18, max_length=18)
    sex: Literal["male", "female"]
    date_of_birth: date
    consent_version: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$",
    )
    purpose_details: PurposeDetails

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        try:
            PasswordService.validate(value.get_secret_value())
        except PasswordPolicyError as exc:
            raise ValueError("password does not meet policy") from exc
        return value


class RegistrationProfileResponse(RegistrationStateResponse):
    pass


class RegistrationVerifyResponse(RegistrationStateResponse):
    csrf_token: str


class RegistrationFinalizeResponse(BaseModel):
    state: Literal["pending_guardian", "finalized", "conflict"]
    next_action: Literal["complete_guardian", "login", "login_or_reset", "await_review"]
    request_id: str


class RegistrationTermsConsentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    consent_version: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$",
    )


class RegistrationTermsConsentStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_version: str
    accepted_version: str | None
    reconfirmation_required: bool
    request_id: str


class GuardianInvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    identifier_kind: IdentifierKind
    identifier: str = Field(min_length=1, max_length=320)
    relationship: str = Field(min_length=2, max_length=40)
    is_primary: bool = False

    @field_validator("relationship")
    @classmethod
    def reject_sensitive_relationship(cls, value: str) -> str:
        if contains_sensitive_registration_text(value):
            raise ValueError("relationship must not contain sensitive data")
        return value


class GuardianInvitationHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    token: str = Field(min_length=1, max_length=128)


class GuardianInvitationRegistrationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GuardianInvitationContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    masked_identifier: str
    relationship: str
    is_primary: bool
    expires_at: datetime
    consent_version: str
    request_id: str


class GuardianInvitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    masked_identifier: str
    relationship: str
    is_primary: bool
    status: Literal["pending", "accepted", "revoked", "expired"]
    expires_at: datetime


class GuardianInvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    consent_version: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$",
    )


class GuardianInvitationAcceptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted"]
    student_finalized: bool
    request_id: str


class GuardianInvitationAcceptanceReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted"]
    student_finalized: bool
    request_id: str


StaffApplicationStatus = Literal["pending", "approved", "rejected", "withdrawn"]


class StaffApplicationSelfResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: UUID
    requested_staff_type: StaffType
    application_reason: str
    status: StaffApplicationStatus
    reviewed_at: datetime | None
    created_at: datetime


class StaffApplicationAdminListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: UUID
    requested_staff_type: StaffType
    status: StaffApplicationStatus
    created_at: datetime


class StaffApplicationAdminDetail(StaffApplicationAdminListItem):
    application_reason: str
    person_id: UUID | None = None
    full_name: str | None = None
    identity_number: str | None = None
    sex: Literal["male", "female"] | None = None
    date_of_birth: date | None = None
    phone: str | None = None
    email: EmailStr | None = None


class StaffApplicationApproveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str | None = Field(default=None, min_length=2, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_blank_reason(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class StaffApplicationRejectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=2, max_length=500)


class StaffApplicationReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: UUID
    status: Literal["approved", "rejected"]
    requested_staff_type: StaffType
    assigned_role: Literal[
        "coach", "life_teacher", "customer_service", "administration", "staff"
    ] | None
    request_id: str


class RegistrationAdminListItem(BaseModel):
    application_id: UUID
    primary_purpose: RegistrationPurpose
    state: Literal["conflict"]
    masked_identifier: str
    conflict_code: str | None
    candidate_count: int
    expires_at: datetime
    created_at: datetime


class RegistrationAdminCandidate(BaseModel):
    person_id: UUID
    full_name: str
    identity_number: str
    sex: Literal["male", "female"]
    date_of_birth: date
    contact: str | None = None
    deleted: bool
    account_status: str | None = None


class RegistrationAdminDetail(RegistrationAdminListItem):
    full_name: str | None = None
    identity_number: str | None = None
    sex: Literal["male", "female"] | None = None
    date_of_birth: date | None = None
    verified_identifier: str | None = None
    candidates: list[RegistrationAdminCandidate] | None = None


class RegistrationReviewResolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_person_id: UUID
    reason: str = Field(min_length=2, max_length=500)


class RegistrationReviewRejectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=2, max_length=500)


class RegistrationReviewActionResponse(BaseModel):
    state: Literal["pending_guardian", "finalized", "rejected"]
    next_action: Literal["complete_guardian", "login", "login_or_reset", "closed"]
    request_id: str
