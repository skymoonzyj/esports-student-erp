"""Registered frozen catalogs and seed-reconstructable raw validation."""

from math import isfinite
from typing import TypedDict

from .config_schemas import MEMBERS, SuiteConfig, TaskConfig, registered_catalog_version
from .raw_schemas import RAW_ADAPTER, RawResult
from .rule_versions import registered_rule

AVAILABLE_SUITES: frozenset[str] = frozenset()
PRACTICE_MULTIPLIERS = (
    (1.5, 0.6, 1.4, 0.7),
    (1.25, 0.8, 1.2, 0.85),
    (1, 1, 1, 1),
    (0.85, 1.15, 0.85, 1.1),
    (0.7, 1.3, 0.7, 1.2),
)


def next_u32(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("seed must be an unsigned 32-bit integer")
    value = value or 0x6D2B79F5
    value ^= (value << 13) & 0xFFFFFFFF
    value ^= value >> 17
    value ^= (value << 5) & 0xFFFFFFFF
    return value & 0xFFFFFFFF


def shuffled[T](values: tuple[T, ...], seed: int) -> tuple[T, ...]:
    next_u32(seed)  # Validate even an empty schedule.
    result = list(values)
    for i in range(len(result) - 1, 0, -1):
        seed = next_u32(seed)
        j = seed % (i + 1)
        result[i], result[j] = result[j], result[i]
    return tuple(result)


def recognition_schedule(seed: int) -> tuple[str, ...]:
    return shuffled(("enemy",) * 15 + ("friend",) * 15, seed)


class DodgeChoiceEvent(TypedDict):
    safe_option_id: int
    warning_at_ms: int
    activation_at_ms: float
    resolved_at_ms: float


def dodge_choice_schedule(seed: int, warning_ms: float) -> tuple[DodgeChoiceEvent, ...]:
    """Build the fixed 30-round choice plan while consuming exactly 60 RNG values."""

    if (
        isinstance(warning_ms, bool)
        or not isinstance(warning_ms, (int, float))
        or not isfinite(warning_ms)
        or warning_ms <= 0
    ):
        raise ValueError("warning duration must be a positive finite number")
    state = seed
    result: list[DodgeChoiceEvent] = []
    for index in range(30):
        state = next_u32(state)
        warning = index * 2000 + 100 + state % 501
        state = next_u32(state)
        result.append(
            {
                "safe_option_id": state % 4,
                "warning_at_ms": warning,
                "activation_at_ms": warning + float(warning_ms),
                "resolved_at_ms": warning + float(warning_ms) + 150,
            }
        )
    return tuple(result)


def _registered_params(
    suite_id: str,
    test_id: str,
    difficulty: int,
    catalog_version: str,
) -> dict[str, object]:
    if suite_id not in MEMBERS or test_id not in MEMBERS[suite_id]:
        raise ValueError("unknown suite or task")
    if type(difficulty) is not int or not 1 <= difficulty <= 5:
        raise ValueError("difficulty must be a registered integer from 1 to 5")
    mobile = suite_id.startswith("mobile")
    size, speed, window, distance = PRACTICE_MULTIPLIERS[difficulty - 1]
    p: dict[str, object]
    if test_id == "precision":
        p = {
            "kind": "aim",
            "opportunity_count": 30,
            "window_ms": 2000,
            "target_diameter_deg": (5 if mobile else 3) * size,
            "angle_distances_deg": tuple(
                x * distance for x in ((8, 16, 24) if mobile else (10, 20, 30))
            ),
            "distance_repetitions": 10,
            "fast_ms": 550 if mobile else 350,
            "slow_ms": 2000,
        }
    elif test_id == "switching":
        p = {
            "kind": "aim",
            "opportunity_count": 40,
            "window_ms": 1500,
            "target_diameter_deg": (6 if mobile else 4) * size,
            "target_separation_deg": (20 if mobile else 25) * distance,
            "group_count": 20,
            "group_ms": 3000,
            "fast_ms": 450 if mobile else 300,
            "slow_ms": 1500,
        }
    elif test_id == "tracking":
        p = {
            "kind": "tracking",
            "target_diameter_deg": (6 if mobile else 4) * size,
            "speed_deg_s": (20 if mobile else 30) * speed,
        }
    elif test_id == "recognition":
        p = {
            "kind": "recognition",
            "target_diameter_deg": 4 * size,
            "angle_distances_deg": tuple(x * distance for x in (10, 20, 30)),
        }
    elif test_id == "move-fire":
        if catalog_version == "mobile-fps-v3":
            p = {
                "kind": "move_fire_v3",
                "target_diameter_deg": 6 * size,
                "checkpoint_distance_m": 1.2 * distance,
                "target_offset_min_deg": 12 * distance,
                "target_offset_max_deg": 20 * distance,
                "min_moving_ms": 100,
                "min_moving_speed_m_s": 1,
            }
        else:
            p = {
                "kind": "move_fire",
                "target_diameter_deg": 6 * size,
                "checkpoint_distance_m": 1.2 * distance,
            }
    elif test_id == "last-hit":
        p = {"kind": "last_hit", "last_hit_window_ms": 500 * window}
    elif test_id in ("orb-walk", "move-attack"):
        p = {
            "kind": "rhythm",
            "attack_windup_ms": 300 if mobile else 250,
            "checkpoint_distance": (90 if mobile else 100) * distance,
            "checkpoint_tolerance": 30 if mobile else 25,
        }
    elif test_id == "dodge":
        if catalog_version == f"{suite_id}-v3":
            warnings = (1250, 1200, 1100, 1050, 1000) if mobile else (1200, 1100, 1000, 950, 900)
            p = {
                "kind": "dodge_choice",
                "warning_ms": warnings[difficulty - 1],
                "safe_radius": 32,
                "candidate_offset": 20,
                "candidate_distance": 100,
                "warning_start_min_ms": 100,
                "warning_start_max_ms": 600,
                "burst_ms": 150,
            }
        else:
            p = {
                "kind": "dodge",
                "warning_ms": (550 if mobile else 400) * window,
                "projectile_speed": (350 if mobile else 400) * speed,
            }
    elif test_id == "skill-aim":
        p = {"kind": "skill_aim", "target_radius": 22 * size, "target_speed": 80 * speed}
    else:
        p = {"kind": "map", "event_window_ms": 1000 * window}
    return p


def get_task(
    suite_id: str,
    test_id: str,
    difficulty: int = 3,
    *,
    catalog_version: str | None = None,
) -> TaskConfig:
    version = registered_catalog_version(suite_id, catalog_version)
    p = _registered_params(suite_id, test_id, difficulty, version)
    identity = registered_rule(suite_id, test_id, version)
    return TaskConfig.model_validate(
        {
            "suite_id": suite_id,
            "test_id": test_id,
            "catalog_version": version,
            "engine_version": version,
            "scoring_version": identity.scoring_version,
            "report_version": identity.report_version,
            "duration_ms": 60000,
            "difficulty": difficulty,
            "params": p,
        }
    )


def get_suite(suite_id: str, *, catalog_version: str | None = None) -> SuiteConfig:
    version = registered_catalog_version(suite_id, catalog_version)
    return SuiteConfig.model_validate(
        {
            "suite_id": suite_id,
            "catalog_version": version,
            "test_ids": MEMBERS[suite_id],
            "tasks": {
                test: get_task(suite_id, test, catalog_version=version)
                for test in MEMBERS[suite_id]
            },
        }
    )


def validate_raw(config: TaskConfig, value: object) -> RawResult:
    # Revalidate even preconstructed objects; no model_construct/model_copy bypass.
    config = TaskConfig.model_validate(config.model_dump(mode="json"))
    if config != get_task(
        config.suite_id, config.test_id, config.difficulty, catalog_version=config.catalog_version
    ):
        raise ValueError("configuration differs from registered version")
    raw = RAW_ADAPTER.validate_python(
        value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    )
    p = config.params
    if raw.kind != p.kind:
        raise ValueError("raw result kind does not match task")
    if raw.kind == "aim":
        assert p.kind == "aim"
        if len(raw.trials) != p.opportunity_count:
            raise ValueError("wrong number of aim opportunities")
        hits = sum(t.hit for t in raw.trials)
        if (
            raw.shots < hits
            or raw.wrong_target_shots > raw.shots - hits
            or (config.test_id == "precision" and raw.wrong_target_shots)
        ):
            raise ValueError("inconsistent shot counts")
        for t in raw.trials:
            if (
                t.latency_ms > p.window_ms
                or (not t.hit and t.latency_ms != p.window_ms)
                or (t.hit and t.latency_ms >= p.window_ms)
            ):
                raise ValueError("invalid aim latency")
        if config.catalog_version == f"{config.suite_id}-v3" and config.test_id == "switching":
            for first, second in zip(raw.trials[::2], raw.trials[1::2]):
                if (not first.hit and second.hit) or (
                    second.hit and second.latency_ms < p.fire_interval_ms
                ):
                    raise ValueError("impossible v3 switching sequence")
    elif raw.kind == "recognition":
        assert p.kind == "recognition"
        if sum(t.target_kind == "enemy" for t in raw.trials) != p.enemy_count:
            raise ValueError("enemy/friend count mismatch")
        for recognition in raw.trials:
            if (
                recognition.latency_ms > p.window_ms
                or (not recognition.shot and recognition.latency_ms != p.window_ms)
                or (recognition.shot and recognition.latency_ms >= p.window_ms)
            ):
                raise ValueError("invalid recognition latency")
    elif raw.kind in ("move_fire", "move_fire_v3"):
        if raw.shots < sum(t.hit for t in raw.rounds):
            raise ValueError("hits exceed shots")
    elif raw.kind == "last_hit":
        assert p.kind == "last_hit"
        if any(t.offset_ms is not None and t.offset_ms >= p.last_hit_window_ms for t in raw.waves):
            raise ValueError("successful attack outside last-hit window")
    elif raw.kind == "rhythm":
        assert p.kind == "rhythm"
        if any(
            t.cancelled_count + int(t.attacked) > p.window_ms / p.attack_interval_ms
            for t in raw.rounds
        ):
            raise ValueError("round exceeds attack budget")
    elif raw.kind == "dodge":
        assert p.kind == "dodge"
        if any(t.response_ms is not None and t.response_ms >= p.window_ms for t in raw.threats):
            raise ValueError("response outside threat window")
    elif raw.kind == "dodge_choice":
        pass
    elif raw.kind == "skill_aim":
        assert p.kind == "skill_aim"
        if any(
            time is not None and time >= p.window_ms
            for t in raw.trials
            for time in (t.release_latency_ms, t.hit_latency_ms)
        ):
            raise ValueError("skill event outside window")
    elif raw.kind == "map":
        assert p.kind == "map"
        for event in raw.events:
            if (
                event.latency_ms > p.event_window_ms
                or (event.response == "missed" and event.latency_ms != p.event_window_ms)
                or (event.response != "missed" and event.latency_ms >= p.event_window_ms)
            ):
                raise ValueError("map event outside window")
    return raw


def validate_schedule(config: TaskConfig, raw: RawResult, seed: int) -> None:
    next_u32(seed)
    raw = validate_raw(config, raw)
    if raw.kind == "recognition" and tuple(
        t.target_kind for t in raw.trials
    ) != recognition_schedule(seed):
        raise ValueError("enemy/friend schedule does not match persisted seed")
    if raw.kind == "dodge_choice":
        assert config.params.kind == "dodge_choice"
        expected = dodge_choice_schedule(seed, config.params.warning_ms)
        for actual, planned in zip(raw.rounds, expected, strict=True):
            if any(
                getattr(actual, field) != planned[field]
                for field in (
                    "safe_option_id",
                    "warning_at_ms",
                    "activation_at_ms",
                    "resolved_at_ms",
                )
            ):
                raise ValueError("danger choice schedule does not match persisted seed")
