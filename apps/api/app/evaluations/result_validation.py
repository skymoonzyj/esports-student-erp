from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NoReturn

from app.core.privacy import contains_phone_or_identity, looks_like_raw_secret
from app.evaluations.errors import EvaluationResultInvalid
from app.evaluations.schemas import is_public_analysis_key

_SENSITIVE_RESULT_FIELD_KEYS = frozenset(
    {
        "id_card",
        "id_number",
        "mobile",
        "nickname",
        "player_id",
        "player_name",
    }
)


@dataclass(frozen=True)
class EvaluationResultValidation:
    is_complete: bool
    missing_required_fields: tuple[str, ...]


def _invalid() -> NoReturn:
    raise EvaluationResultInvalid("evaluation result is invalid")


def is_public_result_field_key(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and is_public_analysis_key(value)
        and value.casefold() not in _SENSITIVE_RESULT_FIELD_KEYS
    )


def _require_safe_value(value: object) -> None:
    if isinstance(value, str):
        if contains_phone_or_identity(value) or looks_like_raw_secret(value):
            _invalid()
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _require_safe_value(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _require_safe_value(item)


def _structured_item_keys(
    items: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    keys: list[str] = []
    for item in items:
        key = item.get("key")
        summary = item.get("summary")
        if (
            not isinstance(key, str)
            or not is_public_analysis_key(key)
            or not isinstance(summary, str)
        ):
            _invalid()
        _require_safe_value(summary)
        keys.append(key)
    if len(keys) != len(set(keys)):
        _invalid()
    return tuple(keys)


def validate_result_content(
    snapshot: Mapping[str, object],
    *,
    fields: Mapping[str, object],
    confidences: Mapping[str, object],
    missing_fields: Sequence[str],
    warnings: Sequence[Mapping[str, object]],
    components: Sequence[Mapping[str, object]],
    score: object | None,
    grade: object | None,
    advice: object | None,
    require_complete: bool,
) -> EvaluationResultValidation:
    required_value = snapshot.get("required_fields")
    if not isinstance(required_value, list) or any(
        not is_public_result_field_key(field) for field in required_value
    ):
        _invalid()
    required = set(required_value)
    supplied = set(fields)
    missing = set(missing_fields)
    if (
        len(required) != len(required_value)
        or len(missing) != len(missing_fields)
        or any(
            not is_public_result_field_key(field) for field in supplied | missing
        )
        or not supplied <= required
        or not missing <= required
        or supplied & missing
        or set(confidences) != supplied
    ):
        _invalid()
    for value in fields.values():
        _require_safe_value(value)
    _structured_item_keys(warnings)
    _structured_item_keys(components)
    if grade is not None:
        if not isinstance(grade, str):
            _invalid()
        _require_safe_value(grade)
    if advice is not None:
        if not isinstance(advice, str):
            _invalid()
        _require_safe_value(advice)

    missing_required = tuple(sorted(required - supplied - missing))
    is_complete = bool(
        not missing_required
        and score is not None
        and isinstance(grade, str)
        and grade.strip()
        and isinstance(advice, str)
        and advice.strip()
        and components
    )
    if require_complete and not is_complete:
        _invalid()
    return EvaluationResultValidation(
        is_complete=is_complete,
        missing_required_fields=missing_required,
    )
