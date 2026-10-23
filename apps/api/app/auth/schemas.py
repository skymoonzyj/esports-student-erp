from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)

from app.security.passwords import PasswordPolicyError, PasswordService

AccountStatus = Literal["active", "disabled"]


class AccountCreate(BaseModel):
    person_id: UUID
    login_phone: str | None = None
    email: EmailStr | None = None


class AccountSummary(BaseModel):
    id: UUID
    person_id: UUID
    full_name: str
    nickname: str | None
    masked_primary_identifier: str
    primary_identifier_kind: Literal["phone", "email"]
    masked_phone: str | None
    masked_email: str | None
    verified_identifier_kinds: list[Literal["phone", "email"]]
    status: AccountStatus
    force_password_change: bool
    created_at: datetime


class AccountCreatedWithTemporaryPassword(BaseModel):
    account: AccountSummary
    temporary_password: str


class TemporaryPassword(BaseModel):
    account_id: UUID
    temporary_password: str


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=16)


class AuthUser(BaseModel):
    account_id: UUID
    person_id: UUID
    full_name: str
    email: EmailStr | None
    force_password_change: bool
    # This is an identity attribute, not a delegated permission.  The UI uses
    # it to hide recovery-grade surfaces, while the API still verifies the
    # role directly for every sensitive request.
    is_super_admin: bool = False
    permissions: list[str]
    scoped_permissions: list[str] = Field(default_factory=list)


class LoginResponse(BaseModel):
    user: AuthUser
    csrf_token: str


class CsrfResponse(BaseModel):
    csrf_token: str


class PasswordResetChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    identifier: str = Field(min_length=1, max_length=320)


class PasswordResetCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")
    new_password: SecretStr

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: SecretStr) -> SecretStr:
        try:
            PasswordService.validate(value.get_secret_value())
        except PasswordPolicyError as exc:
            raise ValueError("password does not meet policy") from exc
        return value


class VerificationChallengeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    expires_at: datetime
    retry_after_seconds: int
    masked_destination: str
    request_id: str


class PasswordResetCompleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed"] = "completed"
    request_id: str


class AccountIdentifierChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    identifier_kind: Literal["phone", "email"]
    identifier: str = Field(min_length=1, max_length=320)
    current_password: SecretStr

    @field_validator("current_password", mode="before")
    @classmethod
    def preserve_current_password(cls, value: object) -> object:
        return SecretStr(value) if isinstance(value, str) else value


class AccountIdentifierVerifyRequest(AccountIdentifierChallengeRequest):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")


class AccountIdentifierSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["phone", "email"]
    masked_identifier: str
    verified_at: datetime


class AccountIdentifiersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AccountIdentifierSummary]
    request_id: str
