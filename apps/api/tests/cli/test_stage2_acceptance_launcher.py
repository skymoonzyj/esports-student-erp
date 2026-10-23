from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest

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


def test_stage2_private_store_gate_precedes_consumers_and_reaches_api() -> None:
    source = LAUNCHER.read_text()
    assert source.count('EVALUATION_MEDIA_STORE_ID="$media_store_id"') == 2
    initialize = source.index(
        "run_erp_cli -m app.evaluations.media_store --initialize-store-identity"
    )
    verify = source.index("run_erp_cli -m app.evaluations.media_store --verify-store-identity")
    assert initialize < verify < source.index("run_erp_cli -m alembic upgrade head")
    api_environment = source[source.index('cd "$repo_root"\nenv \\\n') : source.index("api_pid=$!")]
    assert 'EVALUATION_MEDIA_STORE_ID="$media_store_id"' in api_environment


@pytest.mark.parametrize("invalid_identity", [False, True])
def test_stage2_real_store_cli_uses_unique_private_identity_and_cleans_up(
    tmp_path: Path,
    invalid_identity: bool,
) -> None:
    source = LAUNCHER.read_text()
    # Execute only the real setup/functions and media CLI gate. No database,
    # port probes, service processes, or browser runner can enter this harness.
    prefix = source[: source.index(': "${TEST_DATABASE_URL:?')]
    setup = source[source.index("tmp_dir=$(mktemp") : source.index("wait_http()")]
    cli = source[
        source.index("run_erp_cli()") : source.index("run_erp_cli -m alembic upgrade head")
    ]
    if invalid_identity:
        cli = cli.replace(
            "run_erp_cli -m app.evaluations.media_store --verify-store-identity",
            "media_store_id=22222222-2222-4222-8222-222222222222\n"
            "run_erp_cli -m app.evaluations.media_store --verify-store-identity",
        )
    inspect = """
run_erp_cli -c 'import json, os, stat; from pathlib import Path; from uuid import UUID; identity=os.environ["EVALUATION_MEDIA_STORE_ID"]; UUID(identity); roots=[Path(os.environ["EVALUATION_MEDIA_ROOT"]),Path(os.environ["EVALUATION_MEDIA_QUARANTINE_ROOT"])]; assert all(stat.S_IMODE(p.stat().st_mode)==0o700 for p in roots); assert all(stat.S_IMODE((p/".erp-store-identity").stat().st_mode)==0o600 for p in roots); assert [(p/".erp-store-identity").read_text() for p in roots]==[identity+"\\nmedia\\n",identity+"\\nquarantine\\n"]; print(json.dumps({"identity":identity,"temporary":str(roots[0].parent)}))'
"""
    # BASH_SOURCE must still resolve to the actual launcher for repo discovery.
    prefix = prefix.replace(
        'repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)',
        f"repo_root={str(REPOSITORY_ROOT)!r}",
    )
    env = {
        **os.environ,
        "E2E_PYTHON_BIN": sys.executable,
        "TMPDIR": str(tmp_path),
        "STAGE2_ACCEPTANCE_SUMMARY_PATH": str(tmp_path / "summary.json"),
        "TEST_DATABASE_URL": "postgresql+psycopg://unused:unused@127.0.0.1:1/unused",
        "FIELD_ENCRYPTION_MASTER_KEY": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        "FIELD_LOOKUP_KEY": "Hy4tLCsqKSgnJiUkIyIhIB8eHRwbGhkYFxYVFBMSERA=",
        "SESSION_TOKEN_PEPPER": "EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8=",
        "EVALUATION_TOOL_SERVICE_TOKEN": "stage2-v1-test-token",
        "EVALUATION_TOOL_WEB_SERVICE_TOKEN": "stage2-web-test-token",
        "EVALUATION_TOOL_WORKER_SERVICE_TOKEN": "stage2-worker-test-token",
        "EVALUATION_MEDIA_STORE_ID": "11111111-1111-4111-8111-111111111111",
    }
    identities = []
    for _ in range(2):
        result = subprocess.run(
            ["bash"],
            input=prefix + setup + cli + inspect,
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
            check=False,
        )
        if invalid_identity:
            assert result.returncode != 0
            assert "private media store identity verification failed" in result.stderr
            summary = json.loads((tmp_path / "summary.json").read_text())
            assert summary["cleanup_completed"] is True and summary["status"] == "failed"
            assert not list(tmp_path.glob("stage2-local-acceptance.*"))
            return
        assert result.returncode == 0, result.stderr
        record = json.loads(
            next(line for line in result.stdout.splitlines() if line.startswith("{"))
        )
        identities.append(UUID(record["identity"]))
        assert record["identity"] != env["EVALUATION_MEDIA_STORE_ID"]
        assert not Path(record["temporary"]).exists()
        summary = json.loads((tmp_path / "summary.json").read_text())
        assert summary["cleanup_completed"] is True and summary["status"] == "passed"
    assert identities[0] != identities[1]
