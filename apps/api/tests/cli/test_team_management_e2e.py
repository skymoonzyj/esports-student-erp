from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = ROOT / "scripts/run-team-management-e2e.sh"


def test_launcher_rejects_non_isolated_database_before_reset() -> None:
    environment = {
        **os.environ,
        "TEST_DATABASE_URL": "postgresql+psycopg://127.0.0.1/erp_production",
        "E2E_PYTHON_BIN": sys.executable,
    }
    result = subprocess.run(
        ["bash", str(LAUNCHER)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "隔离测试库" in result.stderr
    assert "fixtures rebuilt" not in result.stdout


def test_fixture_refuses_non_test_environment_without_database_access() -> None:
    from app.cli.seed_team_management_e2e import require_team_e2e_environment

    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        require_team_e2e_environment("production", "postgresql+psycopg://localhost/erp_e2e")


def test_fixture_refuses_non_isolated_database_without_database_access() -> None:
    from app.cli.seed_team_management_e2e import require_team_e2e_environment

    with pytest.raises(RuntimeError, match="isolated"):
        require_team_e2e_environment("test", "postgresql+psycopg://localhost/erp_production")
