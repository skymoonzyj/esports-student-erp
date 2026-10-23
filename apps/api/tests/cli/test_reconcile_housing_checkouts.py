from __future__ import annotations

import subprocess
import sys
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

import pytest

import app.cli.reconcile_housing_checkouts as checkout_cli

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
API_ROOT = Path(__file__).resolve().parents[2]


class _FakeSession(AbstractContextManager["_FakeSession"]):
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def test_cli_uses_one_session_and_prints_only_changed_count(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sessions: list[_FakeSession] = []
    calls: list[tuple[_FakeSession, datetime]] = []

    def session_factory() -> _FakeSession:
        session = _FakeSession()
        sessions.append(session)
        return session

    class Reconciler:
        def __init__(self, db: _FakeSession) -> None:
            self.db = db

        def run(self, now: datetime) -> int:
            calls.append((self.db, now))
            return 3

    monkeypatch.setattr(
        checkout_cli,
        "_load_runtime",
        lambda: (session_factory, Reconciler),
    )
    monkeypatch.setattr(checkout_cli, "utc_now", lambda: NOW)

    result = checkout_cli.main()

    captured = capsys.readouterr()
    assert result == 0
    assert len(sessions) == 1
    assert calls == [(sessions[0], NOW)]
    assert captured.out == "housing_checkout_pending=3\n"
    assert captured.err == ""


def test_cli_failure_is_nonzero_and_does_not_print_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Reconciler:
        def __init__(self, db: _FakeSession) -> None:
            del db

        def run(self, now: datetime) -> int:
            del now
            raise RuntimeError("postgresql://secret@private/learner-name")

    monkeypatch.setattr(
        checkout_cli,
        "_load_runtime",
        lambda: (_FakeSession, Reconciler),
    )
    monkeypatch.setattr(checkout_cli, "utc_now", lambda: NOW)

    result = checkout_cli.main()

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "housing checkout reconciliation failed: RuntimeError\n"
    assert "postgresql" not in captured.err
    assert "learner-name" not in captured.err


def test_cli_missing_configuration_is_safe_before_runtime_initialization() -> None:
    sentinel_secret = "housing-cli-secret-must-not-leak"

    result = subprocess.run(
        [sys.executable, "-m", "app.cli.reconcile_housing_checkouts"],
        cwd=API_ROOT,
        env={
            "PYTHONPATH": str(API_ROOT),
            "HOUSING_CLI_TEST_SECRET": sentinel_secret,
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "housing checkout reconciliation failed: ValidationError\n"
    )
    assert "Traceback" not in result.stderr
    assert str(API_ROOT) not in result.stderr
    assert "pydantic" not in result.stderr.lower()
    assert "database_url" not in result.stderr
    assert sentinel_secret not in result.stderr
