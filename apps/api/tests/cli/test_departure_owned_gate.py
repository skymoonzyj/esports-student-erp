from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.cli.test_housing_care_owned_gate import (
    CONTAINER,
    IMAGE,
    NETWORK,
    ROOT,
    VOLUME,
    DockerBoundary,
    gate,
)


class DepartureBoundary(DockerBoundary):
    def __init__(self, *, child_status=0, drift=None, cleanup_drift=False):
        super().__init__(child_status=child_status, drift=drift)
        self.cleanup_drift = cleanup_drift
        self.launcher_timeouts = []

    def __call__(self, command, **kwargs):
        if command[:2] == ["bash", str(ROOT / "scripts/run-departure-reminders-checks.sh")] and command[2:] in (["api"], ["browser"], ["all"]):
            self.calls.append(command)
            self.launcher_timeouts.append(kwargs.get("timeout"))
            env = kwargs["env"]
            self.child_environments.append(dict(env))
            ledger = Path(env["E2E_DEPARTURE_RUNTIME_LEDGER"])
            owned = json.loads(ledger.read_text())
            assert ledger.stat().st_mode & 0o777 == 0o600
            assert ledger.parent.stat().st_mode & 0o777 == 0o700
            assert owned["task_label"] == "departure-reminders-20261024"
            assert owned["owner"] == self.owner
            assert owned["container_id"] == CONTAINER
            assert owned["volume"] == VOLUME
            assert owned["test_database_url"] == env["TEST_DATABASE_URL"]
            assert env["DATABASE_URL"] == env["TEST_DATABASE_URL"]
            assert "127.0.0.1:43261/erp_departure_reminders_test" in env["TEST_DATABASE_URL"]
            assert "E2E_HOUSING_CARE_RUNTIME_LEDGER" not in env
            if self.cleanup_drift:
                self.drift = "label"
            return subprocess.CompletedProcess(command, self.child_status)
        return super().__call__(command, **kwargs)


@pytest.mark.parametrize(
    "profile,phase",
    [("unknown", "api"), ("departure-reminders", "unknown")],
)
def test_unknown_profile_or_phase_refuse_before_any_resource(tmp_path, profile, phase):
    boundary = DepartureBoundary()
    result = gate().run_gate(
        ROOT,
        sys.executable,
        IMAGE,
        {},
        profile=profile,
        phase=phase,
        process=boundary,
        launcher=boundary,
        scratch_parent=tmp_path,
    )
    assert result["exit_code"] != 0
    assert not boundary.calls
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("status", [0, 87])
@pytest.mark.parametrize("phase,timeout", [("api", 1800), ("browser", 3600), ("all", 5400)])
def test_departure_phases_use_new_private_database_and_clean_up(tmp_path, status, phase, timeout):
    boundary = DepartureBoundary(child_status=status)
    result = gate().run_gate(
        ROOT,
        sys.executable,
        IMAGE,
        {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "DOCKER_HOST": "unix:///synthetic/local.sock",
            "TEST_DATABASE_URL": "postgresql://prod:secret@remote/erp",
            "E2E_DEPARTURE_RUNTIME_LEDGER": "/foreign/ledger",
        },
        profile="departure-reminders",
        phase=phase,
        process=boundary,
        launcher=boundary,
        scratch_parent=tmp_path,
    )
    assert result["exit_code"] == status
    assert result["owned_containers_removed"] == 1
    assert result["owned_containers_remaining"] == 0
    assert result["private_scratch_removed"] is True
    assert boundary.network_removed and len(boundary.child_environments) == 1
    assert boundary.launcher_timeouts == [timeout]
    create = next(command for command in boundary.calls if command[:2] == ["docker", "create"])
    assert "POSTGRES_DB=erp_departure_reminders_test" in create
    assert "secret" not in json.dumps(result)


def test_departure_cleanup_refuses_changed_owner_after_child(tmp_path):
    boundary = DepartureBoundary(cleanup_drift=True)
    result = gate().run_gate(
        ROOT,
        sys.executable,
        IMAGE,
        {"DOCKER_HOST": "unix:///synthetic/local.sock"},
        profile="departure-reminders",
        phase="api",
        process=boundary,
        launcher=boundary,
        scratch_parent=tmp_path,
    )
    assert result["exit_code"] != 0 and result["cleanup_failed"] is True
    assert len(boundary.child_environments) == 1
    assert not boundary.container_removed and not boundary.network_removed
    assert result["private_scratch_removed"] is False


@pytest.mark.parametrize("endpoint", ["ssh://remote.invalid", "tcp://127.0.0.1:2375"])
def test_departure_remote_engine_refused_before_creation(tmp_path, endpoint):
    boundary = DepartureBoundary()
    result = gate().run_gate(
        ROOT,
        sys.executable,
        IMAGE,
        {"DOCKER_HOST": endpoint},
        profile="departure-reminders",
        phase="api",
        process=boundary,
        launcher=boundary,
        scratch_parent=tmp_path,
    )
    assert result["exit_code"] != 0 and not boundary.calls


def runtime():
    path = ROOT / "apps/api/app/cli/departure_e2e_runtime.py"
    assert path.is_file(), "departure runtime guard is missing"
    spec = importlib.util.spec_from_file_location("departure_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def owned_runtime(tmp_path, monkeypatch):
    scratch = tmp_path / "assisted-restore-0069.departure_gate_fixture"
    scratch.mkdir(mode=0o700)
    owner = "f" * 32
    (scratch / "owner").write_text(owner)
    (scratch / "owner").chmod(0o600)
    ledger = scratch / "runtime.json"
    dsn = "postgresql+psycopg://erp_e2e:private@127.0.0.1:43261/erp_departure_reminders_test"
    owned = {
        "task_label": "departure-reminders-20261024",
        "owner": owner,
        "container_id": CONTAINER,
        "volume": VOLUME,
        "test_database_url": dsn,
        "docker_endpoint": "unix:///synthetic/local.sock",
        "network_id": NETWORK,
        "postgres_image": IMAGE,
    }
    ledger.write_text(json.dumps(owned))
    ledger.chmod(0o600)
    monkeypatch.setenv("E2E_DEPARTURE_RUNTIME_LEDGER", str(ledger))
    monkeypatch.setenv("DOCKER_HOST", "unix:///synthetic/local.sock")
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("TEST_DATABASE_URL", dsn)
    boundary = DepartureBoundary()
    boundary.owner = owner
    boundary.started = True
    return ledger, owned, boundary, SimpleNamespace(app_env="test", database_url=dsn)


def test_runtime_accepts_only_live_exact_owned_identity(monkeypatch, owned_runtime):
    _, _, boundary, settings = owned_runtime
    module = runtime()
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=boundary))
    module.require_departure_database(settings)
    assert any(command[:2] == ["docker", "inspect"] for command in boundary.calls)


@pytest.mark.parametrize(
    "change",
    [
        "dsn",
        "shared_port",
        "external",
        "label",
        "volume",
        "network",
        "task",
        "owner",
        "image",
        "mode",
        "parent_mode",
        "symlink",
        "endpoint",
    ],
)
def test_runtime_rejects_forged_or_mismatched_ledger_and_foreign_dsn(
    monkeypatch, owned_runtime, change
):
    ledger, owned, boundary, settings = owned_runtime
    module = runtime()
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=boundary))
    if change == "dsn":
        settings.database_url = settings.database_url.replace("private", "different")
    elif change in {"shared_port", "external"}:
        settings.database_url = (
            settings.database_url.replace("43261", "55433")
            if change == "shared_port"
            else settings.database_url.replace("127.0.0.1", "remote.invalid")
        )
        owned["test_database_url"] = settings.database_url
    elif change in {"label", "volume", "network"}:
        boundary.drift = change
    elif change == "task":
        owned["task_label"] = "housing-care-20261023"
    elif change == "owner":
        owned["owner"] = "e" * 32
    elif change == "image":
        owned["postgres_image"] = "postgres:16"
    elif change == "endpoint":
        owned["docker_endpoint"] = "ssh://remote.invalid"
    ledger.write_text(json.dumps(owned))
    if change == "mode":
        ledger.chmod(0o644)
    elif change == "parent_mode":
        ledger.parent.chmod(0o755)
    elif change == "symlink":
        alias = ledger.parent / "alias.json"
        alias.symlink_to(ledger)
        monkeypatch.setenv("E2E_DEPARTURE_RUNTIME_LEDGER", str(alias))
    with pytest.raises(module.DepartureRuntimeRefused, match="task-owned local database"):
        module.require_departure_database(settings)


def test_runtime_refusal_prevents_pytest_from_resetting_schema(monkeypatch, owned_runtime):
    _, _, boundary, settings = owned_runtime
    module = runtime()
    settings.database_url = settings.database_url.replace("43261", "55433")
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=boundary))
    monkeypatch.setattr(
        module, "run_api_tests", lambda: pytest.fail("pytest must not run after refusal")
    )
    assert module.main(["--phase", "api"]) == 2


@pytest.mark.parametrize(
    "variable,value",
    [
        ("TEST_DATABASE_URL", "postgresql+psycopg://erp:foreign@remote.invalid:5432/foreign_test"),
        ("TEST_DATABASE_URL", "postgresql+psycopg://erp:foreign@127.0.0.1:55433/shared_test"),
        ("TEST_DATABASE_URL", None),
        ("DATABASE_URL", None),
        ("DATABASE_URL", "postgresql+psycopg://erp:foreign@127.0.0.1:43261/foreign_test"),
    ],
)
def test_runtime_refuses_both_environment_dsn_mismatches_before_child_dispatch(
    monkeypatch, owned_runtime, variable, value
):
    _, _, boundary, settings = owned_runtime
    module = runtime()
    if value is None:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, value)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=boundary))
    monkeypatch.setattr(
        module, "run_api_tests", lambda *args: pytest.fail("refused DSN must not dispatch pytest")
    )
    assert module.main(["--phase", "api"]) == 2
    assert not boundary.calls


def test_child_both_dsns_come_from_validated_value_not_later_ambient_mutation(
    monkeypatch, owned_runtime
):
    _, owned, boundary, settings = owned_runtime
    module = runtime()
    children = []

    def process(command, **kwargs):
        if command[0] == "docker":
            result = boundary(command, **kwargs)
            if command[:3] == ["docker", "network", "inspect"]:
                monkeypatch.setenv("DATABASE_URL", "postgresql://foreign:fake@remote/foreign_test")
                monkeypatch.setenv(
                    "TEST_DATABASE_URL", "postgresql://foreign:fake@remote/other_test"
                )
            return result
        children.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=process))
    assert module.main(["--phase", "api"]) == 7
    assert len(children) == 1
    assert children[0]["DATABASE_URL"] == owned["test_database_url"]
    assert children[0]["TEST_DATABASE_URL"] == owned["test_database_url"]


def test_api_phase_executes_explicit_tests_without_skipping_missing_files(monkeypatch):
    module = runtime()
    invocations = []

    def child(command, **kwargs):
        invocations.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=child))
    monkeypatch.setenv("PYTHONPATH", "/foreign/release")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--unexpected-option")
    monkeypatch.setenv("NODE_OPTIONS", "--untrusted-hook")
    assert (
        module.run_api_tests(
            "postgresql+psycopg://erp_e2e:fake@127.0.0.1:43261/erp_departure_reminders_test"
        )
        == 7
    )
    command, options = invocations[0]
    assert command[:5] == [sys.executable, "-m", "pytest", "--tb=short", "-ra"]
    assert "tests/services/test_departure_access.py" in command
    assert "tests/services/test_managed_people_housing.py" in command
    assert "tests/db/test_migrations.py" in command
    assert options["cwd"] == ROOT / "apps/api"
    assert options["env"]["E2E_DEPARTURE_OWNED_ACCEPTANCE"] == "1"
    assert not {"PYTHONPATH", "PYTEST_ADDOPTS", "NODE_OPTIONS"} & set(options["env"])
    assert "start_new_session" not in options  # Stay in the controller-owned PGID.


def test_all_backend_phase_runs_fixed_full_suite_without_external_selectors(monkeypatch):
    module = runtime()
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=execute))
    monkeypatch.setenv("PYTEST_ADDOPTS", "tests/foreign.py")
    assert module.run_api_tests("verified", full_suite=True) == 0
    assert calls[0][0] == [sys.executable, "-m", "pytest", "--tb=short", "-ra"]
    assert calls[0][1]["env"]["DATABASE_URL"] == calls[0][1]["env"]["TEST_DATABASE_URL"] == "verified"
    assert calls[0][1]["env"]["E2E_DEPARTURE_OWNED_ACCEPTANCE"] == "1"
    assert "PYTEST_ADDOPTS" not in calls[0][1]["env"]


@pytest.mark.parametrize("full_suite", [False, True])
def test_owned_api_child_reads_migration_fixture_cipher_without_changing_browser_keys(
    monkeypatch, full_suite,
):
    from app.cli.departure_e2e_browser import child_environment
    from app.security.encryption import EnvelopeCipher, LookupDigest

    module = runtime()
    parent_key = base64.urlsafe_b64encode(bytes(range(32, 64))).decode("ascii")
    fixture_bytes = bytes(range(32))
    fixture_ciphertext = EnvelopeCipher(fixture_bytes).encrypt("13900000000", field_name="phone")
    fixture_digest = LookupDigest(fixture_bytes).digest_domain("account-login-phone", "13900000000")
    monkeypatch.setenv("FIELD_ENCRYPTION_MASTER_KEY", parent_key)
    monkeypatch.setenv("FIELD_LOOKUP_KEY", parent_key)
    monkeypatch.setenv("SESSION_TOKEN_PEPPER", parent_key)
    seen = []

    def child(command, **kwargs):
        seen.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=child))
    assert module.run_api_tests("verified", full_suite=full_suite) == 0
    assert len(seen) == 1
    pytest_env = seen[0]
    pytest_cipher = EnvelopeCipher(base64.urlsafe_b64decode(pytest_env["FIELD_ENCRYPTION_MASTER_KEY"]))
    pytest_lookup = LookupDigest(base64.urlsafe_b64decode(pytest_env["FIELD_LOOKUP_KEY"]))
    assert pytest_cipher.decrypt(fixture_ciphertext, field_name="phone") == "13900000000"
    assert pytest_lookup.digest_domain("account-login-phone", "13900000000") == fixture_digest
    assert pytest_env["SESSION_TOKEN_PEPPER"] == parent_key
    browser_env = child_environment(SimpleNamespace(database_url="verified"))
    assert browser_env["FIELD_ENCRYPTION_MASTER_KEY"] == parent_key
    assert browser_env["FIELD_LOOKUP_KEY"] == parent_key
    assert os.environ["FIELD_ENCRYPTION_MASTER_KEY"] == parent_key
    assert os.environ["FIELD_LOOKUP_KEY"] == parent_key


@pytest.mark.parametrize("phase,api_status,want,expected", [
    ("browser", 0, 3, ["browser"]),
    ("all", 7, 7, ["full-api"]),
    ("all", 0, 3, ["full-api", "browser"]),
])
def test_browser_dispatch_requires_guard_and_all_stops_on_backend_failure(monkeypatch, phase, api_status, want, expected):
    from app.cli import departure_e2e_browser

    module = runtime()
    settings = SimpleNamespace(database_url="verified")
    calls = []
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "require_departure_database", lambda value: None)

    def backend(dsn, *, full_suite=False):
        assert dsn == "verified"
        calls.append("full-api" if full_suite else "focused-api")
        return api_status

    def browser(value):
        assert value is settings
        calls.append("browser")
        return 3

    monkeypatch.setattr(module, "run_api_tests", backend)
    monkeypatch.setattr(departure_e2e_browser, "run_browser", browser)
    assert module.main(["--phase", phase]) == want
    assert calls == expected
