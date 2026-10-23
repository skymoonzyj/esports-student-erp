from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path
from uuid import UUID

import pytest

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
        "housing-filter-focus.spec.ts",
        "cloud-authorization.spec.ts",
        "talent-dynamic-vision.spec.ts",
        "talent-tests.spec.ts",
        "talent-touch.spec.ts",
        "esports-pc.spec.ts",
        "esports-touch.spec.ts",
        "esports-recovery.spec.ts",
        "esports-capabilities.spec.ts",
        "invited-housing.spec.ts",
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
    "scripts/run-assisted-personnel-e2e.sh": {
        "assisted-personnel.spec.ts",
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
        "housing-filter-focus.spec.ts",
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


def test_cloud_auth_mock_matrix_uses_only_supported_profiles_after_core() -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    projects = _shell_array(source, "cloud_authorization_projects")
    assert projects == ["desktop-1440", "mobile-360", "touch-mobile-390"]
    spec_source = _text("apps/web/tests/cloud-authorization.spec.ts")
    supported = re.search(r"test\.skip\(!(?P<projects>\[.*?\])\.includes", spec_source)
    assert supported is not None
    assert json.loads(supported.group("projects")) == projects
    match = re.search(r"\nrun_cloud_authorization_specs\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert match is not None
    function = match.group()
    assert "seed" not in function
    assert 'run_playwright cloud-authorization.spec.ts "$project"' in function
    result = subprocess.run(
        [
            "bash",
            "-eu",
            "-c",
            (
                'run_playwright() { printf \'%s|%s\\n\' "$1" "$2"; }\n'
                f"cloud_authorization_projects=({shlex.join(projects)})\n"
                f"{function}\nrun_cloud_authorization_specs\n"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"cloud-authorization.spec.ts|{project}" for project in projects
    ]
    dispatch = source.rsplit('\ncase "$e2e_mode" in', 1)[1]
    expected = {
        "core": ["run_core_matrix", "run_cloud_authorization_specs", "run_invited_housing_specs"],
        "all": [
            "run_core_matrix",
            "run_cloud_authorization_specs",
            "run_invited_housing_specs",
            "run_talent_specs",
            "run_esports_specs",
        ],
        "talent": ["run_talent_specs"],
        "esports": ["run_esports_specs"],
    }
    for mode, calls in expected.items():
        branch = re.search(rf"\n  {mode}\)\n(?P<body>.*?)    ;;", dispatch, re.DOTALL)
        assert branch is not None
        assert branch.group("body").split() == calls


@pytest.mark.parametrize("mode", [None, "core", "all", "invited-housing"])
def test_actual_dispatch_executes_serial_invitation_matrix_once(
    tmp_path: Path, mode: str | None
) -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    function = re.search(r"\nrun_invited_housing_specs\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert function is not None
    mode_assignment = re.search(r"^e2e_mode=.*$", source, re.MULTILINE)
    assert mode_assignment is not None
    dispatch = 'case "$e2e_mode" in' + source.rsplit('\ncase "$e2e_mode" in', 1)[1]
    (tmp_path / "apps/api").mkdir(parents=True)
    harness = (
        f"repo_root={shlex.quote(str(tmp_path))}\n"
        "run_core_matrix() { printf 'core\\n'; }\n"
        "run_cloud_authorization_specs() { printf 'cloud\\n'; }\n"
        "run_talent_specs() { printf 'talent\\n'; }\n"
        "run_esports_specs() { printf 'esports\\n'; }\n"
        "run_erp_cli() { printf 'cli:%s\\n' \"$*\"; }\n"
        "run_playwright() { printf 'playwright:%s|%s\\n' \"$1\" \"$2\"; }\n"
        f"{function.group()}\n{mode_assignment.group()}\n{dispatch}\n"
    )
    env = {"PATH": os.environ["PATH"]}
    if mode is not None:
        env["E2E_CORE_MODE"] = mode
    result = subprocess.run(
        ["bash", "-eu", "-c", harness], env=env, capture_output=True,
        text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    invitations = [
        line
        for project in ("desktop-1440", "mobile-360")
        for line in (
            "cli:-m app.cli.seed_e2e",
            "cli:-m app.cli.seed_housing_portal_e2e",
            "cli:-m app.cli.seed_invited_housing_e2e",
            f"playwright:invited-housing.spec.ts|{project}",
        )
    ]
    expected = invitations if mode == "invited-housing" else ["core", "cloud", *invitations]
    if mode == "all":
        expected.extend(["talent", "esports"])
    assert result.stdout.splitlines() == expected


def test_invitation_playwright_executes_entire_spec_with_dedicated_artifacts(tmp_path: Path) -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    function = re.search(r"\nrun_playwright\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert function is not None
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    invocation = tmp_path / "npm.log"
    npm = fake_bin / "npm"
    npm.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" > {shlex.quote(str(invocation))}\n"
        f"printf '%s\\n' \"$INVITED_HOUSING_REAL_E2E|$INVITED_HOUSING_E2E_ARTIFACT_DIR|$PLAYWRIGHT_OUTPUT_DIR|$PLAYWRIGHT_HTML_REPORT_DIR\" >> {shlex.quote(str(invocation))}\n",
        encoding="utf-8",
    )
    npm.chmod(0o700)
    artifact_dir = tmp_path / "invited-artifacts"
    harness = (
        f"tmp_dir={shlex.quote(str(tmp_path))}\n"
        f"repo_root={shlex.quote(str(tmp_path))}\n"
        f"invited_artifact_dir={shlex.quote(str(artifact_dir))}\n"
        "talent_artifact_dir=old-talent\nesports_artifact_dir=old-esports\n"
        "api_origin=http://127.0.0.1:8014\nweb_origin=http://127.0.0.1:5188\n"
        "python_bin=/usr/bin/false\nredact_log() { cat; }\n"
        f"{function.group()}\nrun_playwright invited-housing.spec.ts desktop-1440\n"
    )
    result = subprocess.run(
        ["bash", "-eu", "-c", harness],
        env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert invocation.read_text(encoding="utf-8").splitlines() == [
        "--prefix apps/web run test:e2e -- invited-housing.spec.ts --project=desktop-1440",
        f"1|{artifact_dir}|{artifact_dir}/desktop-1440-results|{artifact_dir}/desktop-1440-report",
    ]


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
    generic = "#!/bin/sh\nprintf '%s\\n' \"$(basename \"$0\") $*\" >>\"$FAKE_PROCESS_LOG\"\nif [ \"$1\" = context ] && [ \"${2:-}\" = inspect ]; then printf '%s\\n' unix:///fake.sock; fi\nexit 0\n"
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


def _private_store_gate_trace(
    tmp_path: Path, *, reject_command: str = "", environment=None, assisted_status=0,
):
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    tool_bin = repository / ".venv/bin"
    fake_bin = tmp_path / "fake-bin"
    scripts.mkdir(parents=True)
    tool_bin.mkdir(parents=True)
    fake_bin.mkdir()
    shutil.copy2(CHECK, scripts / "check.sh")
    log = tmp_path / "calls"
    launchers = set(re.findall(r"bash (?:-n )?(scripts/[a-z0-9-]+\.sh)", _text(CHECK)))
    launchers.add("scripts/run-assisted-personnel-e2e.sh")
    for relative in launchers:
        command = (
            "#!/bin/sh\n"
            f"printf '%s\\n' \"launcher {relative} $*\" >> {shlex.quote(str(log))}\n"
        )
        if relative == "scripts/run-assisted-personnel-e2e.sh":
            command += f"/usr/bin/env > {shlex.quote(str(tmp_path / 'assisted-env'))}\nexit {assisted_status}\n"
        else:
            command += "exit 0\n"
        (repository / relative).write_text(command)
    executable = """#!/bin/sh
printf '%s\n' "$(basename "$0") $*" >> "$FAKE_PROCESS_LOG"
if [ "$1" = context ] && [ "${2:-}" = inspect ]; then
  printf '%s\n' "${FAKE_DOCKER_ENDPOINT-unix:///fake.sock}"
fi
if [ -n "${FAKE_COMPOSE_PORT_LOG:-}" ] && [ "$(basename "$0")" = docker ] && [ "$1" = compose ]; then
  effective_port=${E2E_DB_PORT-}
  if [ -z "$effective_port" ] && [ -n "${COMPOSE_ENV_FILES:-}" ]; then
    while IFS='=' read -r key value; do
      if [ "$key" = E2E_DB_PORT ]; then effective_port=$value; fi
    done < "$COMPOSE_ENV_FILES"
  fi
  printf '%s|%s|%s\n' "${E2E_DB_PORT-<unset>}" "$effective_port" "$*" >> "$FAKE_COMPOSE_PORT_LOG"
fi
if [ -n "$REJECT_COMMAND" ]; then
  case "$*" in *"$REJECT_COMMAND"*) exit 86 ;; esac
fi
exit 0
"""
    for directory, names in (
        (tool_bin, ("python", "ruff", "mypy")),
        (fake_bin, ("docker", "npm", "curl")),
    ):
        for name in names:
            command = directory / name
            command.write_text(executable)
            command.chmod(0o700)
    result = subprocess.run(
        ["bash", str(scripts / "check.sh")],
        cwd=repository,
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "FAKE_PROCESS_LOG": str(log),
            "REJECT_COMMAND": reject_command,
            **(environment or {}),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log.read_text().splitlines() if log.exists() else []


def test_complete_gate_runs_assisted_once_with_owned_db_and_clean_fixed_inputs(tmp_path):
    browsers = tmp_path / "existing-browser-cache"
    browsers.mkdir()
    executable = tmp_path / "existing-browser"
    executable.write_text("synthetic executable presence only")
    executable.chmod(0o700)
    result, calls = _private_store_gate_trace(tmp_path, environment={
        "HOME": str(tmp_path), "APP_ENV": "production",
        "TEST_DATABASE_URL": "postgresql://production:private@remote/erp",
        "DATABASE_URL": "production-secret", "ASSISTED_E2E_DISPOSABLE_DATABASE": "production",
        "E2E_DB_PORT": "56541", "E2E_API_PORT": "5432", "E2E_WEB_PORT": "5432",
        "E2E_PYTHON_BIN": "/untrusted/python", "FIELD_LOOKUP_KEY": "production-secret",
        "SMTP_HOST": "smtp.production.invalid", "NODE_OPTIONS": "production-secret",
        "VITE_SECRET": "production-secret", "HTTPS_PROXY": "production-secret",
        "PLAYWRIGHT_BROWSERS_PATH": str(browsers), "PLAYWRIGHT_EXECUTABLE_PATH": str(executable),
    })
    assert result.returncode == 0, result.stderr
    invocation = "launcher scripts/run-assisted-personnel-e2e.sh --suite assisted"
    assert calls.count(invocation) == 1
    env = dict(line.split("=", 1) for line in (tmp_path / "assisted-env").read_text().splitlines())
    assert env["APP_ENV"] == "test"
    assert env["TEST_DATABASE_URL"] == "postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:56541/erp_e2e"
    assert env["ASSISTED_E2E_DISPOSABLE_DATABASE"] == "erp_e2e"
    assert env["E2E_PYTHON_BIN"] == str(tmp_path / "repository/.venv/bin/python")
    assert env["E2E_API_PORT"] == "18019" and env["E2E_WEB_PORT"] == "15199"
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(browsers)
    assert env["PLAYWRIGHT_EXECUTABLE_PATH"] == str(executable)
    assert set(env) <= {
        "PATH", "HOME", "APP_ENV", "TEST_DATABASE_URL", "ASSISTED_E2E_DISPOSABLE_DATABASE",
        "E2E_PYTHON_BIN", "E2E_API_PORT", "E2E_WEB_PORT", "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_EXECUTABLE_PATH", "PWD", "SHLVL", "_",
    }
    provision = next(i for i, call in enumerate(calls) if call.endswith("up -d --wait db"))
    stop_writers = max(i for i, call in enumerate(calls) if call.endswith("stop proxy web api scheduler"))
    team = calls.index("launcher scripts/run-team-management-e2e.sh ")
    assisted = calls.index(invocation)
    evaluation = calls.index("launcher scripts/run-evaluation-page-e2e.sh ")
    backup = next(i for i, call in enumerate(calls) if call.startswith("launcher scripts/backup-db.sh "))
    assert provision < stop_writers < team < assisted < evaluation < backup


def test_complete_gate_bubbles_assisted_failure_before_later_suites_and_backup(tmp_path):
    result, calls = _private_store_gate_trace(tmp_path, assisted_status=87)
    assert result.returncode == 87, result.stderr
    assert calls.count("launcher scripts/run-assisted-personnel-e2e.sh --suite assisted") == 1
    assert not any(call.startswith(("launcher scripts/run-evaluation-page-e2e.sh ",
                                   "launcher scripts/run-stage2-local-acceptance.sh ",
                                   "launcher scripts/backup-db.sh ",
                                   "launcher scripts/restore-db.sh ")) for call in calls)
    assert calls[-1].endswith("down --remove-orphans --volumes")


@pytest.mark.parametrize(("explicit_port", "expected_port"), [(None, "55433"), ("56543", "56543")])
def test_complete_gate_pins_same_db_port_before_all_compose_calls_despite_external_env(tmp_path, explicit_port, expected_port):
    external_env = tmp_path / "external-ci.env"
    external_env.write_text("E2E_DB_PORT=56541\n")
    port_log = tmp_path / "compose-ports"
    environment = {"COMPOSE_ENV_FILES": str(external_env), "FAKE_COMPOSE_PORT_LOG": str(port_log)}
    if explicit_port is not None:
        environment["E2E_DB_PORT"] = explicit_port
    result, calls = _private_store_gate_trace(tmp_path, environment=environment)
    assert result.returncode == 0, result.stderr
    observed_ports = [line.split("|", 2) for line in port_log.read_text().splitlines()]
    assert observed_ports, "fake Compose never observed the gate's database binding"
    assert all(row[:2] == [expected_port, expected_port] for row in observed_ports)
    assert observed_ports[0][2] == "compose version"
    assert observed_ports[-1][2].endswith("down --remove-orphans --volumes")
    env = dict(line.split("=", 1) for line in (tmp_path / "assisted-env").read_text().splitlines())
    assert env["TEST_DATABASE_URL"] == f"postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:{expected_port}/erp_e2e"
    assert env["ASSISTED_E2E_DISPOSABLE_DATABASE"] == "erp_e2e"
    assert "COMPOSE_ENV_FILES" not in env
    assert calls.count("launcher scripts/run-assisted-personnel-e2e.sh --suite assisted") == 1


@pytest.mark.parametrize("port", [
    "0", "1024", "5432", "65536", "-1", "56541/path", "56541;echo private",
    "$(touch /tmp/never-execute)", " 56541", "１２３４", "9999999999999999999999",
])
def test_complete_gate_invalid_db_port_refuses_before_any_provisioning_or_attestation(tmp_path, port):
    result, calls = _private_store_gate_trace(tmp_path, environment={"E2E_DB_PORT": port})
    assert result.returncode == 3, result.stderr
    assert port not in result.stdout + result.stderr
    assert not calls, "invalid DB port must refuse before Docker/Compose/tests/cleanup"
    assert not (tmp_path / "assisted-env").exists()


@pytest.mark.parametrize("environment", [
    {"FAKE_DOCKER_ENDPOINT": "tcp://remote.invalid:2376"},
    {"FAKE_DOCKER_ENDPOINT": ""},
    {"DOCKER_HOST": "ssh://remote.invalid"},
    {"DOCKER_CONTEXT": "remote", "DOCKER_HOST": "unix:///ignored.sock", "FAKE_DOCKER_ENDPOINT": "ssh://remote.invalid"},
    {"DOCKER_CONTEXT": "--host=ssh://remote.invalid"},
])
def test_complete_gate_refuses_nonlocal_effective_docker_before_provisioning(tmp_path, environment):
    result, calls = _private_store_gate_trace(tmp_path, environment=environment)
    assert result.returncode == 3, result.stderr
    assert not any(" up " in call or " stop " in call or " down " in call for call in calls)
    assert not any(call.startswith(("python ", "launcher ")) for call in calls)
    assert not (tmp_path / "assisted-env").exists()


@pytest.mark.parametrize(("environment", "context_call"), [
    ({"DOCKER_CONTEXT": "selected-local", "DOCKER_HOST": "ssh://ignored.invalid"}, "docker context inspect selected-local --format {{.Endpoints.docker.Host}}"),
    ({"DOCKER_HOST": "unix:///effective.sock", "FAKE_DOCKER_ENDPOINT": "ssh://unused.invalid"}, None),
])
def test_complete_gate_resolves_docker_override_precedence_before_attesting(tmp_path, environment, context_call):
    result, calls = _private_store_gate_trace(tmp_path, environment=environment)
    assert result.returncode == 0, result.stderr
    context_calls = [call for call in calls if call.startswith("docker context inspect")]
    assert context_calls == ([context_call] if context_call else [])
    assert calls.count("launcher scripts/run-assisted-personnel-e2e.sh --suite assisted") == 1


def test_gate_initializes_and_verifies_private_store_before_starting_scheduler(tmp_path: Path):
    result, calls = _private_store_gate_trace(tmp_path)
    assert result.returncode == 0, result.stderr
    initialize = next(
        (i for i, call in enumerate(calls) if "--initialize-store-identity" in call), None
    )
    assert initialize is not None, "gate never initializes the private store"
    migrate = next(i for i, call in enumerate(calls) if "alembic upgrade head" in call)
    verify = [i for i, call in enumerate(calls) if "--verify-store-identity" in call]
    start = next(i for i, call in enumerate(calls) if " up " in call and not call.endswith(" db"))
    once = next(i for i, call in enumerate(calls) if "run_teaching_scheduler --once" in call)
    assert migrate < initialize < min(verify) <= max(verify) < start < once
    assert any(" api " in calls[i] for i in verify)
    assert any(" scheduler " in calls[i] for i in verify)
    stop = next(i for i, call in enumerate(calls) if "stop proxy web api scheduler" in call)
    assert stop < migrate
    assert calls[-1].endswith("down --remove-orphans --volumes")


@pytest.mark.parametrize(
    "reject_command", ["--initialize-store-identity", "--verify-store-identity"]
)
def test_gate_refuses_invalid_identity_without_starting_or_wiping_storage(
    tmp_path: Path,
    reject_command: str,
):
    result, calls = _private_store_gate_trace(tmp_path, reject_command=reject_command)
    assert result.returncode == 86
    assert not any(" up " in call and not call.endswith(" db") for call in calls)
    assert not any("run_teaching_scheduler --once" in call for call in calls)
    assert not any("--volumes" in call or "volume rm" in call for call in calls)
    assert calls[-1].endswith("down --remove-orphans")


def test_rendered_e2e_services_share_private_runtime_without_relaxing_privileges():
    compose = (
        [shutil.which("docker-compose")]
        if shutil.which("docker-compose")
        else ["docker", "compose"]
    )
    result = subprocess.run(
        [
            *compose,
            "--profile",
            "manual-scheduler",
            "-p",
            "esports-erp-e2e",
            "-f",
            "compose.yaml",
            "-f",
            "compose.e2e.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    configuration = json.loads(result.stdout)
    mounts = []
    identities = []
    for name in ("api", "scheduler"):
        service = configuration["services"][name]
        assert service["read_only"] is True
        assert service["user"] == "10001:10001"
        runtime = [v for v in service.get("volumes", []) if v["target"] == "/app/runtime"]
        assert len(runtime) == 1, "private roots must be shared, not container-local tmpfs"
        assert runtime[0]["type"] == "volume"
        assert runtime[0].get("read_only", False) is False
        mounts.append(runtime[0]["source"])
        environment = service["environment"]
        assert environment["EVALUATION_MEDIA_ROOT"] == "/app/runtime/evaluation-media"
        assert (
            environment["EVALUATION_MEDIA_QUARANTINE_ROOT"] == "/app/runtime/evaluation-quarantine"
        )
        identities.append(UUID(environment["EVALUATION_MEDIA_STORE_ID"]))
    assert mounts[0] == mounts[1]
    assert identities[0] == identities[1]
