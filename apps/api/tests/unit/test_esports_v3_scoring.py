from __future__ import annotations

import pytest

from app.esports_assessments.catalog import get_task, validate_raw
from app.esports_assessments.schemas import QualityInput
from app.esports_assessments.scoring import practice_result, score_result, summarize_metrics
from tests.unit.esports_v3_samples import sample_raw_v3


def quality(suite_id: str) -> QualityInput:
    return QualityInput(
        duration_ms=60000,
        max_frame_gap_ms=16,
        long_frame_gap_total_ms=0,
        input_kind="touch" if suite_id.startswith("mobile") else "mouse",
        hidden=False,
        interrupted=False,
    )


@pytest.mark.parametrize(
    ("suite_id", "test_id"),
    [
        ("pc-fps", "switching"),
        ("mobile-fps", "switching"),
        ("mobile-fps", "move-fire"),
        ("pc-moba", "dodge"),
        ("mobile-moba", "dodge"),
    ],
)
def test_v3_changed_formulas_score_literal_zero_and_one_hundred(
    suite_id: str,
    test_id: str,
) -> None:
    config = get_task(suite_id, test_id, catalog_version=f"{suite_id}-v3")

    zero = score_result(
        config,
        validate_raw(config, sample_raw_v3(config, perfect=False)),
        quality(suite_id),
    )
    perfect = score_result(
        config,
        validate_raw(config, sample_raw_v3(config, perfect=True)),
        quality(suite_id),
    )

    assert (zero.score, zero.grade) == (0, "low")
    assert (perfect.score, perfect.grade) == (100, "exceptional")


def test_real_slow_switching_is_not_perfect() -> None:
    config = get_task("pc-fps", "switching", catalog_version="pc-fps-v3")
    raw = validate_raw(
        config,
        {
            "kind": "aim",
            "trials": [
                row
                for _ in range(20)
                for row in (
                    {"hit": True, "latency_ms": 200},
                    {"hit": True, "latency_ms": 1200},
                )
            ],
            "shots": 40,
            "wrong_target_shots": 0,
        },
    )

    result = score_result(config, raw, quality("pc-fps"))

    assert result.score == 55
    assert result.metrics == {
        "opportunities": 40,
        "switching_group_successes": 20,
        "switching_group_rate": 1,
        "shots": 40,
        "accuracy": 1,
        "wrong_target_shots": 0,
        "acquisition_success_average_ms": 200,
        "switching_success_average_ms": 1200,
        "switching_scoring_average_ms": 1200,
    }


def test_switching_penalizes_failed_second_targets_and_preserves_empty_metrics() -> None:
    config = get_task("pc-fps", "switching", catalog_version="pc-fps-v3")
    raw = validate_raw(
        config,
        {
            "kind": "aim",
            "trials": [
                row
                for _ in range(20)
                for row in (
                    {"hit": True, "latency_ms": 200},
                    {"hit": False, "latency_ms": 1500},
                )
            ],
            "shots": 20,
            "wrong_target_shots": 0,
        },
    )

    result = score_result(config, raw, quality("pc-fps"))

    assert result.score == 0
    assert result.metrics["switching_group_successes"] == 0
    assert result.metrics["acquisition_success_average_ms"] == 200
    assert result.metrics["switching_success_average_ms"] is None
    assert result.metrics["switching_scoring_average_ms"] == 1500


def test_switching_uses_actual_shots_as_the_accuracy_denominator() -> None:
    config = get_task("pc-fps", "switching", catalog_version="pc-fps-v3")
    raw = validate_raw(
        config,
        {
            "kind": "aim",
            "trials": [
                row
                for _ in range(20)
                for row in (
                    {"hit": True, "latency_ms": 200},
                    {"hit": True, "latency_ms": 300},
                )
            ],
            "shots": 80,
            "wrong_target_shots": 40,
        },
    )

    result = score_result(config, raw, quality("pc-fps"))

    assert result.score == 75
    assert result.metrics["accuracy"] == 0.5


def test_move_fire_v3_uses_moving_hits_and_rounds_half_up_once() -> None:
    config = get_task("mobile-fps", "move-fire", catalog_version="mobile-fps-v3")
    raw = validate_raw(
        config,
        {
            "kind": "move_fire_v3",
            "rounds": [
                *[
                    {
                        "hit": True,
                        "reached": True,
                        "hit_latency_ms": 500,
                        "hit_speed_m_s": 2,
                        "pre_hit_moving_ms": 100,
                    }
                    for _ in range(3)
                ],
                {
                    "hit": True,
                    "reached": False,
                    "hit_latency_ms": 500,
                    "hit_speed_m_s": 2,
                    "pre_hit_moving_ms": 100,
                },
                *[
                    {
                        "hit": False,
                        "reached": False,
                        "hit_latency_ms": None,
                        "hit_speed_m_s": None,
                        "pre_hit_moving_ms": None,
                    }
                    for _ in range(16)
                ],
            ],
            "shots": 6,
        },
    )

    result = score_result(config, raw, quality("mobile-fps"))

    assert result.score == 13
    assert result.metrics == {
        "opportunities": 20,
        "moving_hits": 4,
        "reached": 3,
        "joint_successes": 3,
        "joint_rate": 0.15,
        "shots": 6,
        "accuracy": pytest.approx(2 / 3),
    }


def test_dodge_choice_scores_avoidance_and_averages_every_safe_entry() -> None:
    config = get_task("pc-moba", "dodge", catalog_version="pc-moba-v3")
    raw_value = sample_raw_v3(config, perfect=False)
    rounds = raw_value["rounds"]
    assert isinstance(rounds, list)
    for round_ in rounds[:15]:
        assert isinstance(round_, dict)
        round_["hit"] = False
        round_["safe_entry_latency_ms"] = 100
    for round_ in rounds[15:20]:
        assert isinstance(round_, dict)
        round_["safe_entry_latency_ms"] = 300
    raw = validate_raw(config, raw_value)

    result = score_result(config, raw, quality("pc-moba"))

    assert result.score == 50
    assert result.metrics == {
        "opportunities": 30,
        "avoided": 15,
        "hits_taken": 15,
        "avoid_rate": 0.5,
        "safe_entries": 20,
        "safe_entry_average_ms": 150,
    }


def test_v3_empty_success_averages_are_null_not_fabricated_zeroes() -> None:
    switching = get_task("pc-fps", "switching", catalog_version="pc-fps-v3")
    dodge = get_task("pc-moba", "dodge", catalog_version="pc-moba-v3")

    switching_metrics = summarize_metrics(
        switching,
        validate_raw(switching, sample_raw_v3(switching, perfect=False)),
    )
    dodge_metrics = summarize_metrics(
        dodge,
        validate_raw(dodge, sample_raw_v3(dodge, perfect=False)),
    )

    assert switching_metrics["acquisition_success_average_ms"] is None
    assert switching_metrics["switching_success_average_ms"] is None
    assert switching_metrics["switching_scoring_average_ms"] == 1500
    assert dodge_metrics["safe_entry_average_ms"] is None


def test_v3_practice_returns_metrics_without_formal_score_or_grade() -> None:
    config = get_task(
        "mobile-fps",
        "move-fire",
        difficulty=2,
        catalog_version="mobile-fps-v3",
    )
    raw = validate_raw(config, sample_raw_v3(config, perfect=True))

    result = practice_result(config, raw, quality("mobile-fps"))

    assert result.score is None
    assert result.grade is None
    assert result.metrics["moving_hits"] == 20
    assert result.metrics["joint_rate"] == 1
