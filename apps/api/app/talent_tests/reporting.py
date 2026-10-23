from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from app.talent_tests.catalog import (
    get_dynamic_vision_level,
    get_dynamic_vision_rule_for_result,
    get_rules,
)
from app.talent_tests.schemas import (
    DynamicVisionRaw,
    NormalizedResult,
    PersonDisplay,
    ReportComparison,
    ReportSnapshot,
    RuleSnapshot,
    TestId,
    TestRule,
)

DISCLAIMER = "本报告用于训练参考，受设备、环境和练习影响，不作为智商、诊断或职业潜力判定。"

# These strings are part of talent-report-v1. A future report version should
# add a new mapping instead of silently changing historical report generation.
_ADVICE_BY_REPORT_VERSION: dict[str, dict[TestId, str]] = {
    "talent-report-v1": {
        "reactiontime": "规律作息，固定设备进行少量练习",
        "aim": "先保持准确，再练习平稳移动",
        "sequence": "分组记忆位置与顺序",
        "number-memory": "尝试分组记忆并循序增加位数",
        "verbal-memory": "练习词义联想和重复辨认",
        "chimp": "观察数字空间位置，再按顺序回忆",
        "memory": "分区观察图形位置与结构",
    },
    "talent-report-v2": {
        "reactiontime": "规律作息，固定设备进行少量练习",
        "aim": "先保持准确，再练习平稳移动",
        "sequence": "分组记忆位置与顺序",
        "number-memory": "尝试分组记忆并循序增加位数",
        "verbal-memory": "练习词义联想和重复辨认",
        "chimp": "观察数字空间位置，再按顺序回忆",
        "memory": "分区观察图形位置与结构",
        "dynamic-vision": "在相同显示环境下逐步练习瞬时数字识别",
    },
}
_ADVICE_BY_REPORT_VERSION["talent-report-v3"] = _ADVICE_BY_REPORT_VERSION["talent-report-v2"].copy()

_LABELS: dict[TestId, str] = {
    "reactiontime": "反应",
    "aim": "瞄准",
    "sequence": "顺序",
    "number-memory": "数字",
    "verbal-memory": "词语",
    "chimp": "黑猩猩",
    "memory": "视觉",
    "dynamic-vision": "动态视力",
}
_DEVICE_SENSITIVE = frozenset({"reactiontime", "aim", "chimp", "dynamic-vision"})
_INTERNAL_REFERENCE_INPUTS = frozenset({"touch", "pen", "mixed", "unknown"})
_NON_COMPARABLE_INPUTS = frozenset({"mixed", "unknown"})


def _validate_results(
    results: Sequence[NormalizedResult],
    rules: RuleSnapshot,
) -> dict[TestId, NormalizedResult]:
    canonical = get_rules(rules.catalog_version)
    expected = tuple(canonical.test_ids)
    if rules != canonical:
        raise ValueError("report rules must use the canonical frozen catalog")
    actual = tuple(result.test_id for result in results)
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("report results must contain every frozen catalog test exactly once")
    return {result.test_id: result for result in results}


def _comparison_key(result: NormalizedResult) -> tuple[object, ...]:
    device = result.device
    dynamic_key: tuple[object, ...] = ()
    if result.test_id == "dynamic-vision":
        raw = DynamicVisionRaw.model_validate(result.raw.model_dump())
        rule = get_dynamic_vision_rule_for_result(result.scoring_version, result.engine_version)
        assert rule is not None
        level = get_dynamic_vision_level(rule, raw.selected_level)
        dynamic_key = (
            raw.selected_level,
            raw.font_size_px,
            level.digit_count,
            level.nominal_display_ms,
        )
    return (
        result.engine_version,
        result.scoring_version,
        result.locale,
        device.input_kind,
        device.viewport_width,
        device.viewport_height,
        device.geometry.model_dump(mode="json") if device.geometry is not None else None,
        *dynamic_key,
    )


def _is_comparable(current: NormalizedResult, baseline: NormalizedResult) -> bool:
    if current.test_id == "dynamic-vision" and (
        get_dynamic_vision_rule_for_result(current.scoring_version, current.engine_version) is None
        or get_dynamic_vision_rule_for_result(baseline.scoring_version, baseline.engine_version)
        is None
    ):
        return False
    return bool(
        current.test_id == baseline.test_id
        and not current.quality_flags
        and not baseline.quality_flags
        and current.device.input_kind not in _NON_COMPARABLE_INPUTS
        and baseline.device.input_kind not in _NON_COMPARABLE_INPUTS
        and _comparison_key(current) == _comparison_key(baseline)
    )


def _mixed_devices(results: Sequence[NormalizedResult]) -> bool:
    environments = {
        (
            result.device.input_kind,
            result.device.viewport_width,
            result.device.viewport_height,
        )
        for result in results
    }
    return len(environments) > 1


def _qualified_advice(result: NormalizedResult, advice: Mapping[TestId, str]) -> str:
    prefix = "本次操作环境下，" if result.test_id in _DEVICE_SENSITIVE else ""
    return f"{prefix}{advice[result.test_id]}"


def _advice_for(result: NormalizedResult, advice: Mapping[TestId, str]) -> str:
    return f"{_LABELS[result.test_id]}：{_qualified_advice(result, advice)}"


def _suggestions(
    results: Sequence[NormalizedResult],
    *,
    rules: RuleSnapshot,
) -> list[str]:
    report_version = rules.report_version
    try:
        advice = _ADVICE_BY_REPORT_VERSION[report_version]
    except KeyError as exc:
        raise ValueError(f"unsupported report version: {report_version}") from exc

    retests = [
        f"{_LABELS[result.test_id]}：需复测；{_qualified_advice(result, advice)}"
        for result in results
        if result.quality_flags
    ]
    eligible = [result for result in results if not result.quality_flags]

    def can_level_up(result: NormalizedResult) -> bool:
        if result.test_id != "dynamic-vision" or not isinstance(result.raw, DynamicVisionRaw):
            return False
        rule = rules.tests["dynamic-vision"]
        return (
            not isinstance(rule, TestRule)
            and result.raw.selected_level < max(level.level for level in rule.levels)
            and result.raw_value >= 4
        )

    dynamic_level_up = [
        "动态视力：可在新一轮尝试更高难度" for result in eligible if can_level_up(result)
    ]
    low = [result for result in eligible if result.score < 40]
    medium = [result for result in eligible if 40 <= result.score < 70]
    selected = low or medium
    training = [_advice_for(result, advice) for result in selected if not can_level_up(result)]
    if not selected and eligible:
        training = ["保持当前训练节奏，并在同一设备条件下定期复测"]

    applicability = [
        (
            f"{_LABELS[result.test_id]}：该设备下仅作内部训练参考；"
            + ("本次操作环境下的表现" if result.test_id in _DEVICE_SENSITIVE else "不作跨设备结论")
        )
        for result in results
        if result.device.input_kind in _INTERNAL_REFERENCE_INPUTS
    ]
    mixed_device_note = (
        ["本轮使用了不同设备，各项结果仅按本次操作环境作内部训练参考，不作跨设备进步结论"]
        if _mixed_devices(results)
        else []
    )
    qualified_strengths = [
        f"{_LABELS[result.test_id]}：较高或超高分仅表示本次操作环境下的表现"
        for result in eligible
        if result.score >= 70 and result.test_id in _DEVICE_SENSITIVE
    ]
    return [
        *retests,
        *dynamic_level_up,
        *training,
        *qualified_strengths,
        *applicability,
        *mixed_device_note,
    ]


def build_report(
    *,
    session_id: UUID,
    person: PersonDisplay,
    completed_at: datetime,
    rules: RuleSnapshot,
    results: Sequence[NormalizedResult],
    baselines: Mapping[str, tuple[UUID, NormalizedResult]],
) -> ReportSnapshot:
    """Build the immutable talent-report-v1 payload persisted by the write service."""

    by_test = _validate_results(results, rules)
    ordered = [by_test[test_id].model_copy(deep=True) for test_id in rules.test_ids]
    strengths = [
        result.test_id for result in ordered if not result.quality_flags and result.score >= 70
    ]
    comparisons: dict[TestId, ReportComparison] = {}
    for result in ordered:
        baseline_entry = baselines.get(result.test_id)
        if baseline_entry is None:
            continue
        baseline_report_id, baseline = baseline_entry
        if not _is_comparable(result, baseline):
            continue
        comparisons[result.test_id] = ReportComparison(
            baseline_report_id=baseline_report_id,
            raw_delta=result.raw_value - baseline.raw_value,
            score_delta=result.score - baseline.score,
        )

    return ReportSnapshot(
        person=person.model_copy(deep=True),
        session_id=session_id,
        completed_at=completed_at,
        rules=rules.model_copy(deep=True),
        results=ordered,
        strengths=strengths,
        suggestions=_suggestions(ordered, rules=rules),
        comparisons=comparisons,
        mixed_devices=_mixed_devices(ordered),
        disclaimer=DISCLAIMER,
    )
