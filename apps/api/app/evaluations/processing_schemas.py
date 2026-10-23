from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from app.core.privacy import contains_phone_or_identity, looks_like_raw_secret
from app.evaluations.schemas import is_public_analysis_key

_RESULT_KEY = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}\Z")
_WORKER_IDENTITY = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}\Z")

ResultInteger = Annotated[
    int,
    Field(strict=True, ge=-9_007_199_254_740_991, le=9_007_199_254_740_991),
]
ResultFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
ResultText = Annotated[str, Field(strict=True, max_length=1000)]
ResultScalar = StrictBool | ResultInteger | ResultFloat | ResultText | None
Score = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
EvaluationFailureClass = Literal[
    "network",
    "worker_interrupted",
    "ocr_timeout",
    "temporary_service",
    "adapter_incompatible",
    "media_invalid",
    "result_schema_invalid",
]


class _StrictProcessingInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        strict=True,
        str_strip_whitespace=True,
    )


class SystemEvaluationWarningInput(_StrictProcessingInput):
    key: str = Field(min_length=1, max_length=80)
    severity: Literal["info", "warning", "blocking"]
    summary: str = Field(min_length=1, max_length=500)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if _RESULT_KEY.fullmatch(value) is None:
            raise ValueError("warning key is invalid")
        return value


class SystemEvaluationComponentInput(_StrictProcessingInput):
    key: str = Field(min_length=1, max_length=80)
    score: Score
    weight: Confidence
    summary: str = Field(min_length=1, max_length=500)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if _RESULT_KEY.fullmatch(value) is None:
            raise ValueError("component key is invalid")
        return value


class SystemEvaluationResultInput(_StrictProcessingInput):
    contract_version: Literal["v2"]
    adapter_id: str = Field(min_length=1, max_length=80)
    adapter_version: str = Field(min_length=1, max_length=40)
    scoring_rule_version: str = Field(min_length=1, max_length=40)
    result_schema_version: str = Field(min_length=1, max_length=40)
    fields: dict[str, ResultScalar] = Field(max_length=200)
    confidences: dict[str, Confidence] = Field(max_length=200)
    missing_fields: list[str] = Field(max_length=200)
    warnings: list[SystemEvaluationWarningInput] = Field(max_length=100)
    score: Score
    grade: str = Field(min_length=1, max_length=24)
    components: list[SystemEvaluationComponentInput] = Field(
        min_length=1,
        max_length=50,
    )
    advice: str = Field(min_length=1, max_length=4000)
    duration_ms: StrictInt = Field(ge=0, le=3_600_000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("adapter_id")
    @classmethod
    def validate_adapter_id(cls, value: str) -> str:
        if _RESULT_KEY.fullmatch(value) is None:
            raise ValueError("adapter ID is invalid")
        return value

    @field_validator(
        "adapter_version",
        "scoring_rule_version",
        "result_schema_version",
    )
    @classmethod
    def validate_version_value(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("version value is invalid")
        return value

    @field_validator("fields", "confidences", mode="before")
    @classmethod
    def validate_mapping_keys(cls, value: object) -> object:
        if not isinstance(value, dict) or any(
            not is_public_analysis_key(key) for key in value
        ):
            raise ValueError("result field key is invalid")
        return value

    @field_validator("missing_fields")
    @classmethod
    def validate_missing_fields(cls, value: list[str]) -> list[str]:
        if (
            len(value) != len(set(value))
            or any(_RESULT_KEY.fullmatch(key) is None for key in value)
        ):
            raise ValueError("missing fields are invalid")
        return value

    @model_validator(mode="after")
    def validate_unique_structured_keys(self) -> Self:
        component_keys = [component.key for component in self.components]
        warning_keys = [warning.key for warning in self.warnings]
        if len(component_keys) != len(set(component_keys)):
            raise ValueError("component keys must be unique")
        if len(warning_keys) != len(set(warning_keys)):
            raise ValueError("warning keys must be unique")
        return self


class SystemEvaluationFailureInput(_StrictProcessingInput):
    contract_version: Literal["v2"]
    error_class: EvaluationFailureClass
    safe_error_summary: str = Field(min_length=1, max_length=300)
    trace_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("safe_error_summary")
    @classmethod
    def require_safe_error_summary(cls, value: str) -> str:
        if contains_phone_or_identity(value) or looks_like_raw_secret(value):
            raise ValueError("failure summary is not safe")
        return value


class EvaluationWorkerClaimInput(_StrictProcessingInput):
    contract_version: Literal["v2"]
    worker_identity: str = Field(min_length=1, max_length=120)
    supported_adapters: list[str] = Field(min_length=1, max_length=20)

    @field_validator("worker_identity")
    @classmethod
    def validate_worker_identity(cls, value: str) -> str:
        if _WORKER_IDENTITY.fullmatch(value) is None:
            raise ValueError("worker identity is invalid")
        return value

    @field_validator("supported_adapters")
    @classmethod
    def validate_supported_adapters(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            re.fullmatch(r"[a-z][a-z0-9_]{0,79}\.v[1-9][0-9]{0,8}", item)
            is None
            for item in value
        ):
            raise ValueError("supported adapters are invalid")
        return value


class EvaluationWorkerClaimMediaResponse(BaseModel):
    media_id: UUID
    position: int = Field(ge=1, le=3)
    slot_key: str
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(gt=0, le=20 * 1024 * 1024)
    ticket: str


class EvaluationWorkerClaimResponse(BaseModel):
    task_id: UUID
    attempt: int = Field(ge=1, le=3)
    task_version: int = Field(ge=1)
    lease_token: str
    lease_expires_at: datetime
    adapter_id: str
    adapter_version: str
    scoring_rule_version: str
    result_schema_version: str
    required_fields: list[str]
    confidence_thresholds: dict[str, float]
    media: list[EvaluationWorkerClaimMediaResponse]


class EvaluationWorkerClaimEnvelope(BaseModel):
    api_version: Literal["v2"] = "v2"
    claim: EvaluationWorkerClaimResponse | None


class EvaluationWorkerLeaseRenewalResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task_id: UUID
    attempt: int = Field(ge=1, le=3)
    task_version: int = Field(ge=1)
    lease_expires_at: datetime


class EvaluationWorkerResultResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task_id: UUID
    result_id: UUID
    result_version: int = Field(ge=1)
    task_status: Literal["needs_review"]
    task_version: int = Field(ge=1)


class EvaluationWorkerFailureResponse(BaseModel):
    api_version: Literal["v2"] = "v2"
    task_id: UUID
    attempt: int = Field(ge=1, le=3)
    task_status: Literal["queued", "manual_input"]
    task_version: int = Field(ge=1)
    retryable: bool
    available_at: datetime | None
    failure_code: str | None
