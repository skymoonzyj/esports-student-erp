from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from app.talent_tests.catalog import CURRENT_CATALOG_VERSION, get_rules
from app.talent_tests.reporting import _is_comparable, build_report
from app.talent_tests.schemas import PersonDisplay, RawResultInput, ReportSnapshot, RuleSnapshot
from app.talent_tests.scoring import fingerprint, normalize_result
from tests.unit.talent_test_helpers import valid_payload

ADAPTER: TypeAdapter[RawResultInput] = TypeAdapter(RawResultInput)


def dynamic_payload(level: int, correct: int = 5):
    payload = valid_payload("dynamic-vision")
    payload["engine_version"] = "dynamic-vision-v2"
    payload["raw"].update(
        selected_level=level,
        outcomes=["correct"] * correct + ["incorrect"] * (5 - correct),
        measured_display_ms=[100 if level < 4 else 36] * 5,
    )
    return payload


def test_current_catalog_serves_six_levels_and_explicit_cap_without_rewriting_history():
    current = get_rules(CURRENT_CATALOG_VERSION)
    assert (current.catalog_version, current.scoring_version, current.report_version) == (
        "talent-training-v3",
        "talent-score-v3",
        "talent-report-v3",
    )
    rule = current.model_dump(mode="json")["tests"]["dynamic-vision"]
    assert rule["engine_version"] == "dynamic-vision-v2"
    assert rule["max_score"] == 100
    assert rule["levels"][-1] == {
        "level": 6,
        "digit_count": 7,
        "nominal_display_ms": 36,
        "coefficient_tenths": 22,
    }
    for version, digest in (
        ("talent-training-v1", "a85febb60ebc25704c1ed91f88342b1f6f5478dfa00ec978bccb4e34a86629c3"),
        ("talent-training-v2", "c89ea3c6314d5d81912fd77bc073900e3c805a7038539e16cc5371fe64f77bfa"),
    ):
        historical = get_rules(version)
        assert fingerprint(historical) == digest
        assert RuleSnapshot.model_validate(historical.model_dump(mode="json")) == historical
    assert (
        rule["levels"][:5]
        == get_rules("talent-training-v2").model_dump(mode="json")["tests"]["dynamic-vision"][
            "levels"
        ]
    )


@pytest.mark.parametrize("correct,expected", [(0, 0), (1, 22), (2, 44), (3, 66), (4, 88), (5, 100)])
def test_level_six_scores_twenty_two_per_correct_and_caps_at_one_hundred(correct, expected):
    result = normalize_result(
        ADAPTER.validate_python(dynamic_payload(6, correct)), get_rules("talent-training-v3")
    )
    assert (result.raw_value, result.score, result.engine_version, result.scoring_version) == (
        correct,
        expected,
        "dynamic-vision-v2",
        "talent-score-v3",
    )
    assert result.quality_flags == []


def test_legacy_engine_rejects_level_six_even_when_raw_schema_supports_new_engine():
    payload = dynamic_payload(6)
    payload["engine_version"] = "dynamic-vision-v1"
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(payload)


@pytest.mark.parametrize("level", [True, False, 0, 7, 6.0, "6"])
def test_new_engine_requires_a_strict_supported_level(level):
    payload = dynamic_payload(6)
    payload["raw"]["selected_level"] = level
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(payload)


def test_old_rule_cannot_accept_six_levels_or_new_cap_field():
    snapshot = get_rules("talent-training-v2").model_dump(mode="json")
    snapshot["tests"]["dynamic-vision"]["levels"].append(
        {
            "level": 6,
            "digit_count": 7,
            "nominal_display_ms": 36,
            "coefficient_tenths": 22,
        }
    )
    with pytest.raises(ValidationError):
        RuleSnapshot.model_validate(snapshot)
    snapshot = get_rules("talent-training-v2").model_dump(mode="json")
    snapshot["tests"]["dynamic-vision"]["max_score"] = 100
    with pytest.raises(ValidationError):
        RuleSnapshot.model_validate(snapshot)


def test_report_comparisons_keep_new_and_old_scoring_cohorts_separate():
    current = normalize_result(
        ADAPTER.validate_python(dynamic_payload(5)), get_rules("talent-training-v3")
    )
    old_payload = dynamic_payload(5)
    old_payload["engine_version"] = "dynamic-vision-v1"
    old = normalize_result(ADAPTER.validate_python(old_payload), get_rules("talent-training-v2"))
    assert _is_comparable(current, current.model_copy(deep=True))
    assert _is_comparable(old, old.model_copy(deep=True))
    assert not _is_comparable(current, old)
    assert not _is_comparable(old, current)


@pytest.mark.parametrize(
    "catalog,engine",
    [("talent-training-v2", "dynamic-vision-v2"), ("talent-training-v3", "dynamic-vision-v1")],
)
def test_engine_must_match_the_sessions_frozen_catalog(catalog, engine):
    payload = dynamic_payload(5)
    payload["engine_version"] = engine
    with pytest.raises(ValueError, match="frozen catalog"):
        normalize_result(ADAPTER.validate_python(payload), get_rules(catalog))


@pytest.mark.parametrize("level,up", [(5, True), (6, False)])
def test_v3_report_uses_frozen_max_level_and_keeps_explicit_cap(level, up):
    rules = get_rules("talent-training-v3")
    results = [
        normalize_result(
            ADAPTER.validate_python(
                dynamic_payload(level, 4) if test_id == "dynamic-vision" else valid_payload(test_id)
            ),
            rules,
        )
        for test_id in rules.test_ids
    ]
    dynamic = results[-1]
    baseline = dynamic.model_copy(deep=True)
    baseline.raw_value = 3
    baseline.score = 66 if level == 6 else 60
    report = build_report(
        session_id=UUID(int=1),
        person=PersonDisplay(id=UUID(int=2), name="测试", nickname=None),
        completed_at=datetime(2026, 10, 7, tzinfo=UTC),
        rules=rules,
        results=results,
        baselines={"dynamic-vision": (UUID(int=3), baseline)},
    )
    assert ("动态视力：可在新一轮尝试更高难度" in report.suggestions) is up
    assert report.comparisons["dynamic-vision"].score_delta == (22 if level == 6 else 20)
    saved = report.model_dump(mode="json")
    assert saved["rules"]["tests"]["dynamic-vision"]["max_score"] == 100
    assert ReportSnapshot.model_validate(saved) == report
