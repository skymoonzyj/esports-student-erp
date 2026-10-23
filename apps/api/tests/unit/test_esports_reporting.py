from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.esports_assessments.catalog import get_suite
from app.esports_assessments.reporting import build_report, comparison_key
from app.esports_assessments.schemas import (
    ControlSettings,
    EnvironmentSnapshot,
    PersonDisplaySnapshot,
    ReportComparison,
    ReportResult,
    ScoredResult,
)

REPORT_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
PERSON_ID = UUID("33333333-3333-3333-3333-333333333333")
COMPLETED_AT = datetime(2026, 9, 22, 8, 30, tzinfo=UTC)


def settings(suite_id: str, *, sensitivity: float = 1) -> ControlSettings:
    if suite_id == "pc-fps":
        return ControlSettings(
            sensitivity=sensitivity,
            input_kind="mouse",
            mouse_mode="raw",
        )
    if suite_id == "pc-moba":
        return ControlSettings(sensitivity=sensitivity, input_kind="mouse")
    return ControlSettings(sensitivity=sensitivity, input_kind="touch")


def environment(
    suite_id: str,
    *,
    width_css_px: int | None = None,
    input_kind: str | None = None,
) -> EnvironmentSnapshot:
    mobile = suite_id.startswith("mobile")
    return EnvironmentSnapshot.model_validate(
        {
            "input_kind": input_kind or ("touch" if mobile else "mouse"),
            "is_phone": mobile,
            "touch_capable": mobile,
            "width_css_px": width_css_px or (844 if mobile else 1280),
            "height_css_px": 390 if mobile else 720,
            "device_pixel_ratio": 2 if mobile else 1,
            "pointer_lock_supported": suite_id == "pc-fps",
            "pointer_lock_active": suite_id == "pc-fps",
            "raw_mouse_requested": suite_id == "pc-fps",
            "raw_mouse_confirmed": suite_id == "pc-fps",
            "webgl2_supported": "fps" in suite_id,
        }
    )


def report_results(
    suite_id: str = "pc-fps",
    *,
    scores: tuple[int, int, int, int] = (95, 75, 55, 25),
    flagged_index: int | None = None,
) -> list[ReportResult]:
    suite = get_suite(suite_id, catalog_version=f"{suite_id}-v2")
    results: list[ReportResult] = []
    for index, test_id in enumerate(suite.test_ids):
        score = scores[index]
        grade = (
            "low"
            if score < 40
            else "medium"
            if score < 70
            else "high"
            if score < 90
            else "exceptional"
        )
        results.append(
            ReportResult(
                result_id=UUID(int=index + 10),
                test_id=test_id,
                config=suite.tasks[test_id],
                settings=settings(suite_id),
                environment=environment(suite_id),
                scored=ScoredResult(
                    metrics={"literal_metric": index},
                    score=score,
                    grade=grade,
                    quality_flags=("long_frames",) if index == flagged_index else (),
                ),
            )
        )
    return results


def build_pc_report(
    results: list[ReportResult],
    *,
    comparisons: tuple[ReportComparison, ...] = (),
):
    return build_report(
        SESSION_ID,
        PersonDisplaySnapshot(person_id=PERSON_ID, display_name="测试学员"),
        get_suite("pc-fps", catalog_version="pc-fps-v2"),
        results,
        COMPLETED_AT,
        report_id=REPORT_ID,
        comparisons=comparisons,
    )


def test_report_requires_four_unique_frozen_members_and_orders_them() -> None:
    results = report_results()

    report = build_pc_report(list(reversed(results)))

    assert report.id == REPORT_ID
    assert report.session_id == SESSION_ID
    assert tuple(item.test_id for item in report.results) == (
        "precision",
        "tracking",
        "switching",
        "recognition",
    )
    assert not hasattr(report, "total_score")
    assert not hasattr(report, "talent_index")

    with pytest.raises(ValueError, match="exactly once"):
        build_pc_report(results[:3])
    with pytest.raises(ValueError, match="exactly once"):
        build_pc_report([results[0], results[0], results[2], results[3]])


def test_report_rejects_result_config_that_is_not_the_frozen_member_config() -> None:
    results = report_results()
    wrong = report_results("mobile-fps")[0]
    results[0] = wrong.model_copy(update={"test_id": "precision"})

    with pytest.raises(ValueError, match="frozen task configuration"):
        build_pc_report(results)


def test_report_is_deterministic_and_contains_only_training_scoped_language() -> None:
    results = report_results()

    first = build_pc_report(results)
    second = build_pc_report(list(reversed(results)))

    assert first == second
    assert first.recommendations == (
        "精准定位与微调：本次场景表现相对突出；练习稳定定位，并在保证准确的前提下逐步提速",
        "持续跟枪：本次场景表现相对突出；练习稳定跟随，并关注变向后的重新捕获",
        "多目标切换：练习按当前目标顺序切换，先稳定准确率再提高速度",
        "敌我识别：建议优先练习；练习先确认形状与文字标识，再进行射击判断",
    )
    payload = json.dumps(first.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    assert "职业天赋" not in payload
    assert "phone_number" not in payload.lower()
    assert "email" not in payload.lower()
    assert "轨迹" not in payload


def test_quality_warning_suppresses_strength_and_weakness_classification() -> None:
    report = build_pc_report(report_results(flagged_index=0))

    assert report.recommendations[0] == (
        "精准定位与微调：本次存在质量提示，建议在相同设备、设置和稳定环境下复测"
    )
    assert "相对突出" not in report.recommendations[0]
    assert "优先练习" not in report.recommendations[0]


def test_report_rejects_fatal_quality_even_if_a_dto_was_constructed_directly() -> None:
    results = report_results()
    results[0] = results[0].model_copy(
        update={"scored": results[0].scored.model_copy(update={"quality_flags": ("interrupted",)})}
    )

    with pytest.raises(ValueError, match="fatal quality"):
        build_pc_report(results)


def test_comparisons_belong_to_unique_frozen_members_and_are_canonically_ordered() -> None:
    comparisons = (
        ReportComparison(
            test_id="tracking",
            previous_report_id=UUID(int=101),
            score_delta=-2,
            non_comparable_reasons=(),
        ),
        ReportComparison(
            test_id="precision",
            previous_report_id=UUID(int=100),
            score_delta=3,
            non_comparable_reasons=(),
        ),
    )

    report = build_pc_report(report_results(), comparisons=comparisons)

    assert tuple(item.test_id for item in report.comparisons) == (
        "precision",
        "tracking",
    )

    with pytest.raises(ValueError, match="unique frozen members"):
        build_pc_report(
            report_results(),
            comparisons=(comparisons[0], comparisons[0]),
        )
    with pytest.raises(ValueError, match="unique frozen members"):
        build_pc_report(
            report_results(),
            comparisons=(
                ReportComparison(
                    test_id="last-hit",
                    previous_report_id=None,
                    score_delta=None,
                    non_comparable_reasons=("different_suite",),
                ),
            ),
        )


def test_comparison_key_changes_for_version_settings_input_and_geometry() -> None:
    config = get_suite("pc-fps", catalog_version="pc-fps-v2").tasks["precision"]
    base_settings = settings("pc-fps")
    base_environment = environment("pc-fps")
    base = comparison_key(config, base_settings, base_environment)

    changed_config = config.model_copy(update={"scoring_version": "pc-fps-v2"})
    changed_settings = settings("pc-fps", sensitivity=1.05)
    changed_input = environment("pc-fps", input_kind="unknown")
    changed_geometry = environment("pc-fps", width_css_px=1279)

    assert len(base) == 64
    assert (
        len(
            {
                base,
                comparison_key(changed_config, base_settings, base_environment),
                comparison_key(config, changed_settings, base_environment),
                comparison_key(config, base_settings, changed_input),
                comparison_key(config, base_settings, changed_geometry),
            }
        )
        == 5
    )
