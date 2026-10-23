from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CHECK = REPOSITORY_ROOT / "scripts/check.sh"
CORE_LAUNCHER = REPOSITORY_ROOT / "scripts/run-core-e2e.sh"
ESPORTS_LAUNCHER = REPOSITORY_ROOT / "scripts/run-esports-assessments-e2e.sh"

ESPORTS_MATRIX = [
    "esports-pc.spec.ts|esports-desktop-1440|pc",
    "esports-touch.spec.ts|esports-touch-844x390|touch",
    "esports-recovery.spec.ts|esports-desktop-1440|recovery",
    (
        "esports-capabilities.spec.ts|esports-desktop-1440|"
        "capabilities-desktop"
    ),
    (
        "esports-capabilities.spec.ts|esports-touch-844x390|"
        "capabilities-touch"
    ),
    (
        "esports-capabilities.spec.ts|esports-webkit-capabilities|"
        "capabilities-webkit"
    ),
]

SPEC_OWNERS = {
    "scripts/run-core-e2e.sh": {
        "platform-foundation.spec.ts",
        "teaching-configuration.spec.ts",
        "admissions-enrollment.spec.ts",
        "enrollment-catalog-responsive.spec.ts",
        "coach-family-portal.spec.ts",
        "system-evaluation-responsive.spec.ts",
        "talent-dynamic-vision.spec.ts",
        "talent-tests.spec.ts",
        "talent-touch.spec.ts",
        "esports-pc.spec.ts",
        "esports-touch.spec.ts",
        "esports-recovery.spec.ts",
        "esports-capabilities.spec.ts",
    },
    "scripts/run-housing-room-status-e2e.sh": {
        "housing-room-status-foundation.spec.ts",
    },
    "scripts/run-housing-occupancy-e2e.sh": {
        "housing-occupancy-lifecycle.spec.ts",
        "housing-occupancy-revocation.spec.ts",
    },
    "scripts/run-housing-personal-portal-e2e.sh": {
        "housing-personal-portal.spec.ts",
    },
    "scripts/run-housing-reporting-e2e.sh": {
        "housing-reporting-lifecycle.spec.ts",
        "housing-reporting-authorization.spec.ts",
    },
    "scripts/run-self-service-registration-e2e.sh": {
        "self-service-registration.spec.ts",
        "self-service-registration-privacy.spec.ts",
    },
    "scripts/run-evaluation-page-e2e.sh": {
        "evaluation-page-integration.spec.ts",
        "evaluation-data-media-foundation.spec.ts",
    },
    "scripts/run-stage2-local-acceptance.sh": {
        "system-evaluation-processing.spec.ts",
        "system-evaluation-privacy.spec.ts",
        "teaching-reports.spec.ts",
        "camp-evaluation-surveys.spec.ts",
        "stage2-local-acceptance.spec.ts",
    },
    "scripts/run-team-management-e2e.sh": {
        "team-management.spec.ts",
    },
}


def _text(path: str | Path) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _shell_array(source: str, name: str) -> list[str]:
    match = re.search(
        rf"{re.escape(name)}=\(\n?(?P<body>.*?)\n?\)",
        source,
        re.DOTALL,
    )
    assert match is not None, f"missing shell array: {name}"
    return shlex.split(match.group("body"))


def test_core_launcher_is_serial_and_reseeds_every_viewport_spec_pair() -> None:
    assert CORE_LAUNCHER.is_file()
    assert stat.S_IMODE(CORE_LAUNCHER.stat().st_mode) == 0o755
    syntax = subprocess.run(
        ["bash", "-n", str(CORE_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    assert ': "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"' in source
    assert "-m app.cli.e2e_database_safety" in source
    assert source.count("-m app.cli.e2e_port_safety") == 1
    assert _shell_array(source, "projects") == [
        "desktop-1440",
        "desktop-1024",
        "tablet-768",
        "mobile-360",
    ]
    assert set(_shell_array(source, "specs")) == {
        "platform-foundation.spec.ts",
        "teaching-configuration.spec.ts",
        "admissions-enrollment.spec.ts",
        "enrollment-catalog-responsive.spec.ts",
        "coach-family-portal.spec.ts",
        "system-evaluation-responsive.spec.ts",
    }
    project_loop = source.index('for project in "${projects[@]}"; do')
    spec_loop = source.index('for spec in "${specs[@]}"; do', project_loop)
    seed = source.index("run_erp_cli -m app.cli.seed_e2e", spec_loop)
    playwright = source.index('run_playwright "$spec" "$project"', seed)
    assert project_loop < spec_loop < seed < playwright
    assert "docker" not in source.casefold()
    assert "-u DATABASE_URL" in source
    assert "PLAYWRIGHT_OUTPUT_DIR" in source


def test_talent_launcher_uses_structured_sanitized_results_and_never_tails_raw_logs() -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")

    assert "PLAYWRIGHT_JSON_OUTPUT_NAME" in source
    assert "--reporter=json" in source
    assert "-m app.cli.summarize_talent_playwright" in source
    assert 'talent_log_suppression=1' in source
    assert 'if (( talent_log_suppression == 1 )); then' in source
    assert 'tail -80 "$log"' in source
    cleanup_start = source.index('if (( talent_log_suppression == 1 )); then')
    cleanup_branch = source[
        cleanup_start : source.index("    else", cleanup_start)
    ]
    assert "tail" not in cleanup_branch
    assert 'log_file="$tmp_dir/playwright-${project}-${spec%.spec.ts}.raw"' in source
    assert '"passed":1,"skipped":0,"failed":0' not in source


def test_esports_launcher_uses_the_exact_seeded_matrix_and_private_summary() -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")

    assert _shell_array(source, "esports_matrix") == ESPORTS_MATRIX
    assert "run_esports_specs" in source
    assert "-m app.cli.seed_esports_assessments_e2e" in source
    assert "ESPORTS_E2E_VERIFY_MODE" in source
    assert "PLAYWRIGHT_JSON_OUTPUT_NAME" in source
    assert "--reporter=json" in source
    assert "-m app.cli.summarize_esports_assessments_e2e" in source
    assert "ESPORTS_E2E_ARTIFACT_DIR" in source
    assert "esports-sensitive-browser-failure" in source
    assert 'if [[ "$spec" == esports-*.spec.ts ]]; then' in source


def test_complete_gate_invokes_the_esports_wrapper_once() -> None:
    check_source = CHECK.read_text(encoding="utf-8")

    assert ESPORTS_LAUNCHER.is_file()
    assert check_source.count("bash scripts/run-esports-assessments-e2e.sh") == 1


def test_complete_gate_passes_the_api_strict_config_to_mypy(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    tool_bin = repository / ".venv" / "bin"
    fake_bin = tmp_path / "fake-bin"
    scripts.mkdir(parents=True)
    tool_bin.mkdir(parents=True)
    fake_bin.mkdir()
    shutil.copy2(CHECK, scripts / "check.sh")

    launcher_names = set(
        re.findall(
            r"bash (?:-n )?(scripts/[a-z0-9-]+\.sh)",
            CHECK.read_text(encoding="utf-8"),
        )
    )
    for relative_path in launcher_names:
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    log = tmp_path / "fake-process.log"
    generic = "#!/bin/sh\nprintf '%s\\n' \"$(basename \"$0\") $*\" >>\"$FAKE_PROCESS_LOG\"\nexit 0\n"
    strict_mypy = """#!/bin/sh
printf '%s\n' "$(basename "$0") $*" >>"$FAKE_PROCESS_LOG"
if [ "$1" != "--config-file" ] || [ "$2" != "apps/api/pyproject.toml" ]; then
  exit 97
fi
exit 0
"""
    for name in ("python", "ruff"):
        path = tool_bin / name
        path.write_text(generic, encoding="utf-8")
        path.chmod(0o700)
    mypy = tool_bin / "mypy"
    mypy.write_text(strict_mypy, encoding="utf-8")
    mypy.chmod(0o700)
    for name in ("docker", "npm", "curl"):
        path = fake_bin / name
        path.write_text(generic, encoding="utf-8")
        path.chmod(0o700)

    result = subprocess.run(
        ["bash", str(scripts / "check.sh")],
        cwd=repository,
        env={
            "FAKE_PROCESS_LOG": str(log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mypy --config-file apps/api/pyproject.toml apps/api/app" in log.read_text(
        encoding="utf-8"
    )


def test_complete_gate_owns_every_playwright_spec_exactly_once() -> None:
    check_source = CHECK.read_text(encoding="utf-8")
    actual_specs = {path.name for path in (REPOSITORY_ROOT / "apps/web/tests").glob("*.spec.ts")}
    owned_specs: list[str] = []

    for launcher, specs in SPEC_OWNERS.items():
        launcher_source = _text(launcher)
        assert check_source.count(f"bash {launcher}") == 1
        for spec in specs:
            assert spec in launcher_source
            owned_specs.append(spec)

    assert set(owned_specs) == actual_specs
    assert len(owned_specs) == len(set(owned_specs))
    assert "npm --prefix apps/web run test:e2e" not in check_source
    assert check_source.index('stop proxy web api scheduler') < check_source.index(
        "bash scripts/run-core-e2e.sh"
    )
    assert 'export E2E_PYTHON_BIN="$python_cmd"' in check_source
    assert check_source.index('export E2E_PYTHON_BIN="$python_cmd"') < (
        check_source.index("bash scripts/run-core-e2e.sh")
    )
    for required_secret in (
        "REGISTRATION_VERIFICATION_PEPPER",
        "REGISTRATION_SESSION_TOKEN_PEPPER",
        "EVALUATION_TOOL_WEB_SERVICE_TOKEN",
        "EVALUATION_TOOL_WORKER_SERVICE_TOKEN",
    ):
        assert f"export {required_secret}=" in check_source
        assert check_source.index('"$python_cmd" -m pytest apps/api/tests -q') < (
            check_source.index(f"export {required_secret}=")
        )


def test_multi_spec_launchers_reseed_inside_the_spec_loop() -> None:
    launchers = (
        "scripts/run-self-service-registration-e2e.sh",
        "scripts/run-evaluation-page-e2e.sh",
    )
    for launcher in launchers:
        source = _text(launcher)
        project_loop = source.index('for project in "${projects[@]}"; do')
        spec_loop = source.index('for spec in "${specs[@]}"; do', project_loop)
        seed = source.index("-m app.cli.seed_e2e", spec_loop)
        playwright = source.index("$spec", seed)
        assert project_loop < spec_loop < seed < playwright
