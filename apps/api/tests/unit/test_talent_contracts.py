from copy import deepcopy
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.talent_tests.catalog import get_rules
from app.talent_tests.schemas import (
    ChimpResultInput,
    MemoryResultInput,
    NumberMemoryResultInput,
    RawResultInput,
)
from tests.unit.talent_test_helpers import valid_payload

RAW_ADAPTER: TypeAdapter[RawResultInput] = TypeAdapter(RawResultInput)


@pytest.mark.parametrize(
    ("completed", "misses", "valid"),
    [(0, 9, True), (0, 10, False), (3, 9, True), (3, 15, True), (3, 16, False)],
)
def test_visual_miss_bounds_follow_lives_and_passed_rounds(
    completed: int, misses: int, valid: bool
) -> None:
    payload = valid_payload("memory")
    payload["raw"].update(
        completed_level=completed, reached_level=completed + 1, accepted_misses=misses
    )
    if valid:
        parsed = RAW_ADAPTER.validate_python(payload)
        assert isinstance(parsed, MemoryResultInput)
        assert parsed.raw.accepted_misses == misses
    else:
        with pytest.raises(ValidationError, match="accepted_misses"):
            RAW_ADAPTER.validate_python(payload)


def test_catalog_is_exactly_seven_without_typing() -> None:
    rules = get_rules("talent-training-v1")
    assert tuple(rules.test_ids) == (
        "reactiontime",
        "aim",
        "sequence",
        "number-memory",
        "verbal-memory",
        "chimp",
        "memory",
    )
    assert rules.catalog_version == "talent-training-v1"
    assert rules.scoring_version == "talent-score-v1"
    assert rules.report_version == "talent-report-v1"
    assert rules.tests["verbal-memory"].source_label == "内部试行训练目标"
    assert rules.tests["memory"].source_label == "内部试行训练目标"


@pytest.mark.parametrize(
    "test_id",
    ["reactiontime", "aim", "sequence", "number-memory", "verbal-memory", "chimp", "memory"],
)
def test_all_seven_minimum_json_submissions_validate(test_id: str) -> None:
    parsed = RAW_ADAPTER.validate_python(valid_payload(test_id))
    assert parsed.test_id == test_id
    assert parsed.model_dump(mode="json")["raw"] == valid_payload(test_id)["raw"]


def test_discriminator_rejects_a_raw_shape_from_another_test() -> None:
    payload = valid_payload("reactiontime")
    payload["raw"] = valid_payload("aim")["raw"]
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("test_id", ["reactiontime", "aim", "sequence"])
def test_extra_fields_are_forbidden_at_every_input_level(test_id: str) -> None:
    payload = valid_payload(test_id)
    payload["client_score"] = 100
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)

    payload = valid_payload(test_id)
    payload["raw"]["answer"] = "secret"
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)

    payload = valid_payload(test_id)
    payload["device"]["fingerprint"] = "forbidden"
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("test_id", "field", "value"),
    [
        ("reactiontime", "too_soon_count", False),
        ("aim", "miss_count", False),
        ("sequence", "reached_level", True),
        ("number-memory", "correct_digits", False),
        ("verbal-memory", "correct_count", False),
        ("chimp", "completed_count", False),
        ("memory", "completed_level", False),
    ],
)
def test_boolean_pseudo_integers_are_rejected(test_id: str, field: str, value: bool) -> None:
    payload = valid_payload(test_id)
    payload["raw"][field] = value
    with pytest.raises(ValidationError) as caught:
        RAW_ADAPTER.validate_python(payload)
    assert any(
        error["loc"] == (test_id, "raw", field) and error["type"] == "int_type"
        for error in caught.value.errors()
    )


@pytest.mark.parametrize(
    "test_id",
    ["reactiontime", "aim", "sequence", "number-memory", "verbal-memory", "chimp", "memory"],
)
def test_duration_is_bounded(test_id: str) -> None:
    payload = valid_payload(test_id)
    payload["duration_ms"] = 86_400_000.001
    with pytest.raises(ValidationError) as caught:
        RAW_ADAPTER.validate_python(payload)
    assert any(
        error["loc"] == (test_id, "duration_ms") and error["type"] == "less_than_equal"
        for error in caught.value.errors()
    )


def test_number_memory_accepts_integer_display_duration_above_counter_limit() -> None:
    payload = valid_payload("number-memory")
    payload["raw"] = {
        "correct_digits": 1249,
        "failed_digits": 1250,
        "display_duration_ms": 1_001_000,
    }
    payload["duration_ms"] = 1_001_000

    parsed = RAW_ADAPTER.validate_python(payload)

    assert isinstance(parsed, NumberMemoryResultInput)
    assert parsed.raw.display_duration_ms == 1_001_000


@pytest.mark.parametrize("display_duration_ms", [86_400_001, True, "1800"])
def test_number_memory_display_duration_is_a_strict_bounded_integer(
    display_duration_ms: object,
) -> None:
    payload = valid_payload("number-memory")
    payload["raw"]["display_duration_ms"] = display_duration_ms
    with pytest.raises(ValidationError) as caught:
        RAW_ADAPTER.validate_python(payload)
    expected = "less_than_equal" if display_duration_ms == 86_400_001 else "int_type"
    assert any(
        error["loc"] == ("number-memory", "raw", "display_duration_ms")
        and error["type"] == expected
        for error in caught.value.errors()
    )


@pytest.mark.parametrize(
    ("test_id", "changes", "field"),
    [
        ("reactiontime", {"too_soon_count": 1_000_001}, "too_soon_count"),
        ("aim", {"miss_count": 1_000_001}, "miss_count"),
        ("sequence", {"reached_level": 1_000_001, "completed_level": 1_000_000}, "reached_level"),
        ("verbal-memory", {"correct_count": 999_998, "total_count": 1_000_001}, "total_count"),
        ("chimp", {"reached_count": 41, "completed_count": 40}, "reached_count"),
        ("memory", {"reached_level": 1_000_001, "completed_level": 1_000_000}, "reached_level"),
    ],
)
def test_counter_upper_bound_is_not_a_cross_field_failure(
    test_id: str, changes: dict[str, int], field: str
) -> None:
    payload = valid_payload(test_id)
    payload["raw"].update(changes)
    with pytest.raises(ValidationError) as caught:
        RAW_ADAPTER.validate_python(payload)
    assert any(
        error["loc"] == (test_id, "raw", field) and error["type"] == "less_than_equal"
        for error in caught.value.errors()
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("duration_ms",), "1000"),
        (("device", "viewport_width"), "1280"),
        (("raw", "samples_ms", 0), "200"),
    ],
)
def test_numeric_strings_are_not_coerced(
    path: tuple[str | int, ...],
    value: str,
) -> None:
    payload = valid_payload("reactiontime")
    cursor: Any = payload
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


def test_reaction_requires_five_nonnegative_samples_and_covering_duration() -> None:
    payload = valid_payload("reactiontime")
    payload["raw"]["samples_ms"] = [200] * 4
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)

    payload = valid_payload("reactiontime")
    payload["raw"]["samples_ms"][0] = -1
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)

    payload = valid_payload("reactiontime")
    payload["duration_ms"] = 999.999
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


def test_aim_requires_30_intervals_exact_totals_and_valid_geometry() -> None:
    payload = valid_payload("aim")
    payload["raw"]["intervals_ms"] = [300] * 29
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)

    payload = valid_payload("aim")
    payload["raw"]["total_elapsed_ms"] = 9000.0011
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)

    payload = valid_payload("aim")
    payload["duration_ms"] = 9000.0011
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)

    payload = valid_payload("aim")
    payload["device"]["geometry"] = None
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)

    payload = valid_payload("aim")
    payload["device"]["geometry"]["target_diameter"] = 721
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("test_id", "changes"),
    [
        ("sequence", {"completed_level": 1}),
        ("sequence", {"failed_step": 2}),
        ("number-memory", {"failed_digits": 2}),
        ("number-memory", {"display_duration_ms": 1801}),
        ("verbal-memory", {"error_count": 2}),
        ("verbal-memory", {"total_count": 4}),
        ("verbal-memory", {"word_bank_version": "en-v1"}),
        ("chimp", {"reached_count": 3}),
        ("chimp", {"completed_count": 3}),
        ("chimp", {"strikes": 2}),
        ("memory", {"completed_level": 1}),
        ("memory", {"accepted_misses": 8}),
        ("memory", {"lives_used": 2}),
    ],
)
def test_cross_field_invariants_reject_contradictions(
    test_id: str,
    changes: dict[str, Any],
) -> None:
    payload = valid_payload(test_id)
    payload["raw"].update(changes)
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)


def test_chimp_engine_cap_shape_is_valid_and_strike_shape_is_bounded() -> None:
    cap = valid_payload("chimp")
    cap["raw"] = {
        "reached_count": 40,
        "completed_count": 40,
        "strikes": 2,
        "end_reason": "engine_cap",
    }
    parsed = RAW_ADAPTER.validate_python(cap)
    assert isinstance(parsed, ChimpResultInput)
    assert parsed.raw.end_reason == "engine_cap"

    invalid = deepcopy(cap)
    invalid["raw"]["strikes"] = 3
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(invalid)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("device", "viewport_width"), 0),
        (("device", "viewport_height"), 1_000_000.1),
        (("device", "geometry", "width"), float("inf")),
        (("device", "geometry", "height"), -1),
        (("device", "geometry", "target_diameter"), 0),
    ],
)
def test_viewport_and_geometry_require_finite_positive_bounded_values(
    path: tuple[str, ...],
    value: float,
) -> None:
    payload = valid_payload("aim")
    cursor: dict[str, Any] = payload
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value
    with pytest.raises(ValidationError):
        RAW_ADAPTER.validate_python(payload)
