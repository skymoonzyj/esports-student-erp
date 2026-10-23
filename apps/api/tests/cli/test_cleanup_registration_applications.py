from __future__ import annotations

from contextlib import AbstractContextManager

import pytest

import app.cli.cleanup_registration_applications as cleanup_cli


class _FakeSession(AbstractContextManager["_FakeSession"]):
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def test_cli_uses_one_session_and_prints_only_pruned_count(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sessions: list[_FakeSession] = []
    calls: list[tuple[_FakeSession, int]] = []

    def session_factory() -> _FakeSession:
        session = _FakeSession()
        sessions.append(session)
        return session

    class CleanupService:
        def __init__(self, db: _FakeSession) -> None:
            self.db = db

        def prune(self, *, limit: int) -> int:
            calls.append((self.db, limit))
            return 3

    monkeypatch.setattr(
        cleanup_cli,
        "_load_runtime",
        lambda: (session_factory, CleanupService),
    )

    result = cleanup_cli.main(["--limit", "500"])

    captured = capsys.readouterr()
    assert result == 0
    assert len(sessions) == 1
    assert calls == [(sessions[0], 500)]
    assert captured.out == "3\n"
    assert captured.err == ""


@pytest.mark.parametrize("value", ["invalid", "0", "-1"])
def test_cli_rejects_invalid_limit_before_loading_runtime(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    def fail_if_loaded():
        raise AssertionError("runtime must not load for invalid arguments")

    monkeypatch.setattr(cleanup_cli, "_load_runtime", fail_if_loaded)

    with pytest.raises(SystemExit) as error:
        cleanup_cli.main(["--limit", value])

    assert error.value.code == 2


def test_cli_failure_is_nonzero_and_redacts_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class CleanupService:
        def __init__(self, db: _FakeSession) -> None:
            del db

        def prune(self, *, limit: int) -> int:
            del limit
            raise RuntimeError("postgresql://secret@private/student@example.com")

    monkeypatch.setattr(
        cleanup_cli,
        "_load_runtime",
        lambda: (_FakeSession, CleanupService),
    )

    result = cleanup_cli.main([])

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "registration cleanup failed: RuntimeError\n"
    assert "postgresql" not in captured.err
    assert "student@example.com" not in captured.err
