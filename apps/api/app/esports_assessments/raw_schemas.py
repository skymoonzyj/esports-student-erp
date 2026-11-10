"""Bounded, discriminated raw statistics; no scores, identities or input traces."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictInt, TypeAdapter, model_validator

from .config_schemas import Count, NonNegative, StrictModel

Time = Annotated[NonNegative, Field(le=60000)]
Shots = Annotated[Count, Field(le=300)]


class AimTrial(StrictModel):
    hit: StrictBool
    latency_ms: Time


class AimRaw(StrictModel):
    kind: Literal["aim"]
    trials: Annotated[tuple[AimTrial, ...], Field(min_length=30, max_length=40)]
    shots: Shots
    wrong_target_shots: Shots


class TrackingRaw(StrictModel):
    kind: Literal["tracking"]
    coverage_ms: Time
    loss_count: Annotated[Count, Field(le=7200)]
    reacquisition_ms: Annotated[tuple[Time, ...], Field(max_length=7200)]

    @model_validator(mode="after")
    def check_losses(self) -> Self:
        if (
            len(self.reacquisition_ms) > self.loss_count
            or sum(self.reacquisition_ms) + self.coverage_ms > 60000
        ):
            raise ValueError("reacquisition exceeds losses or uncovered duration")
        return self


class Spheretrack90Raw(StrictModel):
    kind: Literal["spheretrack90"]
    coverage_ms: Time
    loss_count: Annotated[Count, Field(le=7200)]
    reacquisition_ms: Annotated[tuple[Time, ...], Field(max_length=7200)]

    @model_validator(mode="after")
    def check_losses(self) -> Self:
        if (
            len(self.reacquisition_ms) > self.loss_count
            or sum(self.reacquisition_ms) + self.coverage_ms > 60000
        ):
            raise ValueError("reacquisition exceeds losses or uncovered duration")
        return self


class MultiTargetShot(StrictModel):
    at_ms: Annotated[NonNegative, Field(lt=60000)]
    hit: StrictBool


class MultiTargetRaw(StrictModel):
    kind: Literal["multi_target"]
    shots: Annotated[tuple[MultiTargetShot, ...], Field(max_length=1200)]

    @model_validator(mode="after")
    def check_intervals(self) -> Self:
        if any(
            second.at_ms - first.at_ms < 50 for first, second in zip(self.shots, self.shots[1:])
        ):
            raise ValueError("shots must increase by at least 50ms")
        return self


class RecognitionTrial(StrictModel):
    target_kind: Literal["enemy", "friend"]
    shot: StrictBool
    hit: StrictBool
    latency_ms: Time

    @model_validator(mode="after")
    def check_shot(self) -> Self:
        if self.hit and not self.shot:
            raise ValueError("a hit requires a shot")
        return self


class RecognitionRaw(StrictModel):
    kind: Literal["recognition"]
    trials: Annotated[tuple[RecognitionTrial, ...], Field(min_length=30, max_length=30)]


class MoveFireRound(StrictModel):
    hit: StrictBool
    reached: StrictBool


class MoveFireRaw(StrictModel):
    kind: Literal["move_fire"]
    rounds: Annotated[tuple[MoveFireRound, ...], Field(min_length=20, max_length=20)]
    shots: Shots


class MoveFireV3Round(StrictModel):
    hit: StrictBool
    reached: StrictBool
    hit_latency_ms: Time | None
    hit_speed_m_s: Annotated[NonNegative, Field(le=2)] | None
    pre_hit_moving_ms: Time | None

    @model_validator(mode="after")
    def check_moving_hit(self) -> Self:
        details = (self.hit_latency_ms, self.hit_speed_m_s, self.pre_hit_moving_ms)
        if (self.hit and not all(value is not None for value in details)) or (
            not self.hit and any(value is not None for value in details)
        ):
            raise ValueError("moving hit details are incomplete or present for a miss")
        if self.hit:
            assert self.hit_latency_ms is not None
            assert self.hit_speed_m_s is not None
            assert self.pre_hit_moving_ms is not None
            if (
                not 100 <= self.pre_hit_moving_ms <= self.hit_latency_ms < 3000
                or not 1 <= self.hit_speed_m_s <= 2
            ):
                raise ValueError("moving hit does not meet the registered movement window")
        return self


class MoveFireV3Raw(StrictModel):
    kind: Literal["move_fire_v3"]
    rounds: Annotated[tuple[MoveFireV3Round, ...], Field(min_length=20, max_length=20)]
    shots: Shots


class LastHitWave(StrictModel):
    success: StrictBool
    early: Annotated[Count, Field(le=4)]
    late: Annotated[Count, Field(le=4)]
    offset_ms: Time | None

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        if self.early + self.late + int(self.success) > 4 or self.success != (
            self.offset_ms is not None
        ):
            raise ValueError("wave attacks or successful offset are inconsistent")
        return self


class LastHitRaw(StrictModel):
    kind: Literal["last_hit"]
    waves: Annotated[tuple[LastHitWave, ...], Field(min_length=15, max_length=15)]


class LaneDeath(StrictModel):
    unit_id: Annotated[StrictInt, Field(ge=0, le=49)]
    at_ms: Annotated[StrictInt, Field(ge=0, lt=300000)]
    by_player: StrictBool


class LaneLastHitRaw(StrictModel):
    kind: Literal["lane_last_hit"]
    deaths: Annotated[tuple[LaneDeath, ...], Field(max_length=50)]
    attacks: Count
    cancelled_attacks: Annotated[Count, Field(le=15000)]

    @model_validator(mode="after")
    def check_deaths(self) -> Self:
        if (
            len({d.unit_id for d in self.deaths}) != len(self.deaths)
            or any(d.at_ms < d.unit_id // 5 * 30000 for d in self.deaths)
            or any(
                (a.at_ms, a.unit_id) >= (b.at_ms, b.unit_id)
                for a, b in zip(self.deaths, self.deaths[1:])
            )
        ):
            raise ValueError("小兵死亡事件重复、未生成或未按时间排序")
        return self


class LaneTowerDeath(StrictModel):
    unit_id: Annotated[Count, Field(le=74)]
    at_ms: Annotated[Count, Field(lt=300000)]
    by_player: StrictBool


class LaneTowerLastHitRaw(StrictModel):
    kind: Literal["lane_last_hit_towers"]
    deaths: Annotated[tuple[LaneTowerDeath, ...], Field(max_length=75)]
    attacks: Count
    cancelled_attacks: Annotated[Count, Field(le=15000)]
    hero_death_times: Annotated[
        tuple[Annotated[Count, Field(lt=300000)], ...], Field(max_length=60)
    ]
    hero_damage_taken: Annotated[Count, Field(le=90000)]

    @model_validator(mode="after")
    def check_lives_and_deaths(self) -> Self:
        if (
            len({d.unit_id for d in self.deaths}) != len(self.deaths)
            or any(d.at_ms < d.unit_id // 5 * 20000 for d in self.deaths)
            or any(
                (a.at_ms, a.unit_id) >= (b.at_ms, b.unit_id)
                for a, b in zip(self.deaths, self.deaths[1:])
            )
        ):
            raise ValueError("小兵死亡事件重复、未生成或未按时间排序")
        count = len(self.hero_death_times)
        if any(
            b - a < 5000 for a, b in zip(self.hero_death_times, self.hero_death_times[1:])
        ) or not 1000 * count <= self.hero_damage_taken < 1000 * (count + 1):
            raise ValueError("人物死亡次数、复活间隔或实际承伤不一致")
        return self


class RhythmRound(StrictModel):
    attacked: StrictBool
    reached: StrictBool
    cancelled_count: Annotated[Count, Field(le=2)]


class RhythmRaw(StrictModel):
    kind: Literal["rhythm"]
    rounds: Annotated[tuple[RhythmRound, ...], Field(min_length=30, max_length=30)]


class DodgeThreat(StrictModel):
    hit: StrictBool
    response_ms: Time | None


class DodgeRaw(StrictModel):
    kind: Literal["dodge"]
    threats: Annotated[tuple[DodgeThreat, ...], Field(min_length=30, max_length=30)]


class DodgeChoiceRound(StrictModel):
    hit: StrictBool
    safe_option_id: Annotated[StrictInt, Field(ge=0, le=3)]
    warning_at_ms: Annotated[StrictInt, Field(ge=0, le=60000)]
    activation_at_ms: Time
    resolved_at_ms: Time
    safe_entry_latency_ms: Time | None

    @model_validator(mode="after")
    def check_choice_timing(self) -> Self:
        if not self.warning_at_ms < self.activation_at_ms < self.resolved_at_ms:
            raise ValueError("danger choice event times are not ordered")
        entry_deadline = self.resolved_at_ms - self.warning_at_ms
        if self.safe_entry_latency_ms is not None and self.safe_entry_latency_ms >= entry_deadline:
            raise ValueError("safe entry must occur before resolution")
        if not self.hit and (
            self.safe_entry_latency_ms is None
            or self.safe_entry_latency_ms > self.activation_at_ms - self.warning_at_ms
        ):
            raise ValueError("a successful round must enter the safe area before activation")
        return self


class DodgeChoiceRaw(StrictModel):
    kind: Literal["dodge_choice"]
    rounds: Annotated[tuple[DodgeChoiceRound, ...], Field(min_length=30, max_length=30)]


class SkillAimTrial(StrictModel):
    released: StrictBool
    hit: StrictBool
    angle_error_deg: Annotated[NonNegative, Field(le=180)] | None
    release_latency_ms: Time | None
    hit_latency_ms: Time | None

    @model_validator(mode="after")
    def check_release(self) -> Self:
        if (
            self.released != (self.release_latency_ms is not None)
            or self.released != (self.angle_error_deg is not None)
            or self.hit != (self.hit_latency_ms is not None)
        ):
            raise ValueError("release/hit details are inconsistent")
        if self.hit and (
            not self.released
            or self.hit_latency_ms is None
            or self.release_latency_ms is None
            or self.hit_latency_ms < self.release_latency_ms
        ):
            raise ValueError("hit precedes release")
        return self


class SkillAimRaw(StrictModel):
    kind: Literal["skill_aim"]
    trials: Annotated[tuple[SkillAimTrial, ...], Field(min_length=30, max_length=30)]


class MapEvent(StrictModel):
    response: Literal["correct", "wrong", "missed"]
    latency_ms: Time


class MapRaw(StrictModel):
    kind: Literal["map"]
    main_hits: Annotated[tuple[StrictBool, ...], Field(min_length=30, max_length=30)]
    events: Annotated[tuple[MapEvent, ...], Field(min_length=12, max_length=12)]
    unprompted_count: Annotated[Count, Field(le=60000)]


RawResult = Annotated[
    AimRaw
    | MultiTargetRaw
    | Spheretrack90Raw
    | TrackingRaw
    | RecognitionRaw
    | MoveFireRaw
    | MoveFireV3Raw
    | LastHitRaw
    | LaneLastHitRaw
    | LaneTowerLastHitRaw
    | RhythmRaw
    | DodgeRaw
    | DodgeChoiceRaw
    | SkillAimRaw
    | MapRaw,
    Field(discriminator="kind"),
]
RAW_ADAPTER: TypeAdapter[RawResult] = TypeAdapter(RawResult)
