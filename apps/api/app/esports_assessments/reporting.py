"""Pure, deterministic report assembly and comparison fingerprints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from .catalog import get_suite
from .config_schemas import SuiteConfig, TaskConfig, TestId
from .quality import is_fatal_quality_flag
from .schemas import (
    AssessmentReportView,
    ControlSettings,
    EnvironmentSnapshot,
    PersonDisplaySnapshot,
    ReportComparison,
    ReportResult,
    validate_settings,
)

_LABELS: dict[TestId, str] = {
    "precision": "精准定位与微调",
    "tracking": "持续跟枪",
    "switching": "多目标切换",
    "recognition": "敌我识别",
    "move-fire": "移动与射击协同",
    "last-hit": "补刀时机",
    "orb-walk": "走 A 节奏",
    "dodge": "技能躲避",
    "map-awareness": "小地图注意力",
    "skill-aim": "技能方向控制",
    "move-attack": "移动与攻击协同",
}

# These templates are part of each suite's report v1. A wording change requires
# a new registered report version so persisted reports remain reproducible.
_ADVICE: dict[TestId, str] = {
    "precision": "练习稳定定位，并在保证准确的前提下逐步提速",
    "tracking": "练习稳定跟随，并关注变向后的重新捕获",
    "switching": "练习按当前目标顺序切换，先稳定准确率再提高速度",
    "recognition": "练习先确认形状与文字标识，再进行射击判断",
    "move-fire": "练习在同一轮内同时完成移动到位和有效射击",
    "last-hit": "练习识别补刀窗口，并减少过早或过晚攻击",
    "orb-walk": "练习完成攻击前摇后再移动到检查点",
    "dodge": "练习根据预警方向及时开始有效移动",
    "map-awareness": "练习兼顾主任务与小地图事件，减少漏报和误选",
    "skill-aim": "练习稳定释放方向，并逐步减小方向偏差",
    "move-attack": "练习完成攻击前摇后再移动到检查点",
}

_V2_LABELS: dict[TestId, str] = {
    "switching": "多目标切换",
    "move-fire": "移动与射击协同",
    "dodge": "危险区域识别与躲避",
}

_V2_ADVICE: dict[TestId, str] = {
    "switching": (
        "练习先稳定完成两靶组，再分别关注首次获取与第二靶真实切换耗时，并在保持准确率的前提下提速"
    ),
    "move-fire": "练习保持至少 100ms 有效移动后命中，并兼顾轮末到位和有效射击准确率",
    "dodge": (
        "练习根据危险区域预警完整进入安全区，并在爆发期间保持安全；安全区进入时长仅用于解释本次表现"
    ),
}


def comparison_key(
    config: TaskConfig,
    settings: ControlSettings,
    environment: EnvironmentSnapshot,
) -> str:
    """Fingerprint every frozen rule, control and environment comparison input."""

    canonical = json.dumps(
        {
            "config": config.model_dump(mode="json"),
            "settings": settings.model_dump(mode="json"),
            "environment": environment.model_dump(mode="json"),
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _recommendation(result: ReportResult) -> str:
    uses_v2_report = result.config.report_version == f"{result.config.suite_id}-v2"
    labels = _V2_LABELS if uses_v2_report else _LABELS
    advice = _V2_ADVICE if uses_v2_report else _ADVICE
    label = labels[result.test_id]
    if result.scored.quality_flags:
        return f"{label}：本次存在质量提示，建议在相同设备、设置和稳定环境下复测"
    if result.scored.score >= 70:
        return f"{label}：本次场景表现相对突出；{advice[result.test_id]}"
    if result.scored.score < 40:
        return f"{label}：建议优先练习；{advice[result.test_id]}"
    return f"{label}：{advice[result.test_id]}"


def _expected_grade(score: int) -> str:
    if score < 40:
        return "low"
    if score < 70:
        return "medium"
    if score < 90:
        return "high"
    return "exceptional"


def build_report(
    session_id: UUID,
    person_display: PersonDisplaySnapshot,
    rules: SuiteConfig,
    results: Sequence[ReportResult],
    completed_at: datetime,
    *,
    report_id: UUID,
    comparisons: Sequence[ReportComparison] = (),
) -> AssessmentReportView:
    """Build the immutable report payload persisted by the formal write service."""

    canonical_rules = get_suite(rules.suite_id, catalog_version=rules.catalog_version)
    if rules != canonical_rules:
        raise ValueError("report rules must use the canonical frozen suite")

    actual = tuple(result.test_id for result in results)
    if len(actual) != 4 or len(set(actual)) != 4 or set(actual) != set(rules.test_ids):
        raise ValueError("report results must contain every frozen member exactly once")
    by_test = {result.test_id: result for result in results}
    ordered: list[ReportResult] = []
    for test_id in rules.test_ids:
        result = by_test[test_id]
        if result.config != rules.tasks[test_id]:
            raise ValueError("report result does not use its frozen task configuration")
        validate_settings(result.config, result.settings, result.environment)
        if result.scored.grade != _expected_grade(result.scored.score):
            raise ValueError("score and grade are inconsistent")
        if any(is_fatal_quality_flag(flag) for flag in result.scored.quality_flags):
            raise ValueError("completed report contains fatal quality")
        ordered.append(result.model_copy(deep=True))

    comparison_ids = tuple(item.test_id for item in comparisons)
    if len(set(comparison_ids)) != len(comparison_ids) or any(
        test_id not in rules.test_ids for test_id in comparison_ids
    ):
        raise ValueError("report comparisons must reference unique frozen members")
    comparison_by_test = {item.test_id: item for item in comparisons}
    ordered_comparisons = tuple(
        comparison_by_test[test_id].model_copy(
            update={"score_delta": None}
            if comparison_by_test[test_id].non_comparable_reasons
            else {},
            deep=True,
        )
        for test_id in rules.test_ids
        if test_id in comparison_by_test
    )
    ordered_results: tuple[ReportResult, ReportResult, ReportResult, ReportResult] = (
        ordered[0],
        ordered[1],
        ordered[2],
        ordered[3],
    )

    return AssessmentReportView(
        id=report_id,
        session_id=session_id,
        suite_id=rules.suite_id,
        suite=canonical_rules,
        person=person_display.model_copy(deep=True),
        completed_at=completed_at,
        results=ordered_results,
        recommendations=tuple(_recommendation(result) for result in ordered_results),
        comparisons=ordered_comparisons,
    )
