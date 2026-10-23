from __future__ import annotations

import hashlib
import json
import math
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel

from app.talent_tests.catalog import get_dynamic_vision_level, get_test_rule
from app.talent_tests.errors import UnknownTestId
from app.talent_tests.schemas import (
    AimResultInput,
    ChimpResultInput,
    DynamicVisionResultInput,
    DynamicVisionRule,
    Grade,
    MemoryResultInput,
    NormalizedResult,
    NumberMemoryResultInput,
    RawResultInput,
    ReactionResultInput,
    RuleSnapshot,
    SequenceResultInput,
    TestRule,
    VerbalMemoryResultInput,
)

SCORE_ANCHORS = (0, 25, 50, 70, 90, 100)
_OPERATION_KEYS = frozenset({"request_id", "submission_id"})


def interpolate(x: int, anchors: tuple[int, ...]) -> int:
    points = sorted(zip(anchors, SCORE_ANCHORS))
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, s0), (x1, s1) in zip(points, points[1:]):  # noqa: RUF007
        if x0 <= x <= x1:
            value = Decimal(s0) + Decimal(x - x0) * (s1 - s0) / Decimal(x1 - x0)
            return int(value.to_integral_value(rounding=ROUND_FLOOR))
    raise ValueError("invalid score anchors")


def score_value(test_id: str, raw_value: int, rules: RuleSnapshot) -> int:
    test_rule = get_test_rule(test_id, rules)
    if isinstance(test_rule, DynamicVisionRule):
        raise TypeError("dynamic-vision scores must be calculated by normalize_result")
    return interpolate(raw_value, test_rule.anchors)


def _mean(samples: list[float]) -> Decimal:
    total: Decimal = sum((Decimal(str(value)) for value in samples), start=Decimal(0))
    return total / len(samples)


def _rounded_mean(samples: list[float]) -> int:
    return int(_mean(samples).quantize(Decimal("1"), rounding=ROUND_HALF_UP))  # noqa: FURB157


def _grade(score: int) -> Grade:
    if score < 40:
        return "low"
    if score < 70:
        return "medium"
    if score < 90:
        return "high"
    return "exceptional"


def _raw_value(value: RawResultInput) -> int:
    if isinstance(value, ReactionResultInput):
        return _rounded_mean(value.raw.samples_ms)
    if isinstance(value, AimResultInput):
        return _rounded_mean(value.raw.intervals_ms)
    if isinstance(value, SequenceResultInput):
        return value.raw.reached_level
    if isinstance(value, NumberMemoryResultInput):
        return value.raw.correct_digits
    if isinstance(value, VerbalMemoryResultInput):
        return value.raw.correct_count
    if isinstance(value, ChimpResultInput):
        return value.raw.reached_count
    if isinstance(value, MemoryResultInput):
        return value.raw.reached_level
    raise TypeError("unsupported talent-test result")


def _quality_flags(value: RawResultInput, raw_value: int) -> list[str]:
    flags: list[str] = []
    timing_anomaly = value.duration_ms == 0
    if isinstance(value, ReactionResultInput):
        timing_anomaly = (
            timing_anomaly
            or any(sample < 80 for sample in value.raw.samples_ms)
            or _mean(value.raw.samples_ms) < Decimal(100)
        )
    elif isinstance(value, AimResultInput):
        timing_anomaly = timing_anomaly or _mean(value.raw.intervals_ms) < Decimal(150)
    if timing_anomaly:
        flags.append("timing_anomaly")
    if value.device.input_kind == "unknown":
        flags.append("unknown_input")
    if value.background_interrupted:
        flags.append("background_interrupted")
    return flags


def normalize_result(value: RawResultInput, rules: RuleSnapshot) -> NormalizedResult:
    test_rule = get_test_rule(value.test_id, rules)
    if value.engine_version != test_rule.engine_version:
        raise ValueError("engine_version does not match the frozen catalog")
    quality_flags: list[str]
    if isinstance(value, DynamicVisionResultInput):
        if not isinstance(test_rule, DynamicVisionRule):
            raise UnknownTestId(value.test_id)
        level = get_dynamic_vision_level(test_rule, value.raw.selected_level)
        raw_value = value.raw.outcomes.count("correct")
        score = raw_value * test_rule.base_points_per_correct * level.coefficient_tenths // 10
        nominal = Decimal(str(level.nominal_display_ms))
        limit = max(Decimal(30), nominal / 2)
        deviation = any(
            abs(Decimal(str(actual)) - nominal) > limit for actual in value.raw.measured_display_ms
        )
        quality_flags = _quality_flags(value, raw_value)
        if deviation:
            quality_flags.append("display_duration_deviation")
    else:
        if not isinstance(test_rule, TestRule):
            raise UnknownTestId(value.test_id)
        raw_value = _raw_value(value)
        score = score_value(value.test_id, raw_value, rules)
        quality_flags = _quality_flags(value, raw_value)
    return NormalizedResult(
        test_id=value.test_id,
        raw_value=raw_value,
        unit=test_rule.unit,
        score=score,
        grade=_grade(score),
        quality_flags=quality_flags,
        raw=value.raw,
        device=value.device,
        duration_ms=value.duration_ms,
        background_interrupted=value.background_interrupted,
        engine_version=value.engine_version,
        scoring_version=rules.scoring_version,
        locale=value.locale,
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonicalize(item) for key, item in value.items() if key not in _OPERATION_KEYS
        }
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("fingerprint payload contains a non-finite number")
        if value.is_integer():
            return int(value)
    return value


def fingerprint(value: BaseModel) -> str:
    payload = _canonicalize(value.model_dump(mode="json"))
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
