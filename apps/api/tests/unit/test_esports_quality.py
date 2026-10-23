from __future__ import annotations

import pytest

from app.esports_assessments.catalog import get_task, validate_raw
from app.esports_assessments.quality import QualityRejected, quality_flags
from app.esports_assessments.schemas import QualityInput
from app.esports_assessments.scoring import practice_result
from tests.unit.esports_samples import sample_raw


def quality(**overrides: object) -> QualityInput:
    values: dict[str, object] = {
        "duration_ms": 60000,
        "max_frame_gap_ms": 16,
        "long_frame_gap_total_ms": 0,
        "input_kind": "mouse",
        "hidden": False,
        "interrupted": False,
    }
    values.update(overrides)
    return QualityInput.model_validate(values)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"duration_ms": 59999}, "incomplete_duration"),
        ({"max_frame_gap_ms": 500.001}, "frame_gap"),
        ({"hidden": True}, "hidden"),
        ({"interrupted": True}, "interrupted"),
    ],
)
def test_fatal_quality_is_rejected_instead_of_being_saved_as_complete(
    overrides: dict[str, object], reason: str
) -> None:
    with pytest.raises(QualityRejected) as exc_info:
        quality_flags(get_task("pc-fps", "precision"), quality(**overrides))

    assert exc_info.value.reason_codes == (reason,)


def test_all_fatal_quality_reasons_are_safe_and_deterministic() -> None:
    with pytest.raises(QualityRejected) as exc_info:
        quality_flags(
            get_task("pc-fps", "precision"),
            quality(
                duration_ms=0,
                max_frame_gap_ms=501,
                hidden=True,
                interrupted=True,
            ),
        )

    assert exc_info.value.reason_codes == (
        "incomplete_duration",
        "frame_gap",
        "hidden",
        "interrupted",
    )
    assert str(exc_info.value) == (
        "assessment quality rejected: incomplete_duration,frame_gap,hidden,interrupted"
    )


def test_quality_thresholds_are_strictly_greater_than_the_boundary() -> None:
    config = get_task("pc-fps", "precision")

    assert (
        quality_flags(
            config,
            quality(max_frame_gap_ms=500, long_frame_gap_total_ms=3000),
        )
        == ()
    )
    assert quality_flags(config, quality(long_frame_gap_total_ms=3000.001)) == ("long_frames",)


@pytest.mark.parametrize(
    ("suite_id", "input_kind", "expected"),
    [
        ("pc-fps", "mixed", ("mixed_input",)),
        ("pc-fps", "unknown", ("unconfirmed_input",)),
        ("pc-fps", "touch", ("unconfirmed_input",)),
        ("mobile-fps", "mouse", ("unconfirmed_input",)),
        ("mobile-fps", "touch", ()),
    ],
)
def test_input_quality_warnings_preserve_complete_results(
    suite_id: str, input_kind: str, expected: tuple[str, ...]
) -> None:
    assert (
        quality_flags(
            get_task(suite_id, "precision"),
            quality(input_kind=input_kind),
        )
        == expected
    )


def test_practice_result_has_raw_metrics_but_never_a_score_or_grade() -> None:
    config = get_task("pc-fps", "precision", difficulty=3)
    raw = validate_raw(config, sample_raw("pc-fps", "precision", perfect=True))

    result = practice_result(config, raw, quality(input_kind="unknown"))

    assert result.score is None
    assert result.grade is None
    assert result.quality_flags == ("unconfirmed_input",)
    assert result.metrics == {
        "opportunities": 30,
        "hits": 30,
        "hit_rate": 1,
        "shots": 30,
        "accuracy": 1,
        "wrong_target_shots": 0,
        "successful_average_latency_ms": 100,
        "average_latency_ms": 100,
    }


def test_practice_result_cannot_persist_interrupted_input_as_complete() -> None:
    config = get_task("pc-fps", "precision")
    raw = validate_raw(config, sample_raw("pc-fps", "precision", perfect=True))

    with pytest.raises(QualityRejected):
        practice_result(config, raw, quality(interrupted=True))
