"""API DTOs. Settings/layout coordinates never carry device identifiers."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from .config_schemas import (
    AttackMode,
    Difficulty,
    FiniteNumber,
    NonNegative,
    Positive,
    Seed,
    StrictModel,
    SuiteConfig,
    SuiteId,
    TaskConfig,
    TestId,
    members_for_catalog,
)
from .raw_schemas import RawResult

Mode = Literal["formal", "practice"]
RunState = Literal["running", "awaiting_submission", "interrupted", "expired", "submitted"]
InputKind = Literal["mouse", "touch", "mixed", "unknown"]
Grade = Literal["low", "medium", "high", "exceptional"]
QualityFlag = Literal[
    "incomplete_duration",
    "frame_gap",
    "long_frames",
    "mixed_input",
    "unconfirmed_input",
    "hidden",
    "interrupted",
]


class ControlRect(StrictModel):
    """x/y are center ratios in the scene; width/height are CSS pixels."""

    id: Literal["fire", "move", "attack", "skill"]
    kind: Literal["button", "joystick"]
    x: Annotated[NonNegative, Field(le=1)]
    y: Annotated[NonNegative, Field(le=1)]
    width: Positive
    height: Positive

    @model_validator(mode="after")
    def check_diameter(self) -> Self:
        low, high = (96, 160) if self.kind == "joystick" else (48, 96)
        if (
            self.width != self.height
            or not low <= self.width <= high
            or (self.id == "move") != (self.kind == "joystick")
        ):
            raise ValueError("invalid control diameter or role")
        return self


class ControlSettings(StrictModel):
    sensitivity: Annotated[FiniteNumber, Field(ge=0.5, le=2)] = 1
    input_kind: Literal["mouse", "touch"]
    mouse_mode: Literal["raw", "relative"] | None = None
    controls: Annotated[tuple[ControlRect, ...], Field(max_length=3)] = ()

    @model_validator(mode="after")
    def check_settings(self) -> Self:
        if abs(self.sensitivity * 20 - round(self.sensitivity * 20)) > 1e-8:
            raise ValueError("sensitivity must use increments of 0.05")
        if self.input_kind == "mouse" and self.controls:
            raise ValueError("mouse settings cannot have touch controls")
        if self.input_kind == "touch" and self.mouse_mode is not None:
            raise ValueError("touch settings cannot have mouse mode")
        if len({c.id for c in self.controls}) != len(self.controls):
            raise ValueError("duplicate control")
        return self


class EnvironmentSnapshot(StrictModel):
    input_kind: InputKind
    is_phone: StrictBool
    touch_capable: StrictBool
    width_css_px: Positive
    height_css_px: Positive
    device_pixel_ratio: Annotated[Positive, Field(le=16)]
    pointer_lock_supported: StrictBool
    pointer_lock_active: StrictBool
    raw_mouse_requested: StrictBool
    raw_mouse_confirmed: StrictBool
    webgl2_supported: StrictBool


def validate_settings(
    config: TaskConfig, settings: ControlSettings, environment: EnvironmentSnapshot | None = None
) -> None:
    mobile = config.suite_id.startswith("mobile")
    if settings.input_kind != ("touch" if mobile else "mouse"):
        raise ValueError("settings input does not match suite")
    if config.suite_id == "pc-fps":
        if settings.mouse_mode is None:
            raise ValueError("PC FPS requires an explicit mouse mode")
    elif settings.mouse_mode is not None:
        raise ValueError("mouse mode is only relevant to PC FPS")
    if mobile:
        allowed = (
            {"fire", "move"} if config.suite_id == "mobile-fps" else {"move", "attack", "skill"}
        )
        if any(c.id not in allowed for c in settings.controls):
            raise ValueError("control does not belong to suite")
    if environment is not None:
        if environment.input_kind != settings.input_kind:
            raise ValueError("start input must match suite")
        min_width, min_height = (560, 280) if mobile else (800, 450)
        if environment.width_css_px < min_width or environment.height_css_px < min_height:
            raise ValueError("scene is too small")
        if mobile and (
            not environment.is_phone
            or not environment.touch_capable
            or environment.width_css_px <= environment.height_css_px
        ):
            raise ValueError("mobile scene requires a landscape phone with touch")
        if config.suite_id == "pc-fps" and not environment.pointer_lock_supported:
            raise ValueError("PC FPS requires Pointer Lock capability")
        if "fps" in config.suite_id and not environment.webgl2_supported:
            raise ValueError("FPS requires WebGL2")
        rectangles: list[tuple[float, float, float, float]] = []
        for c in settings.controls:
            x, y = c.x * environment.width_css_px, c.y * environment.height_css_px
            rect = (x - c.width / 2, y - c.height / 2, x + c.width / 2, y + c.height / 2)
            if (
                rect[0] < 0
                or rect[1] < 0
                or rect[2] > environment.width_css_px
                or rect[3] > environment.height_css_px
            ):
                raise ValueError("control is outside scene")
            if any(
                rect[0] < r[2] and rect[2] > r[0] and rect[1] < r[3] and rect[3] > r[1]
                for r in rectangles
            ):
                raise ValueError("controls overlap")
            rectangles.append(rect)


class QualityInput(StrictModel):
    duration_ms: Annotated[NonNegative, Field(le=300000)]
    max_frame_gap_ms: Annotated[NonNegative, Field(le=300000)]
    long_frame_gap_total_ms: Annotated[NonNegative, Field(le=300000)]
    input_kind: InputKind
    hidden: StrictBool
    interrupted: StrictBool


class OperationRequest(StrictModel):
    operation_key: UUID


class SessionCreate(OperationRequest):
    suite_id: SuiteId
    attack_mode: AttackMode | None = None


class AttemptCreate(OperationRequest):
    test_id: TestId
    settings: ControlSettings
    environment: EnvironmentSnapshot


class PracticeCreate(AttemptCreate):
    suite_id: SuiteId
    difficulty: Difficulty = 3
    attack_mode: AttackMode | None = None


class ResultSubmit(OperationRequest):
    raw: RawResult
    quality: QualityInput


class AttemptView(StrictModel):
    id: UUID
    mode: Mode
    session_id: UUID | None
    suite_id: SuiteId
    test_id: TestId
    state: RunState
    server_now: datetime
    issued_at: datetime
    run_until: datetime
    submit_until: datetime
    seed: Seed
    config: TaskConfig
    settings: ControlSettings
    environment: EnvironmentSnapshot

    @model_validator(mode="after")
    def check_attempt(self) -> Self:
        if (self.mode == "formal") != (self.session_id is not None):
            raise ValueError("session identity does not match mode")
        if (
            self.suite_id != self.config.suite_id
            or self.test_id != self.config.test_id
            or (self.mode == "formal" and self.config.difficulty != 3)
        ):
            raise ValueError("attempt configuration mismatch")
        validate_settings(self.config, self.settings)
        if self.run_until <= self.issued_at or self.submit_until <= self.run_until:
            raise ValueError("invalid attempt deadlines")
        return self


MetricValue = FiniteNumber | None


class ScoredResult(StrictModel):
    metrics: Annotated[dict[str, MetricValue], Field(max_length=32)]
    score: Annotated[StrictInt, Field(ge=0, le=100)]
    grade: Grade
    quality_flags: tuple[QualityFlag, ...]


class PracticeResult(StrictModel):
    metrics: Annotated[dict[str, MetricValue], Field(max_length=32)]
    score: None = None
    grade: None = None
    quality_flags: tuple[QualityFlag, ...]


class SessionResultView(StrictModel):
    result_id: UUID
    test_id: TestId
    scored: ScoredResult


class SessionView(StrictModel):
    id: UUID
    suite_id: SuiteId
    suite_config: SuiteConfig
    status: Literal["in_progress", "completed", "abandoned"]
    members: Annotated[tuple[TestId, ...], Field(min_length=1, max_length=4)]
    results: Annotated[tuple[SessionResultView, ...], Field(max_length=4)]
    report_id: UUID | None

    @model_validator(mode="after")
    def check_members(self) -> Self:
        if (
            self.members != members_for_catalog(self.suite_id, self.suite_config.catalog_version)
            or self.suite_config.suite_id != self.suite_id
            or self.suite_config.test_ids != self.members
            or len({r.test_id for r in self.results}) != len(self.results)
            or any(r.test_id not in self.members for r in self.results)
        ):
            raise ValueError("invalid session result members")
        if self.status == "completed" and (
            len(self.results) != len(self.members) or self.report_id is None
        ):
            raise ValueError("completed session requires all frozen results and a report")
        return self


class FormalResultSaveView(StrictModel):
    mode: Literal["formal"] = "formal"
    result_id: UUID
    scored: ScoredResult
    session: SessionView


class PracticeResultSaveView(StrictModel):
    mode: Literal["practice"] = "practice"
    result_id: UUID
    scored: PracticeResult
    session: None = None


ResultSaveView = Annotated[
    FormalResultSaveView | PracticeResultSaveView, Field(discriminator="mode")
]


class PersonDisplaySnapshot(StrictModel):
    person_id: UUID
    display_name: Annotated[str, Field(min_length=1, max_length=200)]


class ReportResult(StrictModel):
    result_id: UUID
    test_id: TestId
    config: TaskConfig
    settings: ControlSettings
    environment: EnvironmentSnapshot
    scored: ScoredResult


class ReportComparison(StrictModel):
    test_id: TestId
    previous_report_id: UUID | None
    score_delta: Annotated[StrictInt, Field(ge=-100, le=100)] | None
    non_comparable_reasons: tuple[str, ...]


class AssessmentReportView(StrictModel):
    id: UUID
    session_id: UUID
    suite_id: SuiteId
    suite: SuiteConfig
    person: PersonDisplaySnapshot
    completed_at: datetime
    results: Annotated[tuple[ReportResult, ...], Field(min_length=1, max_length=4)]
    recommendations: Annotated[tuple[str, ...], Field(max_length=16)]
    comparisons: Annotated[tuple[ReportComparison, ...], Field(max_length=4)]

    @model_validator(mode="after")
    def check_frozen_members(self) -> Self:
        if (
            self.suite.suite_id != self.suite_id
            or tuple(result.test_id for result in self.results) != self.suite.test_ids
        ):
            raise ValueError("report requires each frozen member in catalog order")
        if any(result.config != self.suite.tasks[result.test_id] for result in self.results):
            raise ValueError("report task configuration differs from frozen catalog")
        return self
