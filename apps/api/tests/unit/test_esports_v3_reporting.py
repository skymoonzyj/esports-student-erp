from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.esports_assessments.catalog import get_suite
from app.esports_assessments.reporting import build_report
from app.esports_assessments.schemas import (
    AssessmentReportView,
    ControlSettings,
    EnvironmentSnapshot,
    PersonDisplaySnapshot,
    ReportComparison,
    ReportResult,
    ScoredResult,
)


def settings(suite_id: str) -> ControlSettings:
    if suite_id == "pc-fps":
        return ControlSettings(sensitivity=1, input_kind="mouse", mouse_mode="raw")
    if suite_id == "pc-moba":
        return ControlSettings(sensitivity=1, input_kind="mouse")
    return ControlSettings(sensitivity=1, input_kind="touch")


def environment(suite_id: str) -> EnvironmentSnapshot:
    mobile = suite_id.startswith("mobile")
    return EnvironmentSnapshot(
        input_kind="touch" if mobile else "mouse",
        is_phone=mobile,
        touch_capable=mobile,
        width_css_px=844 if mobile else 1280,
        height_css_px=390 if mobile else 720,
        device_pixel_ratio=2 if mobile else 1,
        pointer_lock_supported=suite_id == "pc-fps",
        pointer_lock_active=suite_id == "pc-fps",
        raw_mouse_requested=suite_id == "pc-fps",
        raw_mouse_confirmed=suite_id == "pc-fps",
        webgl2_supported="fps" in suite_id,
    )


def report_for(
    suite_id: str,
    *,
    catalog_version: str,
    comparisons: tuple[ReportComparison, ...] = (),
) -> AssessmentReportView:
    suite = get_suite(suite_id, catalog_version=catalog_version)
    results = [
        ReportResult(
            result_id=UUID(int=index + 10),
            test_id=test_id,
            config=suite.tasks[test_id],
            settings=settings(suite_id),
            environment=environment(suite_id),
            scored=ScoredResult(
                metrics={"literal_metric": index},
                score=50,
                grade="medium",
                quality_flags=(),
            ),
        )
        for index, test_id in enumerate(suite.test_ids)
    ]
    return build_report(
        UUID("22222222-2222-2222-2222-222222222222"),
        PersonDisplaySnapshot(
            person_id=UUID("33333333-3333-3333-3333-333333333333"),
            display_name="测试学员",
        ),
        suite,
        results,
        datetime(2026, 9, 23, 8, 30, tzinfo=UTC),
        report_id=UUID("11111111-1111-1111-1111-111111111111"),
        comparisons=comparisons,
    )


def recommendation(report: AssessmentReportView, test_id: str) -> str:
    results = report.results
    index = next(index for index, result in enumerate(results) if result.test_id == test_id)
    return report.recommendations[index]


def test_v3_report_uses_distinct_switching_and_moving_hit_explanations() -> None:
    report = report_for("mobile-fps", catalog_version="mobile-fps-v3")

    assert recommendation(report, "switching") == (
        "多目标切换：练习先稳定完成两靶组，再分别关注首次获取与第二靶真实切换耗时，"
        "并在保持准确率的前提下提速"
    )
    assert recommendation(report, "move-fire") == (
        "移动与射击协同：练习保持至少 100ms 有效移动后命中，并兼顾轮末到位和有效射击准确率"
    )
    assert "统一反应速度" not in "".join(report.recommendations)


def test_v3_dodge_report_uses_danger_area_name_and_safe_entry_explanation() -> None:
    report = report_for("pc-moba", catalog_version="pc-moba-v3")

    assert recommendation(report, "dodge") == (
        "危险区域识别与躲避：练习根据危险区域预警完整进入安全区，并在爆发期间保持安全；"
        "安全区进入时长仅用于解释本次表现"
    )
    assert "反应速度" not in recommendation(report, "dodge")


def test_legacy_report_wording_remains_exactly_unchanged() -> None:
    report = report_for("pc-fps", catalog_version="pc-fps-v2")

    assert report.recommendations == (
        "精准定位与微调：练习稳定定位，并在保证准确的前提下逐步提速",
        "持续跟枪：练习稳定跟随，并关注变向后的重新捕获",
        "多目标切换：练习按当前目标顺序切换，先稳定准确率再提高速度",
        "敌我识别：练习先确认形状与文字标识，再进行射击判断",
    )


def test_cross_version_comparison_is_non_comparable_without_a_score_delta() -> None:
    comparison = ReportComparison(
        test_id="switching",
        previous_report_id=UUID(int=101),
        score_delta=5,
        non_comparable_reasons=("config_changed",),
    )

    report = report_for(
        "pc-fps",
        catalog_version="pc-fps-v3",
        comparisons=(comparison,),
    )

    assert report.comparisons[0].score_delta is None
    assert report.comparisons[0].non_comparable_reasons == ("config_changed",)
