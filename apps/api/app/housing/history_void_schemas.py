from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator

_PRIVATE_IDENTIFIER = re.compile(
    r"(?<![0-9])(?:1[3-9][0-9]{9}|[0-9]{17}[0-9Xx]|[0-9]{15})(?![0-9])"
)


class StayHistoryVoidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    expected_version: int = Field(ge=1)
    reason: SecretStr = Field(repr=False)
    confirmed: Literal[True]
    idempotency_key: UUID

    @field_validator("confirmed", mode="before")
    @classmethod
    def require_explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit confirmation is required")
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: SecretStr) -> SecretStr:
        reason = value.get_secret_value().strip()
        if not 2 <= len(reason) <= 500:
            raise ValueError("reason must contain 2 to 500 characters")
        if _PRIVATE_IDENTIFIER.search(reason):
            raise ValueError("reason must not contain complete identity or phone numbers")
        return SecretStr(reason)


class StayHistoryVoidResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    stay_id: UUID
    room_id: UUID
    person_id: UUID
    version: int
    voided_at: AwareDatetime
