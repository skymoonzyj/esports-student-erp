from datetime import UTC, datetime
from math import inf, nan
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.talent_tests.catalog import LEGACY_TEST_IDS, get_rules, get_test_rule
from app.talent_tests.errors import UnknownTestId
from app.talent_tests.schemas import DynamicVisionRule, RawResultInput, SessionSummary
from app.talent_tests.scoring import _grade, normalize_result, score_value
from tests.unit.talent_test_helpers import valid_payload

RAW_ADAPTER: TypeAdapter[RawResultInput] = TypeAdapter(RawResultInput)

EXPECTED = (
    (0, 12, 24, 36, 48, 60),
    (0, 14, 28, 42, 56, 70),
    (0, 16, 32, 48, 64, 80),
    (0, 18, 36, 54, 72, 90),
    (0, 20, 40, 60, 80, 100),
)


@pytest.mark.parametrize("level", range(1, 6))
@pytest.mark.parametrize("correct", range(6))
def test_exact_score_matrix(level: int, correct: int) -> None:
    payload = valid_payload("dynamic-vision")
    payload["raw"].update(
        selected_level=level,
        outcomes=["correct"] * correct + ["incorrect"] * (5 - correct),
        measured_display_ms=[100 if level <= 3 else 36] * 5,
    )
    parsed = RAW_ADAPTER.validate_python(payload)
    result = normalize_result(parsed, get_rules("talent-training-v2"))
    assert (result.raw_value, result.score) == (correct, EXPECTED[level - 1][correct])


def test_v1_rules_remain_seven_and_v2_appends_one() -> None:
    old, new = get_rules("talent-training-v1"), get_rules("talent-training-v2")
    assert len(old.test_ids) == 7
    assert new.test_ids[:-1] == old.test_ids
    assert new.test_ids[-1] == "dynamic-vision"
    for test_id in old.test_ids:
        assert old.tests[test_id].model_dump() == new.tests[test_id].model_dump()
        assert "kind" not in old.tests[test_id].model_dump()


def test_v2_dynamic_rule_matches_the_frozen_server_table() -> None:
    rule = get_rules("talent-training-v2").tests["dynamic-vision"]
    assert isinstance(rule, DynamicVisionRule)
    assert rule.model_dump(mode="json") == {
        "kind": "dynamic_vision",
        "engine_version": "dynamic-vision-v1",
        "source_label": "内部试行训练规则",
        "unit": "count",
        "base_points_per_correct": 10,
        "levels": [
            {
                "level": 1,
                "digit_count": 3,
                "nominal_display_ms": 100,
                "coefficient_tenths": 12,
            },
            {
                "level": 2,
                "digit_count": 4,
                "nominal_display_ms": 100,
                "coefficient_tenths": 14,
            },
            {
                "level": 3,
                "digit_count": 5,
                "nominal_display_ms": 100,
                "coefficient_tenths": 16,
            },
            {
                "level": 4,
                "digit_count": 5,
                "nominal_display_ms": 36,
                "coefficient_tenths": 18,
            },
            {
                "level": 5,
                "digit_count": 6,
                "nominal_display_ms": 36,
                "coefficient_tenths": 20,
            },
        ],
    }


def test_catalog_returns_deep_copies_and_uses_snapshot_membership() -> None:
    first = get_rules("talent-training-v2")
    first.tests.pop("dynamic-vision")
    assert "dynamic-vision" in get_rules("talent-training-v2").tests

    missing_id = get_rules("talent-training-v2").model_copy(update={"test_ids": LEGACY_TEST_IDS})
    with pytest.raises(UnknownTestId):
        get_test_rule("dynamic-vision", missing_id)

    missing_rule = get_rules("talent-training-v2")
    missing_rule.tests.pop("dynamic-vision")
    with pytest.raises(UnknownTestId):
        get_test_rule("dynamic-vision", missing_rule)


@pytest.mark.parametrize("level", [True, False, 0, 6, 1.5, "1"])
def test_reject_non_level_integer(level: object) -> None:
    payload = valid_payload("dynamic-vision")
    payload["raw"]["selected_level"] = level
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("actual", "valid"),
    [
        (0, False),
        (-1, False),
        (nan, False),
        (inf, False),
        (-inf, False),
        (10_000, True),
        (10_000.001, False),
        (True, False),
        ("36", False),
    ],
)
def test_display_duration_requires_a_finite_positive_json_number(
    actual: object, valid: bool
) -> None:
    payload = valid_payload("dynamic-vision")
    payload["raw"]["measured_display_ms"][0] = actual
    if actual == 10_000:
        payload["duration_ms"] = 10_400
    if valid:
        parsed = RAW_ADAPTER.validate_python(payload)
        assert parsed.raw.measured_display_ms[0] == 10_000
    else:
        with pytest.raises(ValidationError):
            RAW_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("field", ["outcomes", "measured_display_ms"])
@pytest.mark.parametrize("length", [4, 6])
def test_dynamic_lists_require_exactly_five_items(field: str, length: int) -> None:
    payload = valid_payload("dynamic-vision")
    item: Any = "correct" if field == "outcomes" else 100
    payload["raw"][field] = [item] * length
    payload["duration_ms"] = 600
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


def test_dynamic_outcomes_are_a_closed_enum() -> None:
    payload = valid_payload("dynamic-vision")
    payload["raw"]["outcomes"][0] = "timeout"
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("field", ["answers", "digits", "score", "coefficient"])
def test_dynamic_raw_rejects_client_answers_and_scoring_fields(field: str) -> None:
    payload = valid_payload("dynamic-vision")
    payload["raw"][field] = 100
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("geometry", [None, {}])
def test_dynamic_requires_nonempty_test_area_geometry(geometry: object) -> None:
    payload = valid_payload("dynamic-vision")
    payload["device"]["geometry"] = geometry
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


def test_dynamic_rejects_target_diameter_and_too_short_total_duration() -> None:
    payload = valid_payload("dynamic-vision")
    payload["device"]["geometry"]["target_diameter"] = 10
    with pytest.raises(ValidationError, match="target_diameter"):
        RAW_ADAPTER.validate_python(payload)

    payload = valid_payload("dynamic-vision")
    payload["duration_ms"] = 499.999
    with pytest.raises(ValidationError, match="cover"):
        RAW_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("viewport_width", "font_size"),
    [(599.999, 40), (600, 56)],
)
def test_dynamic_font_matches_the_viewport_threshold(viewport_width: float, font_size: int) -> None:
    payload = valid_payload("dynamic-vision")
    payload["device"]["viewport_width"] = viewport_width
    payload["raw"]["font_size_px"] = font_size
    assert RAW_ADAPTER.validate_python(payload).raw.font_size_px == font_size


@pytest.mark.parametrize(
    ("viewport_width", "font_size"),
    [(599, 56), (600, 40), (800, 55), (800, 57), (800, True), (800, "56")],
)
def test_dynamic_rejects_wrong_or_non_strict_font_size(
    viewport_width: int, font_size: object
) -> None:
    payload = valid_payload("dynamic-vision")
    payload["device"]["viewport_width"] = viewport_width
    payload["raw"]["font_size_px"] = font_size
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


def test_dynamic_rejects_wrong_engine_at_the_input_boundary() -> None:
    payload = valid_payload("dynamic-vision")
    payload["engine_version"] = "dynamic-vision-v2"
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("level", "actual", "flagged"),
    [
        (4, 6, False),
        (4, 66, False),
        (4, 5.999, True),
        (4, 66.001, True),
        (1, 50, False),
        (1, 150, False),
        (1, 49.999, True),
        (1, 150.001, True),
    ],
)
def test_display_duration_deviation_uses_strict_frozen_boundaries(
    level: int, actual: float, flagged: bool
) -> None:
    payload = valid_payload("dynamic-vision")
    payload["raw"].update(selected_level=level, measured_display_ms=[actual] * 5)
    payload["duration_ms"] = 1_000
    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v2"))
    assert ("display_duration_deviation" in result.quality_flags) is flagged


def test_all_skipped_scores_zero_and_keeps_common_quality_flags() -> None:
    payload = valid_payload("dynamic-vision")
    payload["raw"]["outcomes"] = ["skipped"] * 5
    payload["device"]["input_kind"] = "unknown"
    payload["background_interrupted"] = True
    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v2"))
    assert (result.raw_value, result.score, result.grade) == (0, 0, "low")
    assert {"unknown_input", "background_interrupted"} <= set(result.quality_flags)


def test_dynamic_normalization_uses_frozen_versions_and_count_unit() -> None:
    result = normalize_result(
        RAW_ADAPTER.validate_python(valid_payload("dynamic-vision")),
        get_rules("talent-training-v2"),
    )
    assert result.unit == "count"
    assert result.engine_version == "dynamic-vision-v1"
    assert result.scoring_version == "talent-score-v2"


def test_anchor_only_score_api_rejects_dynamic_rules() -> None:
    with pytest.raises(TypeError, match="normalize_result"):
        score_value("dynamic-vision", 5, get_rules("talent-training-v2"))


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (39, "low"),
        (40, "medium"),
        (69, "medium"),
        (70, "high"),
        (89, "high"),
        (90, "exceptional"),
        (100, "exceptional"),
    ],
)
def test_grade_boundaries_include_unreachable_dynamic_scores(score: int, expected: str) -> None:
    assert _grade(score) == expected


def test_session_summary_requires_each_rows_frozen_required_count() -> None:
    payload = {
        "id": "00000000-0000-4000-8000-000000000011",
        "status": "in_progress",
        "created_at": datetime(2026, 9, 21, tzinfo=UTC),
        "completed_at": None,
        "abandoned_at": None,
        "saved_count": 0,
        "report_id": None,
    }
    with pytest.raises(ValidationError):
        SessionSummary.model_validate(payload)
    payload["required_count"] = 8
    assert SessionSummary.model_validate(payload).required_count == 8
