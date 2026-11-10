"""Lifecycle boundaries are faked; no Docker, database or service is started."""

import json
import os
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest


def browser():
    try:
        return import_module("app.cli.departure_e2e_browser")
    except ModuleNotFoundError:
        pytest.fail("owned departure browser lifecycle is missing")


def test_browser_refuses_before_any_command_or_private_directory(monkeypatch, tmp_path):
    module = browser()
    monkeypatch.delenv("E2E_DEPARTURE_RUNTIME_LEDGER", raising=False)
    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", lambda **kwargs: pytest.fail("unowned scratch"))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: pytest.fail("unowned process"))
    settings = SimpleNamespace(app_env="test", database_url="postgresql://unknown/production")
    with pytest.raises(module.DepartureRuntimeRefused, match="exact task-owned"):
        module.run_browser(settings)
    assert list(tmp_path.iterdir()) == []


def test_child_environment_pins_verified_dsn_and_ignores_ambient_hooks(monkeypatch):
    module = browser()
    monkeypatch.setenv("DATABASE_URL", "postgresql://foreign/production")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://foreign/production")
    monkeypatch.setenv("PYTHONPATH", "/old/release")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--skip-safety")
    monkeypatch.setenv("NODE_OPTIONS", "--require foreign-hook")
    monkeypatch.setenv("FIELD_LOOKUP_KEY", "private-synthetic")
    environment = module.child_environment(SimpleNamespace(database_url="verified-synthetic"))
    assert environment["DATABASE_URL"] == environment["TEST_DATABASE_URL"] == "verified-synthetic"
    assert environment["FIELD_LOOKUP_KEY"] == "private-synthetic"
    assert environment["SESSION_COOKIE_SECURE"] == "false"
    assert not {"PYTHONPATH", "PYTEST_ADDOPTS", "NODE_OPTIONS"} & environment.keys()


def test_process_group_is_pinned_until_group_cleanup_and_logs_are_private(monkeypatch, tmp_path):
    module = browser()
    operations = []

    class Child:
        pid = 54321

        def poll(self):
            pytest.fail("poll would reap the pinned owner")

        def wait(self, **kwargs):
            operations.append("reap")
            return 0

    def popen(command, **kwargs):
        assert kwargs["start_new_session"] is True
        assert Path(kwargs["cwd"]) == tmp_path
        assert os.fstat(kwargs["stdout"].fileno()).st_ino == (tmp_path / "service.log").stat().st_ino
        return Child()

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout=""))
    monkeypatch.setattr(module.os, "killpg", lambda pid, sig: operations.append((pid, sig)))
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)
    owned = module.OwnedProcess(["synthetic-command"], cwd=tmp_path, env={}, log=tmp_path / "service.log")
    assert (tmp_path / "service.log").stat().st_mode & 0o777 == 0o600
    assert owned.close() == 0
    assert operations == [(54321, module.signal.SIGTERM), (54321, module.signal.SIGKILL), "reap"]
    assert owned.close() == 0
    assert len(operations) == 3


def test_browser_runs_only_contracted_projects_and_always_cleans_own_processes(monkeypatch, tmp_path):
    module = browser()
    monkeypatch.setenv("E2E_DEPARTURE_RUNTIME_LEDGER", str(tmp_path / "runtime.json"))
    settings = SimpleNamespace(database_url="verified-synthetic")
    monkeypatch.setattr(module, "require_departure_database", lambda supplied: None)
    monkeypatch.setattr(module, "wait_service", lambda *args: None)
    ports = iter([41231, 41232])
    monkeypatch.setattr(module, "free_port", lambda: next(ports))
    created = []

    class Process:
        def __init__(self, command, *, cwd, env, log):
            self.command = command
            self.log = log
            self.child = SimpleNamespace(pid=54321 + len(created))
            log.write_text(json.dumps({"stats": {"expected": 4, "unexpected": 0, "skipped": 0, "flaky": 0}}))
            self.closed = False
            assert env["DATABASE_URL"] == env["TEST_DATABASE_URL"] == settings.database_url
            assert log.parent.stat().st_mode & 0o777 == 0o700
            created.append(self)

        def wait(self, timeout):
            if "--project=tablet-768" in self.command:
                raise RuntimeError("synthetic browser failure")
            return 0

        def close(self):
            self.closed = True
            return 0

    monkeypatch.setattr(module, "OwnedProcess", Process)
    with pytest.raises(RuntimeError, match="synthetic browser failure"):
        module.run_browser(settings)
    commands = [process.command for process in created]
    assert sum("app.cli.seed_departure_reminders_e2e" in command for command in commands) == 2
    browser_commands = [command for command in commands if "departure-reminders.spec.ts" in command]
    assert [[part for part in command if part.startswith("--project=")] for command in browser_commands] == [
        ["--project=desktop-1440"], ["--project=tablet-768"]
    ]
    assert all(process.closed for process in created)
    assert not list(tmp_path.iterdir())


def test_http_success_cannot_replace_owned_service_bind_identity(monkeypatch, tmp_path):
    module = browser()
    log = tmp_path / "service.log"
    log.write_text("another service has not bound the requested port")
    process = SimpleNamespace(log=log, running=lambda: True)
    instants = iter([0, 0, 46])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(instants))
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)
    monkeypatch.setattr(module, "urlopen", lambda *a, **kw: pytest.fail("unowned HTTP probed"))
    with pytest.raises(RuntimeError, match="did not become ready"):
        module.wait_service(process, "http://127.0.0.1:41231/", "owned bind marker")


@pytest.mark.parametrize("passed,skipped", [(0, 4), (3, 1), (5, 0)])
def test_browser_does_not_accept_skipped_or_wrong_count_as_verified(tmp_path, passed, skipped):
    module = browser()
    log = tmp_path / "report.json"
    log.write_text(json.dumps({"stats": {"expected": passed, "unexpected": 0, "skipped": skipped, "flaky": 0}}))
    assert module._report_browser(log, "desktop-1440") is False


def test_live_owned_group_after_cleanup_is_a_failure(monkeypatch, tmp_path):
    module = browser()
    child = SimpleNamespace(pid=54321, wait=lambda **kw: 0)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: child)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="54321 S\n"))
    monkeypatch.setattr(module.os, "killpg", lambda *args: None)
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)
    owned = module.OwnedProcess(["synthetic"], cwd=tmp_path, env={}, log=tmp_path / "service.log")
    with pytest.raises(RuntimeError, match="owned group still has live members"):
        owned.close()


def test_interrupt_during_spawn_still_registers_and_cleans_owned_child(monkeypatch, tmp_path):
    module = browser()
    monkeypatch.setenv("E2E_DEPARTURE_RUNTIME_LEDGER", str(tmp_path / "runtime.json"))
    monkeypatch.setattr(module, "require_departure_database", lambda settings: None)
    created = []

    class Process:
        def __init__(self, *args, **kwargs):
            self.closed = False
            self.child = SimpleNamespace(pid=54321)
            created.append(self)
            handler = module.signal.getsignal(module.signal.SIGTERM)
            handler(module.signal.SIGTERM, None)

        def wait(self, timeout):
            return 0

        def close(self):
            self.closed = True

    monkeypatch.setattr(module, "OwnedProcess", Process)
    with pytest.raises(RuntimeError, match="interrupted"):
        module.run_browser(SimpleNamespace(database_url="verified"))
    assert created and all(process.closed for process in created)
    assert not list(tmp_path.iterdir())


def test_browser_failure_diagnostic_keeps_cause_but_redacts_credentials(tmp_path, capsys):
    from app.cli.e2e_credentials import E2E_CREDENTIALS

    module = browser()
    log = tmp_path / "report.json"
    password = E2E_CREDENTIALS["admin"].password
    log.write_text(json.dumps({
        "stats": {"expected": 0, "unexpected": 1, "skipped": 3},
        "suites": [{"specs": [{"file": "departure-reminders.spec.ts", "line": 20, "ok": False,
            "tests": [{"results": [{"error": {"message": f"Error: synthetic browser cause {password}\nprivate body"}}]}]}]}],
    }))
    assert module._report_browser(log, "desktop-1440") is False
    output = capsys.readouterr().out
    assert "synthetic browser cause" in output
    assert password not in output and "private body" not in output


@pytest.mark.parametrize("state", ["Z", "S"])
def test_darwin_term_eperm_only_accepts_a_proven_zombie_group(monkeypatch, tmp_path, state):
    module = browser()
    child = SimpleNamespace(pid=54321, wait=lambda **kw: 0)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: child)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=f"54321 {state}\n"))
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)

    def denied(*args):
        raise PermissionError("synthetic EPERM")

    monkeypatch.setattr(module.os, "killpg", denied)
    owned = module.OwnedProcess(["synthetic"], cwd=tmp_path, env={}, log=tmp_path / "service.log")
    if state == "Z":
        assert owned.close() == 0
    else:
        with pytest.raises(PermissionError):
            owned.close()
