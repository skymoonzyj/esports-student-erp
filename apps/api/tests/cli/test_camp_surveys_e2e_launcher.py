from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = REPOSITORY_ROOT / "scripts/run-camp-surveys-e2e.sh"


def test_camp_surveys_launcher_is_safe_serial_and_domain_scoped() -> None:
    assert LAUNCHER.is_file()
    assert stat.S_IMODE(LAUNCHER.stat().st_mode) == 0o755
    syntax = subprocess.run(
        ["bash", "-n", str(LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    source = LAUNCHER.read_text(encoding="utf-8")
    assert ': "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"' in source
    assert "-m app.cli.e2e_database_safety" in source
    assert source.count("-m app.cli.e2e_port_safety") == 1
    assert '"$vite_bin" --host 127.0.0.1 --port "$web_port" --strictPort' in source
    assert "projects=(desktop-1440 desktop-1024 tablet-768 mobile-360)" in source
    assert source.count("run_erp_cli -m app.cli.seed_e2e") == 1
    assert source.count("run_erp_cli -m app.cli.seed_camp_surveys_e2e") == 1
    assert "camp-evaluation-surveys.spec.ts --project=\"$project\"" in source
    assert "teaching-reports.spec.ts" not in source
    assert "score-tool" not in source
    assert "worker.py" not in source
