from __future__ import annotations

from collections.abc import Callable

import pytest

from app.esports_assessments.catalog import get_task, validate_raw
from app.esports_assessments.config_schemas import MEMBERS
from app.esports_assessments.quality import QualityRejected
from app.esports_assessments.schemas import QualityInput
from app.esports_assessments.scoring import score_result, summarize_metrics
from tests.unit.esports_samples import sample_raw


def legacy_task(suite_id: str, test_id: str, difficulty: int = 3):
    return get_task(
        suite_id,
        test_id,
        difficulty,
        catalog_version=f"{suite_id}-v2",
    )


def quality(suite_id: str = "pc-fps", **overrides: object) -> QualityInput:
    values: dict[str, object] = {
        "duration_ms": 60000,
        "max_frame_gap_ms": 16,
        "long_frame_gap_total_ms": 0,
        "input_kind": "touch" if suite_id.startswith("mobile") else "mouse",
        "hidden": False,
        "interrupted": False,
    }
    values.update(overrides)
    return QualityInput.model_validate(values)


@pytest.mark.parametrize(
    ("suite_id", "test_id"),
    [(suite_id, test_id) for suite_id, members in MEMBERS.items() for test_id in members],
)
def test_every_formula_scores_literal_zero_and_one_hundred(suite_id: str, test_id: str) -> None:
    config = legacy_task(suite_id, test_id)

    zero = score_result(
        config,
        validate_raw(config, sample_raw(suite_id, test_id, perfect=False)),
        quality(suite_id),
    )
    perfect = score_result(
        config,
        validate_raw(config, sample_raw(suite_id, test_id, perfect=True)),
        quality(suite_id),
    )

    assert (zero.score, zero.grade) == (0, "low")
    assert (perfect.score, perfect.grade) == (100, "exceptional")


@pytest.mark.parametrize(
    ("suite_id", "test_id", "hit_latency_ms", "miss_latency_ms"),
    [
        ("pc-fps", "precision", 350, 2000),
        ("mobile-fps", "precision", 550, 2000),
        ("pc-fps", "switching", 300, 1500),
        ("mobile-fps", "switching", 450, 1500),
    ],
)
def test_aim_formula_uses_all_opportunities_and_full_shot_denominator(
    suite_id: str,
    test_id: str,
    hit_latency_ms: int,
    miss_latency_ms: int,
) -> None:
    config = legacy_task(suite_id, test_id)
    count = config.params.opportunity_count
    raw_value = {
        "kind": "aim",
        "trials": [
            *[{"hit": True, "latency_ms": hit_latency_ms} for _ in range(count // 2)],
            *[{"hit": False, "latency_ms": miss_latency_ms} for _ in range(count // 2)],
        ],
        "shots": count,
        "wrong_target_shots": 0,
    }

    result = score_result(
        config,
        validate_raw(config, raw_value),
        quality(suite_id),
    )

    # 100 * .5 * (.7 + .3*.5) * (.5 + .5*.5) = 31.875 -> 32.
    assert result.score == 32
    assert result.metrics["hit_rate"] == 0.5
    assert result.metrics["accuracy"] == 0.5


def test_recognition_wrong_or_missed_enemy_uses_timeout_for_scoring_latency() -> None:
    config = legacy_task("pc-fps", "recognition")
    raw_value = sample_raw("pc-fps", "recognition", perfect=False)
    enemy_seen = 0
    friend_seen = 0
    for trial in raw_value["trials"]:
        if trial["target_kind"] == "enemy":
            if enemy_seen < 8:
                trial.update(shot=True, hit=True, latency_ms=250)
            else:
                # Raw latency is the real first shot, but a wrong/miss scores as 1200ms.
                trial.update(shot=True, hit=False, latency_ms=100)
            enemy_seen += 1
        elif friend_seen < 3:
            trial.update(shot=True, hit=False, latency_ms=100)
            friend_seen += 1

    result = score_result(
        config,
        validate_raw(config, raw_value),
        quality(),
    )

    assert result.score == 29
    assert result.metrics["enemy_correct"] == 8
    assert result.metrics["friend_wrong_shots"] == 3
    assert result.metrics["enemy_scoring_average_latency_ms"] == pytest.approx(693.3333333333334)


def test_recognition_no_operation_is_zero() -> None:
    config = legacy_task("pc-fps", "recognition")
    result = score_result(
        config,
        validate_raw(config, sample_raw("pc-fps", "recognition", perfect=False)),
        quality(),
    )

    assert result.score == 0
    assert result.metrics["enemy_correct"] == 0
    assert result.metrics["enemy_scoring_average_latency_ms"] == 1200


def test_move_fire_does_not_join_successes_from_different_rounds() -> None:
    config = legacy_task("mobile-fps", "move-fire")
    raw = validate_raw(
        config,
        {
            "kind": "move_fire",
            "rounds": [
                *[{"hit": True, "reached": False} for _ in range(10)],
                *[{"hit": False, "reached": True} for _ in range(10)],
            ],
            "shots": 10,
        },
    )

    result = score_result(config, raw, quality("mobile-fps"))

    assert result.score == 0
    assert result.metrics["hits"] == 10
    assert result.metrics["reached"] == 10
    assert result.metrics["joint_successes"] == 0


def test_move_fire_round_half_up_is_applied_only_at_the_end() -> None:
    config = legacy_task("mobile-fps", "move-fire")
    raw = validate_raw(
        config,
        {
            "kind": "move_fire",
            "rounds": [
                *[{"hit": True, "reached": True} for _ in range(10)],
                *[{"hit": False, "reached": False} for _ in range(10)],
            ],
            "shots": 20,
        },
    )

    # 100 * .5 joint rate * (.5 + .5*.5 accuracy) = 37.5 -> 38.
    assert score_result(config, raw, quality("mobile-fps")).score == 38


def test_move_fire_repeating_accuracy_preserves_exact_half_boundary() -> None:
    config = legacy_task("mobile-fps", "move-fire")
    raw = validate_raw(
        config,
        {
            "kind": "move_fire",
            "rounds": [
                *[{"hit": True, "reached": True} for _ in range(3)],
                {"hit": True, "reached": False},
                *[{"hit": False, "reached": False} for _ in range(16)],
            ],
            "shots": 6,
        },
    )

    # 100 * 3/20 * (1/2 + 1/2*4/6) = 25/2 -> 13.
    assert score_result(config, raw, quality("mobile-fps")).score == 13


def test_aim_repeating_ratios_preserve_exact_half_boundary() -> None:
    config = legacy_task("pc-fps", "precision")
    raw = validate_raw(
        config,
        {
            "kind": "aim",
            "trials": [
                *[{"hit": True, "latency_ms": 625} for _ in range(4)],
                *[{"hit": False, "latency_ms": 2000} for _ in range(26)],
            ],
            "shots": 32,
            "wrong_target_shots": 0,
        },
    )

    # 100 * 2/15 * 11/15 * 9/16 = 11/2 -> 6.
    assert score_result(config, raw, quality()).score == 6


def test_rhythm_does_not_join_attack_and_position_from_different_rounds() -> None:
    config = legacy_task("pc-moba", "orb-walk")
    raw = validate_raw(
        config,
        {
            "kind": "rhythm",
            "rounds": [
                *[{"attacked": True, "reached": False, "cancelled_count": 0} for _ in range(15)],
                *[{"attacked": False, "reached": True, "cancelled_count": 0} for _ in range(15)],
            ],
        },
    )

    assert score_result(config, raw, quality("pc-moba")).score == 0


def test_last_hit_intermediate_score_and_offset_metric() -> None:
    config = legacy_task("pc-moba", "last-hit")
    raw_value = sample_raw("pc-moba", "last-hit", perfect=False)
    raw_value["waves"][:7] = [
        {"success": True, "early": 0, "late": 0, "offset_ms": 250} for _ in range(7)
    ]

    result = score_result(
        config,
        validate_raw(config, raw_value),
        quality("pc-moba"),
    )

    assert result.score == 47
    assert result.metrics["successful_average_offset_ms"] == 250


def test_dodge_and_skill_aim_intermediate_formulas() -> None:
    dodge = legacy_task("pc-moba", "dodge")
    dodge_raw = sample_raw("pc-moba", "dodge", perfect=False)
    dodge_raw["threats"][:15] = [{"hit": False, "response_ms": 300}] * 15
    assert (
        score_result(
            dodge,
            validate_raw(dodge, dodge_raw),
            quality("pc-moba"),
        ).score
        == 50
    )

    skill = legacy_task("mobile-moba", "skill-aim")
    skill_raw = sample_raw("mobile-moba", "skill-aim", perfect=False)
    skill_raw["trials"][:15] = [
        {
            "released": True,
            "hit": True,
            "angle_error_deg": 5,
            "release_latency_ms": 200,
            "hit_latency_ms": 500,
        }
    ] * 15
    assert (
        score_result(
            skill,
            validate_raw(skill, skill_raw),
            quality("mobile-moba"),
        ).score
        == 50
    )


def test_one_half_channel_is_not_full_map_score() -> None:
    config = legacy_task("pc-moba", "map-awareness")
    raw_value = sample_raw("pc-moba", "map-awareness", perfect=True)
    raw_value["main_hits"] = [True] * 15 + [False] * 15

    result = score_result(
        config,
        validate_raw(config, raw_value),
        quality("pc-moba"),
    )

    assert result.score == 71


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.update(main_hits=[False] * 30),
        lambda raw: raw.update(events=[{"response": "missed", "latency_ms": 1000}] * 12),
    ],
)
def test_map_score_is_zero_when_either_channel_is_not_done(
    mutate: Callable[[dict], None],
) -> None:
    config = legacy_task("pc-moba", "map-awareness")
    raw_value = sample_raw("pc-moba", "map-awareness", perfect=True)
    mutate(raw_value)

    assert (
        score_result(
            config,
            validate_raw(config, raw_value),
            quality("pc-moba"),
        ).score
        == 0
    )


def test_map_penalties_are_applied_before_geometric_mean() -> None:
    config = legacy_task("pc-moba", "map-awareness")
    raw_value = sample_raw("pc-moba", "map-awareness", perfect=False)
    raw_value["main_hits"][:15] = [True] * 15
    raw_value["events"] = [
        *[{"response": "correct", "latency_ms": 100} for _ in range(6)],
        *[{"response": "wrong", "latency_ms": 100} for _ in range(2)],
        *[{"response": "missed", "latency_ms": 1000} for _ in range(4)],
    ]
    raw_value["unprompted_count"] = 1

    # N=(6-2-1)/12=.25; sqrt(.5*.25)*100=35.355... -> 35.
    assert (
        score_result(
            config,
            validate_raw(config, raw_value),
            quality("pc-moba"),
        ).score
        == 35
    )


@pytest.mark.parametrize(
    ("coverage_ms", "score", "grade"),
    [
        (23400, 39, "low"),
        (23700, 40, "medium"),
        (41400, 69, "medium"),
        (41700, 70, "high"),
        (53400, 89, "high"),
        (53700, 90, "exceptional"),
    ],
)
def test_round_half_up_and_wire_grade_boundaries(coverage_ms: int, score: int, grade: str) -> None:
    config = legacy_task("pc-fps", "tracking")
    raw = validate_raw(
        config,
        {
            "kind": "tracking",
            "coverage_ms": coverage_ms,
            "loss_count": 0,
            "reacquisition_ms": [],
        },
    )

    result = score_result(config, raw, quality())

    assert (result.score, result.grade) == (score, grade)


def test_formal_score_rejects_practice_difficulty() -> None:
    config = legacy_task("pc-fps", "precision", difficulty=2)
    raw_value = sample_raw("pc-fps", "precision", perfect=True)

    with pytest.raises(ValueError, match="formal scoring requires difficulty 3"):
        score_result(config, validate_raw(config, raw_value), quality())


def test_formal_score_rejects_fatal_quality_instead_of_clamping_it() -> None:
    config = legacy_task("pc-fps", "tracking")
    raw = validate_raw(config, sample_raw("pc-fps", "tracking", perfect=True))

    with pytest.raises(QualityRejected):
        score_result(config, raw, quality(duration_ms=59999))


def test_summarize_metrics_reports_units_without_calculating_a_score() -> None:
    config = legacy_task("pc-fps", "tracking")
    raw = validate_raw(
        config,
        {
            "kind": "tracking",
            "coverage_ms": 30000,
            "loss_count": 2,
            "reacquisition_ms": [200, 400],
        },
    )

    assert summarize_metrics(config, raw) == {
        "coverage_ms": 30000,
        "coverage_rate": 0.5,
        "loss_count": 2,
        "average_reacquisition_ms": 300,
    }
