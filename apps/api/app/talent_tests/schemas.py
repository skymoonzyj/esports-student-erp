from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

TestId = Literal[
    "reactiontime",
    "aim",
    "sequence",
    "number-memory",
    "verbal-memory",
    "chimp",
    "memory",
    "dynamic-vision",
]
InputKind = Literal["mouse", "touch", "pen", "mixed", "unknown"]
Grade = Literal["low", "medium", "high", "exceptional"]
SessionStatus = Literal["in_progress", "completed", "abandoned"]

MAX_COUNTER = 1_000_000
MAX_DURATION_MS = 86_400_000


def _require_json_number(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a JSON number")  # noqa: TRY004 - Pydantic signal
    return value


Counter = Annotated[
    StrictInt,
    Field(ge=0, le=MAX_COUNTER),
]
PositiveCounter = Annotated[
    StrictInt,
    Field(ge=1, le=MAX_COUNTER),
]
IntegerDurationMs = Annotated[
    StrictInt,
    Field(ge=0, le=MAX_DURATION_MS),
]
FiniteNonNegative = Annotated[
    float,
    BeforeValidator(_require_json_number),
    Field(ge=0, allow_inf_nan=False),
]
FinitePositiveBounded = Annotated[
    float,
    BeforeValidator(_require_json_number),
    Field(gt=0, le=MAX_COUNTER, allow_inf_nan=False),
]
DurationMs = Annotated[
    float,
    BeforeValidator(_require_json_number),
    Field(ge=0, le=MAX_DURATION_MS, allow_inf_nan=False),
]
DynamicVisionDuration = Annotated[
    float,
    BeforeValidator(_require_json_number),
    Field(gt=0, le=10_000, allow_inf_nan=False),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Geometry(StrictModel):
    width: FinitePositiveBounded
    height: FinitePositiveBounded
    target_diameter: FinitePositiveBounded | None


class DeviceSnapshot(StrictModel):
    input_kind: InputKind
    viewport_width: FinitePositiveBounded
    viewport_height: FinitePositiveBounded
    geometry: Geometry | None


class ReactionRaw(StrictModel):
    samples_ms: list[FiniteNonNegative] = Field(min_length=5, max_length=5)
    too_soon_count: Counter


class AimRaw(StrictModel):
    intervals_ms: list[FiniteNonNegative] = Field(min_length=30, max_length=30)
    miss_count: Counter
    total_elapsed_ms: DurationMs

    @model_validator(mode="after")
    def require_exact_elapsed_total(self) -> AimRaw:
        from decimal import Decimal

        total = sum(Decimal(str(value)) for value in self.intervals_ms)
        if abs(total - Decimal(str(self.total_elapsed_ms))) > Decimal("0.001"):
            raise ValueError("total_elapsed_ms must equal the interval sum within 0.001ms")
        return self


class SequenceRaw(StrictModel):
    reached_level: PositiveCounter
    completed_level: Counter
    failed_step: PositiveCounter
    end_reason: Literal["wrong_tile"]

    @model_validator(mode="after")
    def require_consistent_progress(self) -> SequenceRaw:
        if self.completed_level != self.reached_level - 1:
            raise ValueError("completed_level must equal reached_level - 1")
        if self.failed_step > self.reached_level:
            raise ValueError("failed_step must be between 1 and reached_level")
        return self


class NumberMemoryRaw(StrictModel):
    correct_digits: Counter
    failed_digits: PositiveCounter
    display_duration_ms: IntegerDurationMs

    @model_validator(mode="after")
    def require_consistent_failure(self) -> NumberMemoryRaw:
        if self.failed_digits != self.correct_digits + 1:
            raise ValueError("failed_digits must equal correct_digits + 1")
        if self.display_duration_ms != 1000 + 800 * self.failed_digits:
            raise ValueError("display_duration_ms does not match the failed digit count")
        return self


class VerbalMemoryRaw(StrictModel):
    correct_count: Counter
    error_count: Counter
    total_count: Counter
    word_bank_version: Literal["zh-192-v1"]

    @model_validator(mode="after")
    def require_three_errors(self) -> VerbalMemoryRaw:
        if self.error_count != 3:
            raise ValueError("error_count must equal 3")
        if self.total_count != self.correct_count + 3:
            raise ValueError("total_count must equal correct_count + 3")
        return self


class ChimpRaw(StrictModel):
    reached_count: Annotated[StrictInt, Field(ge=4, le=40)]
    completed_count: Annotated[StrictInt, Field(ge=0, le=40)]
    strikes: Annotated[StrictInt, Field(ge=0, le=3)]
    end_reason: Literal["strikes", "engine_cap"]

    @model_validator(mode="after")
    def require_consistent_ending(self) -> ChimpRaw:
        if self.end_reason == "engine_cap":
            if self.reached_count != 40 or self.completed_count != 40 or self.strikes >= 3:
                raise ValueError(
                    "engine_cap requires a completed 40-count run with fewer than 3 strikes"
                )
            return self
        expected_completed = 0 if self.reached_count == 4 else self.reached_count - 1
        if self.strikes != 3 or self.completed_count != expected_completed:
            raise ValueError("strikes ending requires 3 strikes and consistent completed_count")
        return self


class MemoryRaw(StrictModel):
    reached_level: PositiveCounter
    completed_level: Counter
    accepted_misses: Counter
    lives_used: Counter
    end_reason: Literal["lives"]

    @model_validator(mode="after")
    def require_consistent_ending(self) -> MemoryRaw:
        if self.completed_level != self.reached_level - 1:
            raise ValueError("completed_level must equal reached_level - 1")
        if self.lives_used != 3:
            raise ValueError("lives_used must equal 3")
        if self.accepted_misses < 9:
            raise ValueError("accepted_misses must be at least 9")
        if self.accepted_misses > 9 + 2 * self.completed_level:
            raise ValueError("accepted_misses cannot exceed 9 + 2 * completed_level")
        return self


class DynamicVisionRaw(StrictModel):
    selected_level: Annotated[StrictInt, Field(ge=1, le=5)]
    outcomes: list[Literal["correct", "incorrect", "skipped"]] = Field(min_length=5, max_length=5)
    measured_display_ms: list[DynamicVisionDuration] = Field(min_length=5, max_length=5)
    font_size_px: StrictInt

    @model_validator(mode="after")
    def require_supported_font(self) -> DynamicVisionRaw:
        if self.font_size_px not in (40, 56):
            raise ValueError("font_size_px must be 40 or 56")
        return self


class SubmissionBase(StrictModel):
    device: DeviceSnapshot
    duration_ms: DurationMs
    background_interrupted: StrictBool
    submission_id: UUID
    locale: Literal["zh-CN"]


class ReactionResultInput(SubmissionBase):
    test_id: Literal["reactiontime"]
    raw: ReactionRaw
    engine_version: Literal["reactiontime-clone-v1"]

    @model_validator(mode="after")
    def require_covering_duration(self) -> ReactionResultInput:
        from decimal import Decimal

        known_duration = sum(Decimal(str(value)) for value in self.raw.samples_ms)
        if Decimal(str(self.duration_ms)) < known_duration:
            raise ValueError("duration_ms must cover all reaction samples")
        return self


class AimResultInput(SubmissionBase):
    test_id: Literal["aim"]
    raw: AimRaw
    engine_version: Literal["aim-clone-v1"]

    @model_validator(mode="after")
    def require_geometry_and_duration(self) -> AimResultInput:
        from decimal import Decimal

        geometry = self.device.geometry
        if geometry is None or geometry.target_diameter is None:
            raise ValueError("aim requires target geometry")
        if geometry.target_diameter > min(geometry.width, geometry.height):
            raise ValueError("target diameter must fit inside the field")
        if abs(Decimal(str(self.duration_ms)) - Decimal(str(self.raw.total_elapsed_ms))) > Decimal(
            "0.001"
        ):
            raise ValueError("duration_ms must equal total_elapsed_ms within 0.001ms")
        return self


class SequenceResultInput(SubmissionBase):
    test_id: Literal["sequence"]
    raw: SequenceRaw
    engine_version: Literal["sequence-clone-v1"]


class NumberMemoryResultInput(SubmissionBase):
    test_id: Literal["number-memory"]
    raw: NumberMemoryRaw
    engine_version: Literal["number-memory-clone-v1"]

    @model_validator(mode="after")
    def require_covering_duration(self) -> NumberMemoryResultInput:
        if self.duration_ms < self.raw.display_duration_ms:
            raise ValueError("duration_ms must cover display_duration_ms")
        return self


class VerbalMemoryResultInput(SubmissionBase):
    test_id: Literal["verbal-memory"]
    raw: VerbalMemoryRaw
    engine_version: Literal["verbal-memory-clone-v1"]


class ChimpResultInput(SubmissionBase):
    test_id: Literal["chimp"]
    raw: ChimpRaw
    engine_version: Literal["chimp-clone-v1"]


class MemoryResultInput(SubmissionBase):
    test_id: Literal["memory"]
    raw: MemoryRaw
    engine_version: Literal["memory-clone-v1"]


class DynamicVisionResultInput(SubmissionBase):
    test_id: Literal["dynamic-vision"]
    raw: DynamicVisionRaw
    engine_version: Literal["dynamic-vision-v1"]

    @model_validator(mode="after")
    def require_geometry_font_and_duration(self) -> DynamicVisionResultInput:
        from decimal import Decimal

        geometry = self.device.geometry
        if geometry is None:
            raise ValueError("dynamic-vision requires test-area geometry")
        if geometry.target_diameter is not None:
            raise ValueError("dynamic-vision target_diameter must be null")
        expected_font_size = 40 if self.device.viewport_width < 600 else 56
        if self.raw.font_size_px != expected_font_size:
            raise ValueError("font_size_px does not match viewport width")
        measured_total = sum(
            (Decimal(str(value)) for value in self.raw.measured_display_ms),
            start=Decimal(0),
        )
        if Decimal(str(self.duration_ms)) < measured_total:
            raise ValueError("duration_ms must cover all measured display durations")
        return self


RawResultInput = Annotated[
    ReactionResultInput
    | AimResultInput
    | SequenceResultInput
    | NumberMemoryResultInput
    | VerbalMemoryResultInput
    | ChimpResultInput
    | MemoryResultInput
    | DynamicVisionResultInput,
    Field(discriminator="test_id"),
]
RawStatistics = (
    ReactionRaw
    | AimRaw
    | SequenceRaw
    | NumberMemoryRaw
    | VerbalMemoryRaw
    | ChimpRaw
    | MemoryRaw
    | DynamicVisionRaw
)


class TestRule(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_version: str
    anchors: tuple[int, int, int, int, int, int]
    source_label: str
    unit: str


class DynamicVisionLevel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: Literal[1, 2, 3, 4, 5]
    digit_count: Literal[3, 4, 5, 6]
    nominal_display_ms: Literal[36, 100]
    coefficient_tenths: Literal[12, 14, 16, 18, 20]


class DynamicVisionRule(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["dynamic_vision"]
    engine_version: Literal["dynamic-vision-v1"]
    source_label: Literal["内部试行训练规则"]
    unit: Literal["count"]
    base_points_per_correct: Literal[10]
    levels: tuple[
        DynamicVisionLevel,
        DynamicVisionLevel,
        DynamicVisionLevel,
        DynamicVisionLevel,
        DynamicVisionLevel,
    ]


class RuleSnapshot(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_version: str
    scoring_version: str
    report_version: str
    test_ids: tuple[TestId, ...]
    tests: dict[TestId, TestRule | DynamicVisionRule]


class NormalizedResult(StrictModel):
    test_id: TestId
    raw_value: int
    unit: str
    score: Annotated[int, Field(ge=0, le=100)]
    grade: Grade
    quality_flags: list[str]
    raw: RawStatistics
    device: DeviceSnapshot
    duration_ms: float
    background_interrupted: bool
    engine_version: str
    scoring_version: str
    locale: Literal["zh-CN"]


class PersonDisplay(StrictModel):
    id: UUID
    name: str
    nickname: str | None


class ReportComparison(StrictModel):
    baseline_report_id: UUID
    raw_delta: int
    score_delta: int


class ReportSnapshot(StrictModel):
    person: PersonDisplay
    session_id: UUID
    completed_at: datetime
    rules: RuleSnapshot
    results: list[NormalizedResult]
    strengths: list[TestId]
    suggestions: list[str]
    comparisons: dict[TestId, ReportComparison]
    mixed_devices: bool
    disclaimer: str


class ResultView(NormalizedResult):
    id: UUID
    submission_id: UUID
    created_at: datetime


class SessionView(StrictModel):
    id: UUID
    person_id: UUID
    status: SessionStatus
    created_at: datetime
    completed_at: datetime | None
    abandoned_at: datetime | None
    rules: RuleSnapshot
    results: list[ResultView]
    report_id: UUID | None


class ResultSaveView(StrictModel):
    session: SessionView
    result: ResultView


class SessionSummary(StrictModel):
    id: UUID
    status: SessionStatus
    created_at: datetime
    completed_at: datetime | None
    abandoned_at: datetime | None
    saved_count: int
    required_count: int
    report_id: UUID | None


class ReportSummary(StrictModel):
    id: UUID
    session_id: UUID
    person: PersonDisplay
    completed_at: datetime
    catalog_version: str
    scoring_version: str


class ReportView(ReportSnapshot):
    id: UUID


class Page[T](StrictModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class ContextView(StrictModel):
    catalog: RuleSnapshot
    can_take: bool
    can_read_self: bool
    can_read_reports: bool
    active_session_id: UUID | None


class SessionCreateRequest(StrictModel):
    request_id: UUID
    catalog_version: str


class AbandonRequest(StrictModel):
    request_id: UUID


class PageParams(StrictModel):
    page: StrictInt = Field(default=1, ge=1)
    page_size: StrictInt = Field(default=20, ge=1, le=100)


class SubjectPageParams(PageParams):
    name: str | None = Field(default=None, max_length=80)
