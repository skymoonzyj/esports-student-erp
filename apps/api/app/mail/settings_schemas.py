from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

TlsMode = Literal["implicit_tls", "starttls"]
MailTestStatus = Literal["never", "passed", "failed"]
MailTemplateEventKey = Literal["verification_code", "password_reset"]


class MailDeliverySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    smtp_host: str = Field(min_length=1, max_length=255)
    smtp_port: int = Field(ge=1, le=65535)
    tls_mode: TlsMode
    username: str | None = Field(default=None, min_length=1, max_length=320)
    sender: EmailStr | None = None
    from_name: str = Field(min_length=1, max_length=120)
    reply_to: EmailStr | None = None
    authorization_code: SecretStr | None = None
    enabled: bool = False

    @field_validator("smtp_host", "username", "from_name")
    @classmethod
    def reject_header_injection(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "\r" in value or "\n" in value:
            raise ValueError("值不能为空或包含换行符")
        return normalized

    @field_validator("authorization_code")
    @classmethod
    def reject_empty_authorization_code(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("授权码不能为空")
        return value


class MailDeliverySettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    enabled: bool
    smtp_host: str | None
    smtp_port: int | None
    tls_mode: TlsMode | None
    username_masked: str | None
    sender_masked: str | None
    from_name: str | None
    reply_to_masked: str | None
    authorization_code_configured: bool
    last_test_status: MailTestStatus
    last_test_at: datetime | None
    updated_at: datetime | None


class MailDeliverySettingsTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MailTestStatus
    request_id: str


class MailTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_template: str = Field(min_length=1, max_length=200)
    html_template: str = Field(min_length=1, max_length=100_000)


class MailTemplatePlaceholder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    label: str
    example: str


class MailTemplateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: MailTemplateEventKey
    event_name: str
    description: str
    subject_template: str
    html_template: str
    placeholders: list[MailTemplatePlaceholder]
    is_default: bool
    updated_at: datetime | None


class MailTemplatePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    html: str
    plain_text: str
