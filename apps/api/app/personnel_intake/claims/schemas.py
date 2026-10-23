from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.identity.document_identity import ISO_COUNTRIES
from app.identity.normalization import normalize_cn_id, normalize_email
from app.personnel_intake.schemas import AssistedGuardianInput, AssistedGuardianView

ClaimStatus = Literal[
    "pending", "rejected", "expired", "invalidated", "approved", "revoked", "completed"
]


class ClaimDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _country(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().upper()
    if value not in ISO_COUNTRIES:
        raise ValueError("签发国家须为 ISO 两位国家代码")
    return value


class ClaimCompletionFields(ClaimDTO):
    phone: SecretStr | None = None
    sex: Literal["male", "female"] | None = None
    date_of_birth: date | None = None
    issuing_country: str | None = None
    guardians: list[AssistedGuardianInput] = Field(default_factory=list, max_length=10)

    @field_validator("issuing_country")
    @classmethod
    def country(cls, value: str | None) -> str | None:
        return _country(value)

    @field_validator("phone")
    @classmethod
    def contact(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if not raw:
            return None
        if len(raw) > 80 or any(ord(char) < 32 for char in raw):
            raise ValueError("联系方式无效")
        return SecretStr(raw)


class ClaimCompletionView(ClaimDTO):
    phone: str | None = None
    sex: Literal["male", "female"] | None = None
    date_of_birth: date | None = None
    issuing_country: str | None = None
    guardians: list[AssistedGuardianView] = Field(default_factory=list)


class ClaimStartInput(ClaimDTO):
    full_name: SecretStr
    identity_number: SecretStr
    email: SecretStr
    document_type: Literal["cn_id", "passport"] = "cn_id"
    issuing_country: str | None = None
    completion: ClaimCompletionFields | None = None

    @field_validator("full_name")
    @classmethod
    def trim_name(cls, value: SecretStr) -> SecretStr:
        name = value.get_secret_value().strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("姓名长度须为 1–80 字")
        return SecretStr(name)

    @field_validator("issuing_country")
    @classmethod
    def country(cls, value: str | None) -> str | None:
        return _country(value)

    @model_validator(mode="after")
    def normalize_identity(self) -> ClaimStartInput:
        raw = self.identity_number.get_secret_value()
        if self.document_type == "cn_id":
            self.identity_number = SecretStr(normalize_cn_id(raw).number)
            if self.issuing_country not in (None, "CN"):
                raise ValueError("身份证签发国家无效")
        elif not raw.strip() or len(raw.strip()) > 80 or any(ord(char) < 32 for char in raw):
            raise ValueError("护照号码无效")
        extra_country = self.completion.issuing_country if self.completion else None
        if self.issuing_country and extra_country and self.issuing_country != extra_country:
            raise ValueError("签发国家不一致")
        if self.document_type == "cn_id" and extra_country not in (None, "CN"):
            raise ValueError("身份证签发国家无效")
        return self

    @field_validator("email")
    @classmethod
    def normalize_contact(cls, value: SecretStr) -> SecretStr:
        return SecretStr(normalize_email(value.get_secret_value()))


class ClaimVerifyInput(ClaimDTO):
    code: SecretStr

    @field_validator("code")
    @classmethod
    def six_digits(cls, value: SecretStr) -> SecretStr:
        if re.fullmatch(r"[0-9]{6}", value.get_secret_value()) is None:
            raise ValueError("请输入六位数字验证码")
        return value


class ClaimStart(ClaimDTO):
    accepted: Literal[True] = True
    resend_available_at: datetime
    expires_at: datetime


class ClaimIssued:
    """Internal-only transport, intentionally not a serializable response DTO."""

    __slots__ = ("public", "session_token")

    def __init__(self, *, public: ClaimStart, session_token: SecretStr):
        self.public = public
        self.session_token = session_token


class ClaimPublicStatus(ClaimDTO):
    status: Literal["waiting_review", "approved", "rejected", "expired", "closed"]
    reason: str | None = None


class ClaimAdminContext(ClaimDTO):
    can_enter: bool


class ClaimListItem(ClaimDTO):
    id: UUID
    version: int = Field(ge=1)
    status: ClaimStatus
    masked_name: str
    masked_email: str
    verified_at: datetime
    expires_at: datetime
    allowed_actions: list[str]


class ClaimPage(ClaimDTO):
    items: list[ClaimListItem]
    total: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ClaimDetail(ClaimListItem):
    person_id: UUID | None
    applicant_masked_name: str
    applicant_masked_identity: str
    applicant_email: str
    profile_masked_name: str | None
    profile_masked_identity: str | None
    profile_masked_email: str | None
    private_match_reason: str | None
    same_target_pending_count: int = Field(ge=0)
    can_approve: bool
    applicant_document_type: Literal["cn_id", "passport"] = "cn_id"
    applicant_issuing_country: str | None = None
    applicant_completion: ClaimCompletionView | None = None
    profile_completion: ClaimCompletionView | None = None


class ClaimDecisionInput(ClaimDTO):
    expected_version: int = Field(ge=1)
    idempotency_key: UUID
    identity_confirmed: bool
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def trim_note(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ClaimReasonInput(ClaimDTO):
    expected_version: int = Field(ge=1)
    idempotency_key: UUID
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请填写可向申请人公开的原因")
        return value
