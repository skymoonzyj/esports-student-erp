from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import TypeAdapter

from app.talent_tests.catalog import LEGACY_TEST_IDS, TEST_IDS, get_rules
from app.talent_tests.reporting import build_report
from app.talent_tests.schemas import (
    InputKind,
    NormalizedResult,
    PersonDisplay,
    RawResultInput,
    ReportSnapshot,
)
from app.talent_tests.schemas import TestRule as FrozenTestRule
from app.talent_tests.scoring import normalize_result
from tests.unit.talent_test_helpers import valid_payload

RAW_ADAPTER: TypeAdapter[RawResultInput] = TypeAdapter(RawResultInput)
SESSION_ID = UUID("10000000-0000-4000-8000-000000000001")
PERSON_ID = UUID("20000000-0000-4000-8000-000000000002")
BASELINE_ID = UUID("30000000-0000-4000-8000-000000000003")
COMPLETED_AT = datetime(2026, 9, 20, 8, tzinfo=UTC)


def normalized_seven() -> list[NormalizedResult]:
    rules = get_rules("talent-training-v1")
    return [
        normalize_result(RAW_ADAPTER.validate_python(valid_payload(test_id)), rules)
        for test_id in LEGACY_TEST_IDS
    ]


def normalized_eight() -> list[NormalizedResult]:
    rules = get_rules("talent-training-v2")
    return [
        normalize_result(RAW_ADAPTER.validate_python(valid_payload(test_id)), rules)
        for test_id in TEST_IDS
    ]


def report_for(
    results: list[NormalizedResult],
    baselines: dict[str, tuple[UUID, NormalizedResult]] | None = None,
) -> ReportSnapshot:
    return build_report(
        session_id=SESSION_ID,
        person=PersonDisplay(id=PERSON_ID, name="测试学员", nickname="小测"),
        completed_at=COMPLETED_AT,
        rules=get_rules("talent-training-v1"),
        results=results,
        baselines=baselines or {},
    )


def report_for_v2(
    results: list[NormalizedResult],
    baselines: dict[str, tuple[UUID, NormalizedResult]] | None = None,
) -> ReportSnapshot:
    return build_report(
        session_id=SESSION_ID,
        person=PersonDisplay(id=PERSON_ID, name="测试学员", nickname="小测"),
        completed_at=COMPLETED_AT,
        rules=get_rules("talent-training-v2"),
        results=results,
        baselines=baselines or {},
    )


def dynamic_result(
    *, level: int, correct: int, measured_ms: float | None = None
) -> NormalizedResult:
    payload = valid_payload("dynamic-vision")
    payload["raw"].update(
        selected_level=level,
        outcomes=["correct"] * correct + ["incorrect"] * (5 - correct),
        measured_display_ms=[measured_ms or (100 if level <= 3 else 36)] * 5,
    )
    return normalize_result(
        RAW_ADAPTER.validate_python(payload),
        get_rules("talent-training-v2"),
    )


def _result(results: list[NormalizedResult], test_id: str) -> NormalizedResult:
    return next(result for result in results if result.test_id == test_id)


@pytest.mark.parametrize(
    ("test_id", "reached", "score"),
    [
        ("sequence", 18, 70),
        ("sequence", 25, 90),
        ("memory", 18, 70),
        ("memory", 25, 90),
        ("chimp", 15, 70),
        ("chimp", 20, 90),
    ],
)
def test_reached_difficulty_drives_normalized_report_score(
    test_id: str, reached: int, score: int
) -> None:
    payload = valid_payload(test_id)
    suffix = "count" if test_id == "chimp" else "level"
    payload["raw"].update({f"reached_{suffix}": reached, f"completed_{suffix}": reached - 1})
    normalized = normalize_result(
        RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1")
    )
    results = [normalized if item.test_id == test_id else item for item in normalized_seven()]
    report = report_for(results)
    saved = _result(report.results, test_id)
    assert (saved.raw_value, saved.score) == (reached, score)
    assert getattr(saved.raw, f"completed_{suffix}") == reached - 1
    assert test_id in report.strengths


@pytest.mark.parametrize(
    ("samples", "anomalous"), [([99, 100, 100, 100, 99], True), ([100] * 5, False)]
)
def test_precise_reaction_mean_controls_report_quality(samples: list[int], anomalous: bool) -> None:
    payload = valid_payload("reactiontime")
    payload["raw"]["samples_ms"] = samples
    result = normalize_result(RAW_ADAPTER.validate_python(payload), get_rules("talent-training-v1"))
    assert result.raw_value == 100
    assert result.score == 100
    assert ("timing_anomaly" in result.quality_flags) is anomalous
    report = report_for([result, *normalized_seven()[1:]])
    assert ("reactiontime" in report.strengths) is not anomalous


@pytest.mark.parametrize("input_kind", ["mouse", "touch"])
def test_all_sensitive_strengths_are_environment_qualified(input_kind: InputKind) -> None:
    results = normalized_seven()
    for result in results:
        result.score = 90
        result.device.input_kind = input_kind
    report = report_for(results)
    for label in ("反应", "瞄准", "黑猩猩"):
        assert any(
            item.startswith(f"{label}：") and "本次操作环境下的表现" in item
            for item in report.suggestions
        )


def test_warning_never_becomes_strength_or_weakness() -> None:
    results = normalized_seven()
    reaction = _result(results, "reactiontime")
    reaction.quality_flags = ["timing_anomaly"]
    reaction.score = 100
    reaction.grade = "exceptional"

    report = report_for(results)

    assert "reactiontime" not in report.strengths
    assert "需复测" in "\n".join(report.suggestions)
    assert "总天赋分" not in report.model_dump_json()


def test_report_requires_each_frozen_catalog_test_exactly_once_and_sorts_results() -> None:
    results = normalized_seven()
    report = report_for(list(reversed(results)))
    assert [result.test_id for result in report.results] == list(LEGACY_TEST_IDS)

    with pytest.raises(ValueError, match="exactly once"):
        report_for(results[:-1])
    with pytest.raises(ValueError, match="exactly once"):
        report_for([*results[:-1], results[0]])


def test_report_rejects_a_self_consistent_but_noncanonical_six_test_rule_snapshot() -> None:
    results = normalized_seven()
    rules = get_rules("talent-training-v1")
    malformed = rules.model_copy(
        update={
            "test_ids": rules.test_ids[:-1],
            "tests": {
                test_id: rule for test_id, rule in rules.tests.items() if test_id != "memory"
            },
        }
    )

    with pytest.raises(ValueError, match="canonical frozen catalog"):
        build_report(
            session_id=SESSION_ID,
            person=PersonDisplay(id=PERSON_ID, name="测试学员", nickname=None),
            completed_at=COMPLETED_AT,
            rules=malformed,
            results=results[:-1],
            baselines={},
        )


def test_low_suggestions_take_priority_then_medium_and_all_high_gets_maintenance() -> None:
    results = normalized_seven()
    for result in results:
        result.score = 70
        result.grade = "high"
    _result(results, "sequence").score = 20
    _result(results, "sequence").grade = "low"
    _result(results, "aim").score = 50
    _result(results, "aim").grade = "medium"
    report = report_for(results)
    assert any("分组记忆位置与顺序" in suggestion for suggestion in report.suggestions)
    assert all("先保持准确" not in suggestion for suggestion in report.suggestions)

    _result(results, "sequence").score = 70
    _result(results, "sequence").grade = "high"
    report = report_for(results)
    assert any("先保持准确，再练习平稳移动" in item for item in report.suggestions)

    _result(results, "aim").score = 70
    _result(results, "aim").grade = "high"
    report = report_for(results)
    assert any("同一设备" in item for item in report.suggestions)


def test_device_applicability_is_internal_reference_not_a_quality_warning() -> None:
    results = normalized_seven()
    reaction = _result(results, "reactiontime")
    reaction.score = 70
    reaction.grade = "high"
    reaction.device.input_kind = "touch"

    report = report_for(results)

    assert "reactiontime" in report.strengths
    assert "需复测" not in "\n".join(report.suggestions)
    assert "内部训练参考" in "\n".join(report.suggestions)
    assert "本次操作环境下" in "\n".join(report.suggestions)


def test_device_sensitive_retest_advice_keeps_the_environment_qualifier() -> None:
    results = normalized_seven()
    reaction = _result(results, "reactiontime")
    reaction.quality_flags = ["timing_anomaly"]

    report = report_for(results)

    reaction_retest = next(
        item for item in report.suggestions if item.startswith("反应：") and "需复测" in item
    )
    assert "本次操作环境下" in reaction_retest


def test_mixed_devices_uses_input_and_viewport_but_not_cross_test_geometry() -> None:
    results = normalized_seven()
    assert report_for(results).mixed_devices is False

    _result(results, "sequence").device.viewport_width = 1024
    report = report_for(results)
    assert report.mixed_devices is True
    assert "本轮使用了不同设备" in "\n".join(report.suggestions)


def test_comparisons_require_warning_free_identical_versions_and_devices() -> None:
    current = normalized_seven()
    current_reaction = _result(current, "reactiontime")
    baseline = current_reaction.model_copy(deep=True)
    baseline.raw_value = 220
    baseline.score = 52

    report = report_for(current, {"reactiontime": (BASELINE_ID, baseline)})
    assert report.comparisons["reactiontime"].baseline_report_id == BASELINE_ID
    assert report.comparisons["reactiontime"].raw_delta == -20
    assert report.comparisons["reactiontime"].score_delta == 8

    mutations = (
        lambda value: setattr(value.device, "viewport_width", 1024),
        lambda value: setattr(value.device, "input_kind", "touch"),
        lambda value: setattr(value, "locale", "zh-CN-x-private"),
        lambda value: value.quality_flags.append("timing_anomaly"),
        lambda value: setattr(value, "engine_version", "reactiontime-clone-v2"),
    )
    for mutate in mutations:
        changed = baseline.model_copy(deep=True)
        mutate(changed)
        assert (
            "reactiontime"
            not in report_for(current, {"reactiontime": (BASELINE_ID, changed)}).comparisons
        )

    current_reaction.device.input_kind = "unknown"
    assert (
        "reactiontime"
        not in report_for(current, {"reactiontime": (BASELINE_ID, baseline)}).comparisons
    )


def test_all_warning_results_still_build_a_complete_report() -> None:
    results = normalized_seven()
    for result in results:
        result.quality_flags = ["timing_anomaly"]

    report = report_for(results)

    assert len(report.results) == 7
    assert report.strengths == []
    assert sum("需复测" in item for item in report.suggestions) == 7


def test_dynamic_vision_low_level_full_score_prefers_higher_difficulty_advice() -> None:
    dynamic = dynamic_result(level=1, correct=5)
    results = [dynamic if item.test_id == "dynamic-vision" else item for item in normalized_eight()]

    report = report_for_v2(results)

    saved = _result(report.results, "dynamic-vision")
    assert (saved.raw_value, saved.score, saved.grade) == (5, 60, "medium")
    assert "dynamic-vision" not in report.strengths
    assert any("可在新一轮尝试更高难度" in item for item in report.suggestions)
    assert not any("能力缺陷" in item for item in report.suggestions)


def test_dynamic_vision_high_score_is_environment_qualified_strength() -> None:
    dynamic = dynamic_result(level=4, correct=4)
    results = [dynamic if item.test_id == "dynamic-vision" else item for item in normalized_eight()]

    report = report_for_v2(results)

    assert (dynamic.raw_value, dynamic.score, dynamic.grade) == (4, 72, "high")
    assert "dynamic-vision" in report.strengths
    assert any(
        item.startswith("动态视力：") and "本次操作环境下的表现" in item
        for item in report.suggestions
    )


def test_dynamic_vision_quality_warning_never_becomes_a_strength() -> None:
    dynamic = dynamic_result(level=4, correct=4, measured_ms=100)
    results = [dynamic if item.test_id == "dynamic-vision" else item for item in normalized_eight()]

    report = report_for_v2(results)

    assert dynamic.quality_flags == ["display_duration_deviation"]
    assert "dynamic-vision" not in report.strengths
    assert any(item.startswith("动态视力：需复测") for item in report.suggestions)


def test_dynamic_comparisons_require_identical_level_font_geometry_and_v2_scoring() -> None:
    current = normalized_eight()
    current_dynamic = _result(current, "dynamic-vision")
    baseline = current_dynamic.model_copy(deep=True)
    baseline.raw_value = 3
    baseline.score = 36

    report = report_for_v2(current, {"dynamic-vision": (BASELINE_ID, baseline)})
    assert report.comparisons["dynamic-vision"].raw_delta == -3
    assert report.comparisons["dynamic-vision"].score_delta == -36

    def change_level(value: NormalizedResult) -> None:
        value.raw.selected_level = 2

    def change_font(value: NormalizedResult) -> None:
        value.raw.font_size_px = 40

    def change_geometry(value: NormalizedResult) -> None:
        assert value.device.geometry is not None
        value.device.geometry.width = 801

    def change_scoring(value: NormalizedResult) -> None:
        value.scoring_version = "talent-score-v3"

    for mutate in (change_level, change_font, change_geometry, change_scoring):
        changed = baseline.model_copy(deep=True)
        mutate(changed)
        assert (
            "dynamic-vision"
            not in report_for_v2(
                current,
                {"dynamic-vision": (BASELINE_ID, changed)},
            ).comparisons
        )


def test_legacy_report_remains_seven_items_without_dynamic_placeholder() -> None:
    report = report_for(normalized_seven())

    assert report.rules.catalog_version == "talent-training-v1"
    assert len(report.results) == 7
    assert "dynamic-vision" not in report.model_dump_json()


def test_snapshot_does_not_follow_later_input_or_catalog_mutation() -> None:
    results = normalized_seven()
    rules = get_rules("talent-training-v1")
    report = build_report(
        session_id=SESSION_ID,
        person=PersonDisplay(id=PERSON_ID, name="旧姓名", nickname=None),
        completed_at=COMPLETED_AT,
        rules=rules,
        results=results,
        baselines={},
    )

    results[0].score = 0
    rules.tests["reactiontime"] = FrozenTestRule(
        engine_version="reactiontime-clone-v2",
        anchors=(600, 400, 300, 200, 150, 100),
        source_label="新规则",
        unit="ms",
    )

    assert report.person.name == "旧姓名"
    assert report.results[0].score == 60
    assert report.rules.tests["reactiontime"].engine_version == "reactiontime-clone-v1"
    assert report.disclaimer == (
        "本报告用于训练参考，受设备、环境和练习影响，不作为智商、诊断或职业潜力判定。"
    )
    forbidden = ("选拔", "职业结论", "智商分")
    assert not any(term in report.model_dump_json() for term in forbidden)
