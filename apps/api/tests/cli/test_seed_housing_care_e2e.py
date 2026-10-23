from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest


def seed():
    try:
        return importlib.import_module("app.cli.seed_housing_care_e2e")
    except ModuleNotFoundError:
        pytest.fail("isolated housing-care seed is not implemented")


@pytest.mark.parametrize(
    "env,url",
    [
        (
            "development",
            "postgresql+psycopg://erp_e2e:erp-care-local-only@127.0.0.1:32786/erp_housing_care_test",
        ),
        ("test", "postgresql+psycopg://erp:secret@127.0.0.1:55433/erp_test"),
        (
            "test",
            "postgresql+psycopg://erp_e2e:erp-care-local-only@nas:32786/erp_housing_care_test",
        ),
        ("test", "postgresql+psycopg://erp_e2e:erp-care-local-only@127.0.0.1:32786/production"),
    ],
)
def test_seed_refuses_unknown_database_before_any_session_access(env, url):
    module = seed()
    with pytest.raises(module.E2ESeedRefused):
        module.seed_housing_care(None, SimpleNamespace(app_env=env, database_url=url))


def test_care_fixture_roles_are_actual_roles_not_permission_only_impersonation():
    module = seed()
    assert set(module.CARE_ACCOUNT_ROLES.values()) == {
        "admin",
        "administration",
        "super_admin",
        "coach",
    }
    assert module.CARE_SCENARIOS == {
        "single": "4-201",
        "double": "4-202",
        "target": "4-203",
        "maintenance": "4-204",
        "pending_verification": "4-205",
        "long_term": "4-206",
    }


def test_seed_accepts_explicit_exact_owned_ledger_and_rejects_mismatch(tmp_path, monkeypatch):
    module = seed()
    ledger = tmp_path / "owned-runtime.json"
    url = "postgresql+psycopg://erp_e2e:synthetic@127.0.0.1:32786/erp_housing_care_test"
    ledger.write_text(
        json.dumps(
            {
                "test_database_url": url,
                "task_label": "housing-care-20261023",
                "container_id": "a" * 64,
                "volume": "b" * 64,
            }
        )
    )
    monkeypatch.setenv("E2E_HOUSING_CARE_RUNTIME_LEDGER", str(ledger))
    settings = SimpleNamespace(app_env="test", database_url=url)
    module.require_care_database(settings)
    settings.database_url = url.replace("32786", "55433")
    with pytest.raises(module.E2ESeedRefused):
        module.require_care_database(settings)


def test_care_launcher_refuses_unknown_local_database_before_migration(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        ["bash", str(root / "scripts/run-housing-care-e2e.sh")],
        env={
            **os.environ,
            "E2E_PYTHON_BIN": sys.executable,
            "TEST_DATABASE_URL": "postgresql+psycopg://erp:secret@127.0.0.1:55433/housing_test",
            "E2E_HOUSING_CARE_RUNTIME_LEDGER": str(tmp_path / "missing.json"),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode != 0
    assert "exact task-owned local database" in result.stderr
    assert "alembic" not in result.stdout
