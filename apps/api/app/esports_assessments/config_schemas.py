"""Frozen JSON contracts. Angles are degrees, times milliseconds unless named otherwise."""

from collections.abc import Mapping
from math import isfinite
from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    TypeAdapter,
    field_serializer,
    model_validator,
)


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # Pydantic wraps ValueError, but deliberately propagates TypeError.
        raise ValueError("expected a finite number, not a boolean or string")  # noqa: TRY004
    try:
        converted = float(value)
    except OverflowError as exc:
        raise ValueError("number is outside the finite float range") from exc
    if not isfinite(converted):
        raise ValueError("expected a finite number")
    return converted


FiniteNumber = Annotated[float, BeforeValidator(_finite_number)]
NonNegative = Annotated[FiniteNumber, Field(ge=0)]
Positive = Annotated[FiniteNumber, Field(gt=0)]
Count = Annotated[StrictInt, Field(ge=0)]
Difficulty = Annotated[StrictInt, Field(ge=1, le=5)]
Seed = Annotated[StrictInt, Field(ge=0, le=0xFFFFFFFF)]
SuiteId = Literal["pc-fps", "mobile-fps", "pc-moba", "mobile-moba"]
TestId = Literal[
    "precision",
    "tracking",
    "switching",
    "recognition",
    "move-fire",
    "last-hit",
    "orb-walk",
    "dodge",
    "map-awareness",
    "skill-aim",
    "move-attack",
]
MEMBERS: dict[str, tuple[str, ...]] = {
    "pc-fps": ("precision", "tracking", "switching", "recognition"),
    "mobile-fps": ("precision", "tracking", "switching", "move-fire"),
    "pc-moba": ("last-hit", "orb-walk", "dodge", "map-awareness"),
    "mobile-moba": ("dodge", "skill-aim", "move-attack", "map-awareness"),
}
KINDS = {
    "precision": "aim",
    "switching": "aim",
    "tracking": "tracking",
    "recognition": "recognition",
    "move-fire": "move_fire",
    "last-hit": "last_hit",
    "orb-walk": "rhythm",
    "move-attack": "rhythm",
    "dodge": "dodge",
    "skill-aim": "skill_aim",
    "map-awareness": "map",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class FpsParams(StrictModel):
    horizontal_fov_deg: Positive = 90
    pitch_limit_deg: Positive = 70
    fire_interval_ms: Positive = 200


class AimParams(FpsParams):
    kind: Literal["aim"] = "aim"
    opportunity_count: Annotated[StrictInt, Field(gt=0, le=40)]
    window_ms: Positive
    target_diameter_deg: Positive
    angle_distances_deg: tuple[Positive, ...] = ()
    distance_repetitions: Count = 0
    target_separation_deg: NonNegative = 0
    group_count: Count = 0
    group_ms: NonNegative = 0
    fast_ms: Positive
    slow_ms: Positive


class TrackingParams(FpsParams):
    kind: Literal["tracking"] = "tracking"
    target_diameter_deg: Positive
    speed_deg_s: Positive
    horizontal_bound_deg: Positive = 30
    vertical_bound_deg: Positive = 15
    direction_interval_ms: Positive = 2000
    turn_ms: Positive = 200


class RecognitionParams(FpsParams):
    kind: Literal["recognition"] = "recognition"
    opportunity_count: StrictInt = 30
    enemy_count: StrictInt = 15
    friend_count: StrictInt = 15
    round_ms: Positive = 2000
    window_ms: Positive = 1200
    target_diameter_deg: Positive = 4
    angle_distances_deg: tuple[Positive, ...] = (10, 20, 30)
    distance_repetitions: StrictInt = 10
    fast_ms: Positive = 250
    slow_ms: Positive = 1200


class MoveFireParams(FpsParams):
    kind: Literal["move_fire"] = "move_fire"
    opportunity_count: StrictInt = 20
    window_ms: Positive = 3000
    target_diameter_deg: Positive = 6
    checkpoint_distance_m: Positive = 1.2
    checkpoint_tolerance_m: Positive = 0.35
    player_speed_m_s: Positive = 2


class MoveFireV3Params(FpsParams):
    kind: Literal["move_fire_v3"] = "move_fire_v3"
    opportunity_count: StrictInt = 20
    window_ms: Positive = 3000
    target_diameter_deg: Positive = 6
    checkpoint_distance_m: Positive = 1.2
    checkpoint_tolerance_m: Positive = 0.35
    player_speed_m_s: Positive = 2
    target_offset_min_deg: Positive = 12
    target_offset_max_deg: Positive = 20
    min_moving_ms: Positive = 100
    min_moving_speed_m_s: Positive = 1


class MobaParams(StrictModel):
    logical_width: Positive = 1000
    logical_height: Positive = 562.5
    player_radius: Positive = 12
    player_speed: Positive = 180
    attack_range: Positive = 180


class LastHitParams(MobaParams):
    kind: Literal["last_hit"] = "last_hit"
    opportunity_count: StrictInt = 15
    window_ms: Positive = 4000
    attack_windup_ms: Positive = 250
    attack_interval_ms: Positive = 1000
    attack_damage: Positive = 10
    last_hit_window_ms: Positive = 500
    window_starts_ms: tuple[Positive, ...] = (2000, 2250, 2500, 2750, 3000)


class RhythmParams(MobaParams):
    kind: Literal["rhythm"] = "rhythm"
    opportunity_count: StrictInt = 30
    window_ms: Positive = 2000
    attack_windup_ms: Positive
    attack_interval_ms: Positive = 1000
    checkpoint_distance: Positive
    checkpoint_tolerance: Positive


class DodgeParams(MobaParams):
    kind: Literal["dodge"] = "dodge"
    opportunity_count: StrictInt = 30
    window_ms: Positive = 2000
    warning_ms: Positive
    projectile_radius: Positive = 10
    projectile_speed: Positive
    spawn_distance: Positive = 240


class DodgeChoiceParams(MobaParams):
    kind: Literal["dodge_choice"] = "dodge_choice"
    opportunity_count: StrictInt = 30
    window_ms: Positive = 2000
    warning_ms: Positive
    safe_radius: Positive = 32
    candidate_offset: Positive = 20
    candidate_distance: Positive = 100
    warning_start_min_ms: NonNegative = 100
    warning_start_max_ms: Positive = 600
    burst_ms: Positive = 150


class SkillAimParams(MobaParams):
    kind: Literal["skill_aim"] = "skill_aim"
    opportunity_count: StrictInt = 30
    window_ms: Positive = 2000
    target_radius: Positive = 22
    target_speed: Positive = 80
    projectile_speed: Positive = 600
    projectile_radius: Positive = 10
    max_distance: Positive = 500


class MapParams(MobaParams):
    kind: Literal["map"] = "map"
    opportunity_count: StrictInt = 30
    window_ms: Positive = 2000
    target_radius: Positive = 24
    event_count: StrictInt = 12
    event_start_ms: Positive = 3000
    event_interval_ms: Positive = 5000
    event_window_ms: Positive = 1000
    minimap_x: NonNegative = 820
    minimap_y: NonNegative = 0
    minimap_width: Positive = 180
    minimap_height: Positive = 110


Params = Annotated[
    AimParams
    | TrackingParams
    | RecognitionParams
    | MoveFireParams
    | MoveFireV3Params
    | LastHitParams
    | RhythmParams
    | DodgeParams
    | DodgeChoiceParams
    | SkillAimParams
    | MapParams,
    Field(discriminator="kind"),
]


def registered_catalog_version(suite_id: str, version: str | None = None) -> str:
    """Resolve a current default or an exact supported frozen catalog identity."""
    if suite_id not in MEMBERS:
        raise ValueError("unknown suite")
    legacy = f"{suite_id}-v1"
    previous = f"{suite_id}-v2"
    current = f"{suite_id}-v3"
    resolved = current if version is None else version
    if resolved not in (legacy, previous, current):
        raise ValueError("unknown catalog version")
    return resolved


class TaskConfig(StrictModel):
    suite_id: SuiteId
    test_id: TestId
    catalog_version: str
    engine_version: str
    scoring_version: str
    report_version: str
    duration_ms: Annotated[StrictInt, Field(ge=60000, le=60000)] = 60000
    difficulty: Difficulty = 3
    params: Params

    @model_validator(mode="after")
    def check_identity(self) -> Self:
        from .catalog import _registered_params
        from .rule_versions import registered_rule

        if self.test_id not in MEMBERS[self.suite_id]:
            raise ValueError("task does not belong to suite or engine")
        registered_catalog_version(self.suite_id, self.catalog_version)
        identity = registered_rule(self.suite_id, self.test_id, self.catalog_version)
        if (
            self.engine_version != self.catalog_version
            or self.params.kind != identity.raw_kind
            or self.scoring_version != identity.scoring_version
            or self.report_version != identity.report_version
        ):
            raise ValueError("unknown rule version")
        p = self.params
        expected: Params = TypeAdapter(Params).validate_python(
            _registered_params(
                self.suite_id,
                self.test_id,
                self.difficulty,
                self.catalog_version,
            )
        )
        if p != expected:
            raise ValueError("parameters differ from the registered rule version")
        if (
            isinstance(p, DodgeParams)
            and p.warning_ms + p.spawn_distance / p.projectile_speed * 1000 > p.window_ms
        ):
            raise ValueError("threat cannot reach its target within the round")
        if (
            isinstance(p, RhythmParams)
            and p.attack_windup_ms + p.checkpoint_distance / p.player_speed * 1000 > p.window_ms
        ):
            raise ValueError("checkpoint is unreachable")
        if (
            isinstance(p, LastHitParams)
            and max(p.window_starts_ms) + p.last_hit_window_ms > p.window_ms
        ):
            raise ValueError("last-hit window exceeds wave")
        if isinstance(p, (AimParams, RecognitionParams)):
            farthest = max(
                p.angle_distances_deg,
                default=p.target_separation_deg if isinstance(p, AimParams) else 0,
            )
            if farthest + p.target_diameter_deg / 2 > p.horizontal_fov_deg / 2:
                raise ValueError("target is not fully visible")
        if isinstance(p, MoveFireV3Params) and (
            p.target_offset_min_deg <= p.target_diameter_deg / 2
            or p.target_offset_max_deg + p.target_diameter_deg / 2 > p.horizontal_fov_deg / 2
        ):
            raise ValueError("moving target is not fully visible away from the reticle")
        if isinstance(p, DodgeChoiceParams) and (
            p.warning_start_min_ms > p.warning_start_max_ms
            or p.warning_start_max_ms + p.warning_ms + p.burst_ms > p.window_ms
            or p.candidate_offset + p.candidate_distance + p.safe_radius
            > min(p.logical_width / 2, p.logical_height / 2)
        ):
            raise ValueError("danger choice timing or geometry is infeasible")
        return self


class SuiteConfig(StrictModel):
    suite_id: SuiteId
    catalog_version: str
    test_ids: tuple[TestId, TestId, TestId, TestId]
    tasks: Mapping[TestId, TaskConfig]

    @model_validator(mode="after")
    def freeze_tasks(self) -> Self:
        if self.test_ids != MEMBERS[self.suite_id] or set(self.tasks) != set(self.test_ids):
            raise ValueError("suite members do not match the frozen catalog")
        registered_catalog_version(self.suite_id, self.catalog_version)
        if any(
            t.suite_id != self.suite_id
            or t.test_id != key
            or t.difficulty != 3
            or t.catalog_version != self.catalog_version
            for key, t in self.tasks.items()
        ):
            raise ValueError("formal suite contains an incompatible task")
        object.__setattr__(
            self, "tasks", MappingProxyType({key: self.tasks[key] for key in self.test_ids})
        )
        return self

    @field_serializer("tasks")
    def serialize_tasks(self, tasks: Mapping[TestId, TaskConfig]) -> dict[str, object]:
        return {key: value.model_dump(mode="json") for key, value in tasks.items()}
