from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import app.cli.run_teaching_scheduler as scheduler_cli
from app.core.config import Settings

TEST_NOW = datetime(2026, 9, 1, 8, tzinfo=UTC)


class FakeSession(AbstractContextManager["FakeSession"]):
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def test_once_uses_one_fresh_session_and_reports_count(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sessions: list[FakeSession] = []
    calls: list[tuple[FakeSession, datetime]] = []

    def session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    def reconcile(session: FakeSession, *, now: datetime) -> int:
        calls.append((session, now))
        return 3

    monkeypatch.setattr(scheduler_cli, "SessionLocal", session_factory)
    monkeypatch.setattr(scheduler_cli, "reconcile_due_cohorts", reconcile)
    monkeypatch.setattr(scheduler_cli, "utc_now", lambda: TEST_NOW)
    monkeypatch.setattr(scheduler_cli.logger, "disabled", False)
    monkeypatch.setattr(
        scheduler_cli,
        "get_settings",
        lambda: SimpleNamespace(cohort_scheduler_interval_seconds=30),
    )

    with caplog.at_level(logging.INFO):
        result = scheduler_cli.main(["--once"])

    assert result == 0
    assert len(sessions) == 1
    assert calls == [(sessions[0], TEST_NOW)]
    assert "processed 3 cohort transitions" in caplog.text


def test_once_logs_only_error_type_and_returns_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(scheduler_cli, "SessionLocal", FakeSession)
    monkeypatch.setattr(scheduler_cli, "utc_now", lambda: TEST_NOW)
    monkeypatch.setattr(scheduler_cli.logger, "disabled", False)
    monkeypatch.setattr(
        scheduler_cli,
        "get_settings",
        lambda: SimpleNamespace(cohort_scheduler_interval_seconds=30),
    )

    def fail(*args, **kwargs):
        raise RuntimeError("postgresql://secret@private/student-name")

    monkeypatch.setattr(scheduler_cli, "reconcile_due_cohorts", fail)

    with caplog.at_level(logging.ERROR):
        result = scheduler_cli.main(["--once"])

    assert result == 1
    assert "RuntimeError" in caplog.text
    assert "postgresql" not in caplog.text
    assert "student-name" not in caplog.text


def test_cli_rejects_interval_below_runtime_minimum() -> None:
    with pytest.raises(SystemExit) as error:
        scheduler_cli.main(["--once", "--interval", "4"])
    assert error.value.code == 2


def test_settings_validate_scheduler_interval(test_settings: Settings) -> None:
    assert test_settings.cohort_scheduler_interval_seconds == 30
    values = test_settings.model_dump()
    values["cohort_scheduler_interval_seconds"] = 4
    with pytest.raises(ValidationError):
        Settings(**values)
