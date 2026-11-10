"""Deterministic server scoring and raw metric summaries."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from itertools import pairwise

from .catalog import validate_raw
from .config_schemas import AimParams, MultiTargetParams, RecognitionParams, TaskConfig
from .quality import quality_flags
from .raw_schemas import (
    AimRaw,
    DodgeChoiceRaw,
    DodgeRaw,
    LaneLastHitRaw,
    LaneTowerLastHitRaw,
    LastHitRaw,
    MapRaw,
    MoveFireRaw,
    MoveFireV3Raw,
    MultiTargetRaw,
    RawResult,
    RecognitionRaw,
    RhythmRaw,
    SkillAimRaw,
    Spheretrack90Raw,
    TrackingRaw,
)
from .schemas import Grade, PracticeResult, QualityInput, ScoredResult

Metric = float | None
Metrics = dict[str, Metric]
_ZERO = Decimal(0)
_ONE = Decimal(1)
_FRACTION_ZERO = Fraction(0)
_FRACTION_ONE = Fraction(1)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _ratio(numerator: float, denominator: float) -> Decimal:
    return _decimal(numerator) / _decimal(denominator)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum((_decimal(value) for value in values), start=_ZERO) / len(values))


def _clamp_unit(value: Decimal) -> Decimal:
    return min(_ONE, max(_ZERO, value))


def _fraction(value: float) -> Fraction:
    """Preserve the validated JSON decimal value, never the binary float payload."""

    return Fraction(str(value))


def _fraction_ratio(numerator: float, denominator: float) -> Fraction:
    return _fraction(numerator) / _fraction(denominator)


def _clamp_fraction(value: Fraction) -> Fraction:
    return min(_FRACTION_ONE, max(_FRACTION_ZERO, value))


def _speed_fraction(value_ms: Fraction, fast_ms: float, slow_ms: float) -> Fraction:
    fast = _fraction(fast_ms)
    slow = _fraction(slow_ms)
    return _clamp_fraction((slow - value_ms) / (slow - fast))


def _round_fraction(value: Fraction) -> int:
    """Round a nonnegative exact rational HALF_UP without an intermediate decimal."""

    rounded = (2 * value.numerator + value.denominator) // (2 * value.denominator)
    return min(100, max(0, rounded))


def _round_sqrt_fraction(value_squared: Fraction) -> int:
    """Round sqrt(value_squared) HALF_UP using exact squared half-step comparisons."""

    for rounded in range(100):
        upper = Fraction(2 * rounded + 1, 2)
        if value_squared < upper * upper:
            return rounded
    return 100


def _grade(score: int) -> Grade:
    if score < 40:
        return "low"
    if score < 70:
        return "medium"
    if score < 90:
        return "high"
    return "exceptional"


def _uses_v2_rule(config: TaskConfig, test_id: str) -> bool:
    return config.test_id == test_id and config.scoring_version == f"{config.suite_id}-v2"


def _summarize_validated(config: TaskConfig, raw: RawResult) -> Metrics:
    if isinstance(raw, MultiTargetRaw):
        hit_times = [shot.at_ms for shot in raw.shots if shot.hit]
        hits, shots = len(hit_times), len(raw.shots)
        return {
            "hits": hits,
            "shots": shots,
            "accuracy": float(_ratio(hits, shots)) if shots else 0,
            "hits_per_second": float(_ratio(hits, 60)),
            "successful_average_interval_ms": _mean(
                [second - first for first, second in pairwise(hit_times)]
            ),
        }
    if _uses_v2_rule(config, "switching"):
        assert isinstance(raw, AimRaw)
        groups = tuple(zip(raw.trials[::2], raw.trials[1::2], strict=True))
        group_successes = sum(first.hit and second.hit for first, second in groups)
        hits = sum(trial.hit for trial in raw.trials)
        return {
            "opportunities": len(raw.trials),
            "switching_group_successes": group_successes,
            "switching_group_rate": float(_ratio(group_successes, len(groups))),
            "shots": raw.shots,
            "accuracy": float(_ratio(hits, raw.shots)) if raw.shots else 0,
            "wrong_target_shots": raw.wrong_target_shots,
            "acquisition_success_average_ms": _mean(
                [first.latency_ms for first, _ in groups if first.hit]
            ),
            "switching_success_average_ms": _mean(
                [second.latency_ms for _, second in groups if second.hit]
            ),
            "switching_scoring_average_ms": _mean([second.latency_ms for _, second in groups]),
        }
    if isinstance(raw, AimRaw):
        hits = sum(trial.hit for trial in raw.trials)
        successful_latency = [trial.latency_ms for trial in raw.trials if trial.hit]
        return {
            "opportunities": len(raw.trials),
            "hits": hits,
            "hit_rate": float(_ratio(hits, len(raw.trials))),
            "shots": raw.shots,
            "accuracy": float(_ratio(hits, raw.shots)) if raw.shots else 0,
            "wrong_target_shots": raw.wrong_target_shots,
            "successful_average_latency_ms": _mean(successful_latency),
            "average_latency_ms": _mean([trial.latency_ms for trial in raw.trials]),
        }
    if isinstance(raw, (TrackingRaw, Spheretrack90Raw)):
        return {
            "coverage_ms": raw.coverage_ms,
            "coverage_rate": float(_ratio(raw.coverage_ms, 60000)),
            "loss_count": raw.loss_count,
            "average_reacquisition_ms": _mean(list(raw.reacquisition_ms)),
        }
    if isinstance(raw, RecognitionRaw):
        enemy_trials = [trial for trial in raw.trials if trial.target_kind == "enemy"]
        friend_trials = [trial for trial in raw.trials if trial.target_kind == "friend"]
        correct_trials = [trial for trial in enemy_trials if trial.hit]
        friend_wrong = sum(trial.shot for trial in friend_trials)
        slow_ms = 1200
        scoring_latency = [trial.latency_ms if trial.hit else slow_ms for trial in enemy_trials]
        return {
            "enemy_opportunities": len(enemy_trials),
            "enemy_correct": len(correct_trials),
            "enemy_correct_rate": float(_ratio(len(correct_trials), len(enemy_trials))),
            "enemy_missed_or_wrong": len(enemy_trials) - len(correct_trials),
            "friend_opportunities": len(friend_trials),
            "friend_wrong_shots": friend_wrong,
            "friend_wrong_rate": float(_ratio(friend_wrong, len(friend_trials))),
            "enemy_correct_average_latency_ms": _mean(
                [trial.latency_ms for trial in correct_trials]
            ),
            "enemy_scoring_average_latency_ms": _mean(scoring_latency),
        }
    if isinstance(raw, MoveFireV3Raw):
        moving_hits = sum(round_.hit for round_ in raw.rounds)
        reached = sum(round_.reached for round_ in raw.rounds)
        joint = sum(round_.hit and round_.reached for round_ in raw.rounds)
        return {
            "opportunities": len(raw.rounds),
            "moving_hits": moving_hits,
            "reached": reached,
            "joint_successes": joint,
            "joint_rate": float(_ratio(joint, len(raw.rounds))),
            "shots": raw.shots,
            "accuracy": float(_ratio(moving_hits, raw.shots)) if raw.shots else 0,
        }
    if isinstance(raw, MoveFireRaw):
        hits = sum(round_.hit for round_ in raw.rounds)
        reached = sum(round_.reached for round_ in raw.rounds)
        joint = sum(round_.hit and round_.reached for round_ in raw.rounds)
        return {
            "opportunities": len(raw.rounds),
            "hits": hits,
            "reached": reached,
            "joint_successes": joint,
            "joint_rate": float(_ratio(joint, len(raw.rounds))),
            "shots": raw.shots,
            "accuracy": float(_ratio(hits, raw.shots)) if raw.shots else 0,
        }
    if isinstance(raw, (LaneLastHitRaw, LaneTowerLastHitRaw)):
        assert config.params.kind in ("lane_last_hit", "lane_last_hit_towers")
        opportunities = config.params.wave_count * (
            config.params.melee_per_wave + config.params.ranged_per_wave
        )
        lane_hits = [d for d in raw.deaths if d.by_player]
        return {
            "last_hits": len(lane_hits),
            "enemy_deaths": len(raw.deaths),
            "missed": len(raw.deaths) - len(lane_hits),
            "unresolved": opportunities - len(raw.deaths),
            "last_hit_rate": float(_ratio(len(lane_hits), len(raw.deaths))) if raw.deaths else 0,
            "opportunity_completion": len(lane_hits) / opportunities,
            "last_hits_per_minute": len(lane_hits) / 5,
            "attacks": raw.attacks,
            "cancelled_attacks": raw.cancelled_attacks,
            **(
                {
                    "hero_deaths": len(raw.hero_death_times),
                    "hero_damage_taken": raw.hero_damage_taken,
                }
                if isinstance(raw, LaneTowerLastHitRaw)
                else {}
            ),
            **{
                f"minute_{i + 1}_last_hits": sum(d.at_ms // 60000 == i for d in lane_hits)
                for i in range(5)
            },
        }
    if isinstance(raw, LastHitRaw):
        successes = sum(wave.success for wave in raw.waves)
        return {
            "opportunities": len(raw.waves),
            "successes": successes,
            "success_rate": float(_ratio(successes, len(raw.waves))),
            "early_attacks": sum(wave.early for wave in raw.waves),
            "late_attacks": sum(wave.late for wave in raw.waves),
            "successful_average_offset_ms": _mean(
                [wave.offset_ms for wave in raw.waves if wave.offset_ms is not None]
            ),
        }
    if isinstance(raw, RhythmRaw):
        attacked = sum(round_.attacked for round_ in raw.rounds)
        reached = sum(round_.reached for round_ in raw.rounds)
        joint = sum(round_.attacked and round_.reached for round_ in raw.rounds)
        return {
            "opportunities": len(raw.rounds),
            "attacks_completed": attacked,
            "reached": reached,
            "joint_successes": joint,
            "joint_rate": float(_ratio(joint, len(raw.rounds))),
            "cancelled_attacks": sum(round_.cancelled_count for round_ in raw.rounds),
        }
    if isinstance(raw, DodgeChoiceRaw):
        hits = sum(round_.hit for round_ in raw.rounds)
        safe_entries = [
            round_.safe_entry_latency_ms
            for round_ in raw.rounds
            if round_.safe_entry_latency_ms is not None
        ]
        return {
            "opportunities": len(raw.rounds),
            "avoided": len(raw.rounds) - hits,
            "hits_taken": hits,
            "avoid_rate": float(_ratio(len(raw.rounds) - hits, len(raw.rounds))),
            "safe_entries": len(safe_entries),
            "safe_entry_average_ms": _mean(safe_entries),
        }
    if isinstance(raw, DodgeRaw):
        hits = sum(threat.hit for threat in raw.threats)
        return {
            "opportunities": len(raw.threats),
            "avoided": len(raw.threats) - hits,
            "hits_taken": hits,
            "avoid_rate": float(_ratio(len(raw.threats) - hits, len(raw.threats))),
            "average_response_ms": _mean(
                [threat.response_ms for threat in raw.threats if threat.response_ms is not None]
            ),
        }
    if isinstance(raw, SkillAimRaw):
        released = [trial for trial in raw.trials if trial.released]
        hit_trials = [trial for trial in raw.trials if trial.hit]
        return {
            "opportunities": len(raw.trials),
            "released": len(released),
            "unreleased": len(raw.trials) - len(released),
            "hits": len(hit_trials),
            "hit_rate": float(_ratio(len(hit_trials), len(raw.trials))),
            "average_release_angle_error_deg": _mean(
                [trial.angle_error_deg for trial in released if trial.angle_error_deg is not None]
            ),
            "average_release_latency_ms": _mean(
                [
                    trial.release_latency_ms
                    for trial in released
                    if trial.release_latency_ms is not None
                ]
            ),
            "average_hit_latency_ms": _mean(
                [trial.hit_latency_ms for trial in hit_trials if trial.hit_latency_ms is not None]
            ),
        }
    if isinstance(raw, MapRaw):
        main_hits = sum(raw.main_hits)
        correct_events = [event for event in raw.events if event.response == "correct"]
        wrong = sum(event.response == "wrong" for event in raw.events)
        missed = sum(event.response == "missed" for event in raw.events)
        attention = _clamp_unit(
            _ratio(len(correct_events) - wrong - raw.unprompted_count, len(raw.events))
        )
        return {
            "main_opportunities": len(raw.main_hits),
            "main_hits": main_hits,
            "main_hit_rate": float(_ratio(main_hits, len(raw.main_hits))),
            "map_opportunities": len(raw.events),
            "map_correct": len(correct_events),
            "map_wrong": wrong,
            "map_missed": missed,
            "unprompted_count": raw.unprompted_count,
            "map_attention_rate": float(attention),
            "map_correct_average_latency_ms": _mean([event.latency_ms for event in correct_events]),
        }
    raise TypeError("unsupported raw result")


def summarize_metrics(config: TaskConfig, raw: RawResult) -> Metrics:
    """Return bounded report metrics with milliseconds and explicit rates."""

    return _summarize_validated(config, validate_raw(config, raw))


def _score_validated(config: TaskConfig, raw: RawResult) -> int:
    if isinstance(raw, MultiTargetRaw):
        assert isinstance(config.params, MultiTargetParams)
        hits, shots = sum(shot.hit for shot in raw.shots), len(raw.shots)
        if not shots:
            return 0
        return _round_fraction(
            100
            * min(Fraction(hits, config.params.reference_hits), _FRACTION_ONE)
            * Fraction(hits, shots)
        )
    if _uses_v2_rule(config, "switching"):
        assert isinstance(raw, AimRaw)
        assert isinstance(config.params, AimParams)
        groups = tuple(zip(raw.trials[::2], raw.trials[1::2], strict=True))
        group_successes = sum(first.hit and second.hit for first, second in groups)
        joint_rate = _fraction_ratio(group_successes, len(groups))
        hits = sum(trial.hit for trial in raw.trials)
        accuracy = _fraction_ratio(hits, raw.shots) if raw.shots else _FRACTION_ZERO
        switching_average = sum(
            (_fraction(second.latency_ms) for _, second in groups),
            start=_FRACTION_ZERO,
        ) / len(groups)
        speed = _speed_fraction(
            switching_average,
            config.params.fast_ms,
            config.params.slow_ms,
        )
        return _round_fraction(
            Fraction(100)
            * joint_rate
            * (Fraction(2, 5) + Fraction(3, 5) * speed)
            * (Fraction(1, 2) + Fraction(1, 2) * accuracy)
        )
    if isinstance(raw, AimRaw):
        assert isinstance(config.params, AimParams)
        hits = sum(trial.hit for trial in raw.trials)
        hit_rate = _fraction_ratio(hits, len(raw.trials))
        accuracy = _fraction_ratio(hits, raw.shots) if raw.shots else _FRACTION_ZERO
        average = sum(
            (_fraction(trial.latency_ms) for trial in raw.trials),
            start=_FRACTION_ZERO,
        ) / len(raw.trials)
        speed = _speed_fraction(average, config.params.fast_ms, config.params.slow_ms)
        return _round_fraction(
            Fraction(100)
            * hit_rate
            * (Fraction(7, 10) + Fraction(3, 10) * speed)
            * (Fraction(1, 2) + Fraction(1, 2) * accuracy)
        )
    if isinstance(raw, (TrackingRaw, Spheretrack90Raw)):
        return _round_fraction(Fraction(100) * _fraction_ratio(raw.coverage_ms, config.duration_ms))
    if isinstance(raw, RecognitionRaw):
        assert isinstance(config.params, RecognitionParams)
        enemy = [trial for trial in raw.trials if trial.target_kind == "enemy"]
        friends = [trial for trial in raw.trials if trial.target_kind == "friend"]
        correct = sum(trial.hit for trial in enemy)
        friend_wrong = sum(trial.shot for trial in friends)
        discrimination = max(
            _FRACTION_ZERO,
            _fraction_ratio(correct, len(enemy)) - _fraction_ratio(friend_wrong, len(friends)),
        )
        average = sum(
            (
                _fraction(trial.latency_ms if trial.hit else config.params.slow_ms)
                for trial in enemy
            ),
            start=_FRACTION_ZERO,
        ) / len(enemy)
        speed = _speed_fraction(average, config.params.fast_ms, config.params.slow_ms)
        return _round_fraction(
            Fraction(100) * discrimination * (Fraction(7, 10) + Fraction(3, 10) * speed)
        )
    if isinstance(raw, MoveFireV3Raw):
        joint = sum(round_.hit and round_.reached for round_ in raw.rounds)
        moving_hits = sum(round_.hit for round_ in raw.rounds)
        accuracy = _fraction_ratio(moving_hits, raw.shots) if raw.shots else _FRACTION_ZERO
        return _round_fraction(
            Fraction(100)
            * _fraction_ratio(joint, len(raw.rounds))
            * (Fraction(1, 2) + Fraction(1, 2) * accuracy)
        )
    if isinstance(raw, MoveFireRaw):
        joint = sum(round_.hit and round_.reached for round_ in raw.rounds)
        hits = sum(round_.hit for round_ in raw.rounds)
        accuracy = _fraction_ratio(hits, raw.shots) if raw.shots else _FRACTION_ZERO
        return _round_fraction(
            Fraction(100)
            * _fraction_ratio(joint, len(raw.rounds))
            * (Fraction(1, 2) + Fraction(1, 2) * accuracy)
        )
    if isinstance(raw, (LaneLastHitRaw, LaneTowerLastHitRaw)):
        assert config.params.kind in ("lane_last_hit", "lane_last_hit_towers")
        opportunities = config.params.wave_count * (
            config.params.melee_per_wave + config.params.ranged_per_wave
        )
        return _round_fraction(
            Fraction(100) * _fraction_ratio(sum(d.by_player for d in raw.deaths), opportunities)
        )
    if isinstance(raw, LastHitRaw):
        return _round_fraction(
            Fraction(100) * _fraction_ratio(sum(wave.success for wave in raw.waves), len(raw.waves))
        )
    if isinstance(raw, RhythmRaw):
        joint = sum(round_.attacked and round_.reached for round_ in raw.rounds)
        return _round_fraction(Fraction(100) * _fraction_ratio(joint, len(raw.rounds)))
    if isinstance(raw, DodgeChoiceRaw):
        return _round_fraction(
            Fraction(100)
            * _fraction_ratio(
                sum(not round_.hit for round_ in raw.rounds),
                len(raw.rounds),
            )
        )
    if isinstance(raw, DodgeRaw):
        return _round_fraction(
            Fraction(100)
            * (
                _FRACTION_ONE
                - _fraction_ratio(sum(threat.hit for threat in raw.threats), len(raw.threats))
            )
        )
    if isinstance(raw, SkillAimRaw):
        return _round_fraction(
            Fraction(100) * _fraction_ratio(sum(trial.hit for trial in raw.trials), len(raw.trials))
        )
    if isinstance(raw, MapRaw):
        main = _fraction_ratio(sum(raw.main_hits), len(raw.main_hits))
        correct = sum(event.response == "correct" for event in raw.events)
        wrong = sum(event.response == "wrong" for event in raw.events)
        attention = _clamp_fraction(
            _fraction_ratio(correct - wrong - raw.unprompted_count, len(raw.events))
        )
        return _round_sqrt_fraction(Fraction(10000) * main * attention)
    raise TypeError("unsupported raw result")


def score_result(config: TaskConfig, raw: RawResult, quality: QualityInput) -> ScoredResult:
    """Recompute an official score from registered formal rules and validated raw data."""

    if config.difficulty != 3:
        raise ValueError("formal scoring requires difficulty 3")
    validated = validate_raw(config, raw)
    flags = quality_flags(config, quality)
    score = _score_validated(config, validated)
    return ScoredResult(
        metrics=_summarize_validated(config, validated),
        score=score,
        grade=_grade(score),
        quality_flags=flags,
    )


def practice_result(config: TaskConfig, raw: RawResult, quality: QualityInput) -> PracticeResult:
    """Summarize practice raw data without invoking formal score or report logic."""

    validated = validate_raw(config, raw)
    return PracticeResult(
        metrics=_summarize_validated(config, validated),
        quality_flags=quality_flags(config, quality),
    )
