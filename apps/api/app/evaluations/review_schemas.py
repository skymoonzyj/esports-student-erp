from __future__ import annotations

import re
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.privacy import contains_phone_or_identity, looks_like_raw_secret
from app.evaluations.processing_schemas import Confidence, ResultScalar, Score
from app.evaluations.result_validation import is_public_result_field_key
from app.evaluations.schemas import is_public_analysis_key

_PUBLIC_KEY = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")


class _StrictReviewInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        strict=True,
        str_strip_whitespace=True,
    )


def _validate_public_key(value: str) -> str:
    if not is_public_analysis_key(value) or _PUBLIC_KEY.fullmatch(value) is None:
        raise ValueError("result key is invalid")
    return value


def _validate_safe_text(value: str) -> str:
    if contains_phone_or_identity(value) or looks_like_raw_secret(value):
        raise ValueError("result text contains sensitive data")
    return value


class EvaluationFieldInput(_StrictReviewInput):
    key: str = Field(min_length=1, max_length=80)
    value: ResultScalar

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        value = _validate_public_key(value)
        if not is_public_result_field_key(value):
            raise ValueError("identity result fields are not allowed")
        return value

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: ResultScalar) -> ResultScalar:
        if isinstance(value, str):
            _validate_safe_text(value)
        return value


class EvaluationConfidenceInput(_StrictReviewInput):
    key: str = Field(min_length=1, max_length=80)
    confidence: Confidence

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return _validate_public_key(value)


class EvaluationWarningInput(_StrictReviewInput):
    key: str = Field(min_length=1, max_length=80)
    severity: Literal["info", "warning", "blocking"]
    summary: str = Field(min_length=1, max_length=500)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return _validate_public_key(value)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _validate_safe_text(value)


class EvaluationComponentInput(_StrictReviewInput):
    key: str = Field(min_length=1, max_length=80)
    score: Score
    weight: Confidence
    summary: str = Field(min_length=1, max_length=500)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return _validate_public_key(value)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _validate_safe_text(value)


class EvaluationManualDraftInput(_StrictReviewInput):
    expected_lock_version: int = Field(ge=1)
    draft_revision: int | None = Field(default=None, ge=1)
    fields: list[EvaluationFieldInput] = Field(default_factory=list, max_length=200)
    confidences: list[EvaluationConfidenceInput] = Field(
        default_factory=list,
        max_length=200,
    )
    missing_fields: list[str] = Field(default_factory=list, max_length=200)
    warnings: list[EvaluationWarningInput] = Field(default_factory=list, max_length=100)
    components: list[EvaluationComponentInput] = Field(
        default_factory=list,
        max_length=50,
    )
    score: Score | None = None
    grade: str | None = Field(default=None, min_length=1, max_length=24)
    advice: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("missing_fields")
    @classmethod
    def validate_missing_fields(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("missing fields must be unique")
        return [_validate_public_key(item) for item in value]

    @field_validator("advice")
    @classmethod
    def validate_advice(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_safe_text(value)
        return value

    @model_validator(mode="after")
    def validate_unique_keys(self) -> Self:
        for items in (self.fields, self.confidences, self.warnings, self.components):
            keys = [item.key for item in items]
            if len(keys) != len(set(keys)):
                raise ValueError("result keys must be unique")
        return self


class EvaluationManualSubmitInput(_StrictReviewInput):
    expected_lock_version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)


class EvaluationTaskMatchInput(_StrictReviewInput):
    expected_lock_version: int = Field(ge=1)
    student_person_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    class_id: UUID


class EvaluationResultRevisionInput(_StrictReviewInput):
    expected_lock_version: int = Field(ge=1)
    mode: Literal["manual", "automatic"] = "manual"


class EvaluationReviewInput(_StrictReviewInput):
    expected_lock_version: int = Field(ge=1)
    decision: Literal["approved", "rejected"]
    rejection_reason: str | None = Field(default=None, max_length=1000)

    @field_validator("rejection_reason")
    @classmethod
    def validate_rejection_reason(cls, value: str | None) -> str | None:
        if value:
            _validate_safe_text(value)
        return value


class EvaluationArchiveInput(_StrictReviewInput):
    expected_lock_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _validate_safe_text(value)


class EvaluationReopenInput(_StrictReviewInput):
    expected_lock_version: int = Field(ge=1)
    resume: Literal["automatic", "correction"]
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _validate_safe_text(value)


class EvaluationAdapterCertificationInput(_StrictReviewInput):
    project_id: UUID
    adapter_id: str = Field(min_length=1, max_length=80)
    adapter_version: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$",
    )
    scoring_rule_version: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$",
    )
    result_schema_version: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$",
    )
    manifest_digest: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
        repr=False,
    )

    @field_validator("adapter_id")
    @classmethod
    def validate_adapter_id(cls, value: str) -> str:
        return _validate_public_key(value)
