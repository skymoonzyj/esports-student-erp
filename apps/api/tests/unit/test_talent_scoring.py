from copy import deepcopy
from math import inf, nan

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.talent_tests.catalog import LEGACY_TEST_IDS, get_rules
from app.talent_tests.errors import UnknownCatalogVersion, UnknownTestId
from app.talent_tests.schemas import RawResultInput
from app.talent_tests.scoring import fingerprint, normalize_result, score_value
from tests.unit.talent_test_helpers import valid_payload

RAW_ADAPTER: TypeAdapter[RawResultInput] = TypeAdapter(RawResultInput)


def test_verbal_raw_unit_counts_judgments_not_distinct_words() -> None:
    payload = valid_payload("verbal-memory")
    payload["raw"].update(correct_count=200, total_count=203)
    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))
    assert (result.raw_value, result.unit, result.score) == (200, "count", 90)


ANCHORS = {
    "reactiontime": (500, 275, 225, 175, 150, 100),
    "aim": (1000, 550, 450, 350, 300, 250),
    "sequence": (0, 8, 10, 18, 25, 35),
    "number-memory": (0, 7, 9, 13, 16, 20),
    "verbal-memory": (0, 20, 50, 100, 200, 300),
    "chimp": (0, 9, 11, 15, 20, 25),
    "memory": (0, 6, 10, 18, 25, 35),
}
SCORES = (0, 25, 50, 70, 90, 100)


@pytest.mark.parametrize(
    ("test_id", "x", "expected"),
    [
        ("reactiontime", 175, 70),
        ("reactiontime", 150, 90),
        ("reactiontime", 176, 69),
        ("sequence", 18, 70),
        ("number-memory", 16, 90),
        ("verbal-memory", 200, 90),
        ("chimp", 40, 100),
        ("memory", 25, 90),
    ],
)
def test_thresholds(test_id: str, x: int, expected: int) -> None:
    assert score_value(test_id, x, get_rules("talent-training-v1")) == expected


@pytest.mark.parametrize(
    ("test_id", "raw_value", "expected"),
    [
        (test_id, raw_value, score)
        for test_id, anchors in ANCHORS.items()
        for raw_value, score in zip(anchors, SCORES, strict=True)
    ],
)
def test_all_42_score_anchors(test_id: str, raw_value: int, expected: int) -> None:
    assert score_value(test_id, raw_value, get_rules("talent-training-v1")) == expected


@pytest.mark.parametrize("test_id", LEGACY_TEST_IDS)
def test_every_score_segment_is_monotonic(test_id: str) -> None:
    anchors = ANCHORS[test_id]
    low, high = min(anchors), max(anchors)
    values = [
        score_value(test_id, value, get_rules("talent-training-v1"))
        for value in range(low, high + 1)
    ]
    if test_id in {"reactiontime", "aim"}:
        assert values == sorted(values, reverse=True)
    else:
        assert values == sorted(values)


@pytest.mark.parametrize(
    ("test_id", "below", "above"),
    [
        ("reactiontime", (99, 100), (501, 0)),
        ("aim", (249, 100), (1001, 0)),
        ("sequence", (-1, 0), (36, 100)),
        ("number-memory", (-1, 0), (21, 100)),
        ("verbal-memory", (-1, 0), (301, 100)),
        ("chimp", (-1, 0), (41, 100)),
        ("memory", (-1, 0), (36, 100)),
    ],
)
def test_scores_are_clamped_outside_endpoints(
    test_id: str,
    below: tuple[int, int],
    above: tuple[int, int],
) -> None:
    rules = get_rules("talent-training-v1")
    assert score_value(test_id, below[0], rules) == below[1]
    assert score_value(test_id, above[0], rules) == above[1]


def test_reaction_mean_rounds_half_up_before_scoring() -> None:
    payload = valid_payload("reactiontime")
    payload["raw"]["samples_ms"] = [100, 100, 100, 100, 102.5]
    payload["duration_ms"] = 502.5

    result = normalize_result(
        RAW_ADAPTER.validate_python(payload),
        get_rules("talent-training-v1"),
    )

    assert result.raw_value == 101
    assert result.score == 99


@pytest.mark.parametrize("test_id", LEGACY_TEST_IDS)
def test_normalization_returns_stable_versions_and_grade(test_id: str) -> None:
    result = normalize_result(
        RAW_ADAPTER.validate_python(valid_payload(test_id)),
        get_rules("talent-training-v1"),
    )

    assert result.test_id == test_id
    assert result.engine_version == f"{test_id}-clone-v1"
    assert result.scoring_version == "talent-score-v1"
    assert result.locale == "zh-CN"
    assert result.grade in {"low", "medium", "high", "exceptional"}


@pytest.mark.parametrize("test_id", LEGACY_TEST_IDS)
def test_wrong_engine_version_is_rejected(test_id: str) -> None:
    payload = valid_payload(test_id)
    payload["engine_version"] = "wrong-v1"

    with pytest.raises(ValueError, match="engine_version"):
        normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))


def test_reaction_timing_anomaly_uses_individual_and_rounded_summary_thresholds() -> None:
    payload = valid_payload("reactiontime")
    payload["raw"]["samples_ms"] = [79, 105, 105, 105, 105]
    payload["duration_ms"] = 499
    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))
    assert "timing_anomaly" in result.quality_flags

    payload["raw"]["samples_ms"] = [99, 99, 99, 99, 99]
    payload["duration_ms"] = 495
    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))
    assert "timing_anomaly" in result.quality_flags


def test_aim_timing_anomaly_and_decimal_tail_tolerance() -> None:
    payload = valid_payload("aim")
    payload["raw"]["intervals_ms"] = [149.9] * 30
    payload["raw"]["total_elapsed_ms"] = 4497.0009
    payload["duration_ms"] = 4497.001

    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))

    assert result.raw_value == 150
    assert "timing_anomaly" in result.quality_flags


def test_aim_uses_the_mean_not_an_individual_interval_for_timing_quality() -> None:
    payload = valid_payload("aim")
    payload["raw"]["intervals_ms"] = [149, *([301] * 29)]
    payload["raw"]["total_elapsed_ms"] = 8878
    payload["duration_ms"] = 8878

    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))

    assert "timing_anomaly" not in result.quality_flags


def test_unknown_input_and_interruption_are_quality_flags() -> None:
    payload = valid_payload("sequence")
    payload["device"]["input_kind"] = "unknown"
    payload["background_interrupted"] = True
    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))
    assert {"unknown_input", "background_interrupted"} <= set(result.quality_flags)


@pytest.mark.parametrize("input_kind", ["touch", "pen", "mixed"])
def test_device_applicability_does_not_create_a_quality_warning(input_kind: str) -> None:
    baseline_payload = valid_payload("sequence")
    baseline = normalize_result(
        RAW_ADAPTER.validate_python(baseline_payload),
        get_rules("talent-training-v1"),
    )
    payload = valid_payload("sequence")
    payload["device"]["input_kind"] = input_kind
    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))

    assert result.quality_flags == baseline.quality_flags == []
    assert result.score == baseline.score
    assert result.grade == baseline.grade


def test_zero_duration_is_protocol_valid_but_flagged_for_retest() -> None:
    payload = valid_payload("sequence")
    payload["duration_ms"] = 0

    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))

    assert "timing_anomaly" in result.quality_flags


def test_unknown_catalog_and_test_are_rejected_by_catalog_boundary() -> None:
    with pytest.raises(UnknownCatalogVersion):
        get_rules("talent-training-v3")
    with pytest.raises(UnknownTestId):
        score_value("typing", 100, get_rules("talent-training-v1"))


def test_fingerprint_ignores_submission_operation_key() -> None:
    first_payload = valid_payload("reactiontime")
    replay_payload = deepcopy(first_payload)
    replay_payload["submission_id"] = "00000000-0000-4000-8000-000000000002"
    first = RAW_ADAPTER.validate_python(first_payload)
    replay = RAW_ADAPTER.validate_python(replay_payload)

    assert fingerprint(first) == fingerprint(replay)


def test_fingerprint_normalizes_equivalent_integral_numbers() -> None:
    class NumberModel(BaseModel):
        value: float

    assert fingerprint(NumberModel(value=200)) == fingerprint(NumberModel(value=200.0))


def test_fingerprint_rejects_non_finite_json_numbers() -> None:
    class NonFiniteModel(BaseModel):
        model_config = ConfigDict(allow_inf_nan=True)
        value: float

    with pytest.raises(ValueError):
        fingerprint(NonFiniteModel(value=nan))


@pytest.mark.parametrize("non_finite", [nan, inf, -inf])
def test_timing_inputs_reject_non_finite_values(non_finite: float) -> None:
    payload = valid_payload("reactiontime")
    payload["raw"]["samples_ms"][0] = non_finite
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)
