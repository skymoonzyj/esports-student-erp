"""Supplemental wire DTOs mirrored by web/esportsAssessments/apiContracts.ts."""

import re
from datetime import date, datetime
from typing import Annotated, Literal, Self, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator, model_validator

from .config_schemas import (
    StrictModel,
    SuiteConfig,
    SuiteId,
    TaskConfig,
    TestId,
    is_supported_suite_test,
)
from .schemas import (
    AttemptView,
    ControlSettings,
    EnvironmentSnapshot,
    Mode,
    PersonDisplaySnapshot,
    PracticeResult,
    ResultSaveView,
    SessionView,
    validate_settings,
)

T = TypeVar("T")


class CursorPage[T](StrictModel):
    items: Annotated[tuple[T, ...], Field(max_length=100)]
    next_cursor: Annotated[str, Field(min_length=1, max_length=2048)] | None
    visible_total: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def check_total(self) -> Self:
        if self.visible_total < len(self.items):
            raise ValueError("visible total cannot be smaller than page")
        return self


class ActiveRunView(StrictModel):
    mode: Mode
    attempt_id: UUID
    session_id: UUID | None
    suite_id: SuiteId
    test_id: TestId
    run_until: AwareDatetime
    submit_until: AwareDatetime

    @model_validator(mode="after")
    def check_identity(self) -> Self:
        if (self.mode == "formal") != (self.session_id is not None):
            raise ValueError("run mode and session differ")
        if self.submit_until <= self.run_until:
            raise ValueError("invalid run deadlines")
        return self


class AssessmentContextView(StrictModel):
    server_now: AwareDatetime
    can_take: StrictBool
    can_read_self: StrictBool
    can_read_reports: StrictBool
    available_suites: Annotated[tuple[SuiteId, ...], Field(max_length=4)]
    own_in_progress_sessions: Annotated[tuple[SessionView, ...], Field(max_length=4)]
    active_run: ActiveRunView | None

    @model_validator(mode="after")
    def check_context(self) -> Self:
        sessions = self.own_in_progress_sessions
        if len(set(self.available_suites)) != len(self.available_suites):
            raise ValueError("duplicate available suite")
        if any(s.status != "in_progress" for s in sessions) or len(
            {s.suite_id for s in sessions}
        ) != len(sessions):
            raise ValueError("invalid ongoing session set")
        if self.active_run:
            run = self.active_run
            if not is_supported_suite_test(run.suite_id, run.test_id):
                raise ValueError("active run test does not belong to suite")
            if run.mode == "formal" and not any(
                s.id == run.session_id and s.suite_id == run.suite_id and run.test_id in s.members
                for s in sessions
            ):
                raise ValueError("active formal run is not an ongoing session")
        if not self.can_read_self and (sessions or self.active_run):
            raise ValueError("self data without self read")
        return self


class SuiteCatalogView(StrictModel):
    items: Annotated[tuple[SuiteConfig, ...], Field(max_length=4)]

    @model_validator(mode="after")
    def check_suites(self) -> Self:
        if len({s.suite_id for s in self.items}) != len(self.items):
            raise ValueError("duplicate suite")
        return self


class RecoveryItem(ActiveRunView):
    state: Literal["running", "awaiting_submission"]
    issued_at: AwareDatetime

    @model_validator(mode="after")
    def check_recovery(self) -> Self:
        if self.run_until <= self.issued_at or not is_supported_suite_test(
            self.suite_id, self.test_id
        ):
            raise ValueError("invalid recovery identity or deadlines")
        return self


class AttemptDetailView(StrictModel):
    attempt: AttemptView
    saved: ResultSaveView | None

    @model_validator(mode="after")
    def check_saved(self) -> Self:
        if (self.attempt.state == "submitted") != (self.saved is not None):
            raise ValueError("submitted attempt requires saved result")
        if self.saved:
            if self.saved.mode != self.attempt.mode:
                raise ValueError("saved mode differs from attempt")
            if self.saved.mode == "formal":
                session = self.saved.session
                if (
                    session.id != self.attempt.session_id
                    or session.suite_id != self.attempt.suite_id
                    or not any(
                        r.result_id == self.saved.result_id and r.test_id == self.attempt.test_id
                        for r in session.results
                    )
                ):
                    raise ValueError("saved formal result differs from attempt")
        return self


class FormalHistoryItem(StrictModel):
    mode: Literal["formal"] = "formal"
    session: SessionView
    created_at: AwareDatetime
    completed_at: AwareDatetime | None

    @model_validator(mode="after")
    def check_completion(self) -> Self:
        if (self.session.status == "completed") != (self.completed_at is not None):
            raise ValueError("completion timestamp differs from session")
        return self


class PracticeHistoryItem(StrictModel):
    mode: Literal["practice"] = "practice"
    attempt_id: UUID
    session_id: None = None
    result_id: UUID
    suite_id: SuiteId
    test_id: TestId
    completed_at: AwareDatetime
    config: TaskConfig
    settings: ControlSettings
    environment: EnvironmentSnapshot
    scored: PracticeResult
    report_id: None = None

    @model_validator(mode="after")
    def check_practice(self) -> Self:
        if self.config.suite_id != self.suite_id or self.config.test_id != self.test_id:
            raise ValueError("practice identity mismatch")
        validate_settings(self.config, self.settings, self.environment)
        return self


HistoryItem = Annotated[FormalHistoryItem | PracticeHistoryItem, Field(discriminator="mode")]


class AssessmentReportSummary(StrictModel):
    id: UUID
    session_id: UUID
    suite_id: SuiteId
    person: PersonDisplaySnapshot
    completed_at: AwareDatetime


class ReportFilters(StrictModel):
    suite_id: SuiteId | None = None
    person_id: UUID | None = None
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def strict_date(cls, value: object) -> date | None:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("expected ISO YYYY-MM-DD date")
        return date.fromisoformat(value)

    @model_validator(mode="after")
    def ordered_dates(self) -> Self:
        if self.date_from == date.min:
            raise ValueError("date_from precedes the supported China timezone range")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date range is reversed")
        if self.date_to == date.max:
            raise ValueError("date_to cannot exceed the supported inclusive range")
        return self
