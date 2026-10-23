from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = REPOSITORY_ROOT / "scripts/run-stage2-local-acceptance.sh"
CROSS_MODULE_SPEC = REPOSITORY_ROOT / "apps/web/tests/stage2-local-acceptance.spec.ts"


def test_stage2_launcher_is_safe_serial_and_runs_every_viewport_and_domain() -> None:
    assert LAUNCHER.is_file()
    assert CROSS_MODULE_SPEC.is_file()
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
    assert "set -euo pipefail" in source
    assert "umask 077" in source
    assert ': "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"' in source
    assert 'python_bin" -m app.cli.e2e_database_safety' in source
    assert source.index("app.cli.e2e_database_safety") < source.index("-m alembic upgrade head")
    for variable in (
        "FIELD_ENCRYPTION_MASTER_KEY",
        "FIELD_LOOKUP_KEY",
        "SESSION_TOKEN_PEPPER",
        "EVALUATION_TOOL_SERVICE_TOKEN",
        "EVALUATION_TOOL_WEB_SERVICE_TOKEN",
        "EVALUATION_TOOL_WORKER_SERVICE_TOKEN",
    ):
        assert f"${{{variable}:?" in source
    assert "v1、Web 与 Worker 服务密钥必须彼此不同" in source

    assert "stage2-local-acceptance.XXXXXX" in source
    assert "trap cleanup EXIT" in source
    assert source.index("trap cleanup EXIT") < source.index('mkdir -m 700 -p "$summary_dir"')
    assert "terminate_children || cleanup_status=1" in source
    assert "cleanup_completed=false" in source
    assert "无法写入 Stage 2 验收摘要" in source
    assert 'write_summary "$state" "$status" "$cleanup_completed"' in source
    assert "NamedTemporaryFile" in source
    assert "os.replace(temporary, path)" in source
    assert "source-tree-digest.py" in source
    assert '"source_tree_sha256"' in source
    assert "projects=(desktop-1440 desktop-1024 tablet-768 mobile-360)" in source
    for spec in (
        "system-evaluation-processing.spec.ts",
        "system-evaluation-privacy.spec.ts",
        "teaching-reports.spec.ts",
        "camp-evaluation-surveys.spec.ts",
        "stage2-local-acceptance.spec.ts",
    ):
        assert spec in source
    assert "-m app.cli.seed_e2e" in source
    assert "seed-stage2-acceptance-e2e.py" in source
    assert "stage2-local-acceptance-summary.json" in source
    assert "os.chmod(temporary, 0o600)" in source
    assert "docker " not in source.lower()
    assert "docker-compose" not in source.lower()
