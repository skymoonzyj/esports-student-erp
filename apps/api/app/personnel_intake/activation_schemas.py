from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from app.identity.normalization import normalize_email
from app.personnel_intake.schemas import PersonKind


class ActivationStartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: SecretStr

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: SecretStr) -> SecretStr:
        return SecretStr(normalize_email(value.get_secret_value()))


class ActivationVerifyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: SecretStr

    @field_validator("code")
    @classmethod
    def six_digits(cls, value: SecretStr) -> SecretStr:
        if re.fullmatch(r"[0-9]{6}", value.get_secret_value()) is None:
            raise ValueError("请输入六位数字验证码")
        return value


class ActivationCompleteInput(ActivationVerifyInput):
    password: SecretStr


class ActivationStart(BaseModel):
    accepted: Literal[True] = True
    resend_available_at: datetime
    expires_at: datetime


class ActivationPreview(BaseModel):
    masked_name: str
    kind: PersonKind


class ActivationCompleted(BaseModel):
    completed: Literal[True] = True


class ActivationIssued:
    """Internal transport only; deliberately not a dataclass/Pydantic response."""

    __slots__ = ("public", "session_token")

    def __init__(self, *, public: ActivationStart, session_token: SecretStr):
        self.public = public
        self.session_token = session_token
