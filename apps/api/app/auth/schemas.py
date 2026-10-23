from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, EmailStr, Field

from app.identity.schemas import StaffType

AccountStatus = Literal["active", "disabled"]


class AccountCreate(BaseModel):
    person_id: UUID
    login_phone: str | None = None
    email: EmailStr | None = None


class AccountSummary(BaseModel):
    id: UUID
    person_id: UUID
    full_name: str
    masked_login_phone: str
    email: EmailStr
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
    identifier: str = Field(
        min_length=1,
        max_length=320,
        validation_alias=AliasChoices("identifier", "phone"),
    )
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=16)


class AuthUser(BaseModel):
    account_id: UUID
    person_id: UUID
    full_name: str
    email: EmailStr
    force_password_change: bool
    permissions: list[str]
    scoped_permissions: list[str] = Field(default_factory=list)


class LoginResponse(BaseModel):
    user: AuthUser
    csrf_token: str


class CsrfResponse(BaseModel):
    csrf_token: str


class RegistrationStartInput(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=11, max_length=32)
    email: EmailStr
    identity_number: str = Field(min_length=18, max_length=18)
    sex: Literal["male", "female"]
    date_of_birth: date
    person_type: Literal["student", "guardian", "staff"]
    staff_type: StaffType | None = None
    password: str = Field(min_length=8, max_length=16)


class RegistrationStartResponse(BaseModel):
    registration_id: UUID
    expires_at: datetime
    message: str


class RegistrationVerifyInput(BaseModel):
    registration_id: UUID
    code: str = Field(min_length=6, max_length=6)


class RegistrationResendInput(BaseModel):
    registration_id: UUID


class RegistrationVerifyResponse(BaseModel):
    registration_id: UUID
    account_status: AccountStatus
    requires_guardian: bool
    message: str
