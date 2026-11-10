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
    "scripts/run-housing-care-gate.sh": {"housing-cleaning-photos-export.spec.ts"},
    "scripts/run-departure-reminders-gate.sh": {"departure-reminders.spec.ts"},
    "scripts/run-core-e2e.sh": {
        "reactionx-integration.spec.ts",
        "platform-foundation.spec.ts",
        "teaching-configuration.spec.ts",
        "admissions-enrollment.spec.ts",
        "enrollment-catalog-responsive.spec.ts",
        "coach-family-portal.spec.ts",
        "system-evaluation-responsive.spec.ts",
        "housing-filter-focus.spec.ts",
        "cloud-authorization.spec.ts",
        "operations-request-errors.spec.ts",
        "asset-rentals.spec.ts",
        "asset-batch-hardware.spec.ts",
        "person-detail-chunk.spec.ts",
        "person-profile-assignments.spec.ts",
        "personnel-documents.spec.ts",
        "ui-themes.spec.ts",
        "ui-theme-foundation.spec.ts",
        "talent-dynamic-vision.spec.ts",
        "talent-tests.spec.ts",
        "talent-touch.spec.ts",
        "talent-item-ranking.spec.ts",
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
        "housing-history-void.spec.ts",
        "profile-housing-edit.spec.ts",
        "person-housing.spec.ts",
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
        "profile-claims.spec.ts",
        "pending-profile-lifecycle.spec.ts",
    },
    "scripts/run-intake-activation-roles-e2e.sh": {
        "intake-activation-roles.spec.ts",
    },
    "scripts/run-intake-identity-teams-e2e.sh": {"intake-identity-teams.spec.ts"},
}

# The full-check entry owns a spec through one verified executable delegate,
# not by adding an unrelated comment/filename to its wrapper source.
SPEC_DELEGATES = {
    "scripts/run-housing-care-gate.sh": "scripts/run-housing-care-e2e.sh",
    "scripts/run-departure-reminders-gate.sh": "apps/api/app/cli/departure_e2e_browser.py",
    "scripts/run-intake-activation-roles-e2e.sh": "scripts/run-assisted-personnel-e2e.sh",
    "scripts/run-intake-identity-teams-e2e.sh": "scripts/run-assisted-personnel-e2e.sh",
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


def test_reactionx_lane_restarts_api_and_verifies_persisted_evidence(tmp_path):
    source = CORE_LAUNCHER.read_text()
    lane = re.search(r"\nrun_reactionx_specs\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert lane is not None, "ReactionX acceptance is not dispatched"
    harness = (
        f"repo_root={shlex.quote(str(REPOSITORY_ROOT))}\n"
        "api_origin=http://127.0.0.1:8014\napi_port=8014\napi_pid=42\n"
        f"tmp_dir={shlex.quote(str(tmp_path))}\n"
        "run_erp_cli() { printf 'cli|%s\\n' \"$*\"; }\n"
        "private_reactionx_cli() { run_erp_cli \"$@\"; }\n"
        "stop_api() { printf 'stop\\n'; }\n"
        "start_api() { printf 'api|%s\\n' \"$1\"; }\n"
        "wait_http() { :; }\n"
        "run_playwright() { printf 'browser|%s|%s\\n' \"$1\" \"$2\"; }\n"
        f"{lane.group()}\nrun_reactionx_specs\n"
    )
    result = subprocess.run(["bash", "-eu", "-c", harness], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "cli|-m app.cli.seed_reactionx_e2e",
        "cli|-m app.cli.seed_reactionx_e2e --probe-closed http://127.0.0.1:8014",
        "stop", "api|true",
        "browser|reactionx-integration.spec.ts|reactionx-desktop-1440",
        "cli|-m app.cli.seed_reactionx_e2e --verify", "reactionx-persisted-evidence-passed", "stop", "api|false",
    ]


def test_reactionx_api_opt_in_is_child_local_and_ordinary_cli_drops_ambient_file(tmp_path):
    source = CORE_LAUNCHER.read_text()
    api = re.search(r"\nstart_api\(\) \{\n.*?\n\}", source, re.DOTALL)
    cli = re.search(r"\nrun_erp_cli\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert api is not None and cli is not None
    # A real shell child reports only safe public environment values. It never
    # imports the app, binds ports, reads a secret file or opens a database.
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\nprintf '%s|%s|%s|%s\\n' \"$REACTIONX_SYNTHETIC_FORMAL_ENABLED\" \"${REACTIONX_SYNTHETIC_FORMAL_ENABLED_FILE-unset}\" \"${ERP_WEB_BASE_URL-unset}\" \"$APP_ENV\"\n")
    executable.chmod(0o755)
    harness = (
        f"python_bin={shlex.quote(str(executable))}\n"
        f"repo_root={shlex.quote(str(REPOSITORY_ROOT))}\ntmp_dir={shlex.quote(str(tmp_path))}\n"
        "pids=()\napi_port=8014\nweb_origin=http://127.0.0.1:5188\n"
        "TEST_DATABASE_URL=synthetic FIELD_ENCRYPTION_MASTER_KEY=synthetic FIELD_LOOKUP_KEY=synthetic SESSION_TOKEN_PEPPER=synthetic\n"
        "EVALUATION_TOOL_SERVICE_TOKEN=one EVALUATION_TOOL_WEB_SERVICE_TOKEN=two EVALUATION_TOOL_WORKER_SERVICE_TOKEN=three\n"
        f"{api.group()}\n{cli.group()}\n"
        "start_api true\nwait \"$api_pid\"\ncat \"$tmp_dir/api.log\"\n"
        "start_api false\nwait \"$api_pid\"\ncat \"$tmp_dir/api.log\"\n"
        "run_erp_cli -m no_real_cli\n"
    )
    environment = {**os.environ, "REACTIONX_SYNTHETIC_FORMAL_ENABLED": "true",
                   "REACTIONX_SYNTHETIC_FORMAL_ENABLED_FILE": "/must-not-read"}
    result = subprocess.run(["bash", "-eu", "-c", harness], env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "true|unset|http://127.0.0.1:5188|test",
        "false|unset|http://127.0.0.1:5188|test", "false|unset|unset|test",
    ]


def test_reactionx_cli_failure_does_not_print_sql_credential_or_challenge(tmp_path):
    source = CORE_LAUNCHER.read_text()
    private_cli = re.search(r"\nprivate_reactionx_cli\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert private_cli is not None
    result = subprocess.run(["bash", "-eu", "-c", (
        f"tmp_dir={shlex.quote(str(tmp_path))}\n"
        "run_erp_cli() { printf 'do-not-print-credential-challenge\\n'; return 43; }\n"
        f"{private_cli.group()}\nprivate_reactionx_cli -m app.cli.seed_reactionx_e2e\n"
    )], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "credential-challenge" not in result.stdout + result.stderr
    assert "reactionx-cli-failed" in result.stderr


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


def test_talent_item_ranking_dispatches_once_and_uses_its_persisted_api_verifier() -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    seeded = re.search(r"\nrun_seeded_spec\(\) \{\n.*?\n\}", source, re.DOTALL)
    talent = re.search(r"\nrun_talent_specs\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert seeded is not None and talent is not None
    result = subprocess.run(
        ["bash", "-eu", "-c", (
            f"repo_root={shlex.quote(str(REPOSITORY_ROOT))}\n"
            "run_erp_cli() { printf 'cli|%s|%s\\n' \"${TALENT_E2E_VERIFY_MODE:-seed}\" \"$*\"; }\n"
            "run_playwright() { printf 'browser|%s|%s\\n' \"$1\" \"$2\"; }\n"
            f"{seeded.group()}\n"
            "run_seeded_spec talent-item-ranking.spec.ts desktop-1440 app.cli.seed_talent_e2e\n"
        )], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "cli|seed|-m app.cli.seed_talent_e2e",
        "browser|talent-item-ranking.spec.ts|desktop-1440",
    ]
    result = subprocess.run(
        ["bash", "-eu", "-c", (
            "run_seeded_spec() { printf '%s|%s\\n' \"$1\" \"$2\"; }\n"
            f"{talent.group()}\nrun_talent_specs\n"
        )], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines().count("talent-item-ranking.spec.ts|desktop-1440") == 1


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
        "core": ["run_core_matrix", "run_cloud_authorization_specs", "run_request_error_mock_specs", "run_asset_rental_mock_specs", "run_person_profile_mock_specs", "run_ui_theme_mock_specs", "run_invited_housing_specs"],
        "all": [
            "run_core_matrix",
            "run_cloud_authorization_specs",
            "run_request_error_mock_specs",
            "run_asset_rental_mock_specs",
            "run_person_profile_mock_specs",
            "run_ui_theme_mock_specs",
            "run_invited_housing_specs",
            "run_talent_specs",
            "run_esports_specs",
            "run_reactionx_specs",
        ],
        "talent": ["run_talent_specs"],
        "esports": ["run_esports_specs"],
        "reactionx": ["run_reactionx_specs"],
    }
    for mode, calls in expected.items():
        branch = re.search(rf"\n  {mode}\)\n(?P<body>.*?)    ;;", dispatch, re.DOTALL)
        assert branch is not None
        assert branch.group("body").split() == calls


def test_request_error_mock_matrix_runs_without_database_seeding() -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    projects = _shell_array(source, "request_error_mock_projects")
    assert projects == [
        "desktop-1440", "desktop-1024", "tablet-768", "mobile-360", "touch-mobile-390",
    ]
    match = re.search(r"\nrun_request_error_mock_specs\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert match is not None
    function = match.group()
    result = subprocess.run(
        [
            "bash", "-eu", "-c",
            (
                "run_erp_cli() { return 91; }\n"
                'run_playwright() { printf \'%s|%s\\n\' "$1" "$2"; }\n'
                f"request_error_mock_projects=({shlex.join(projects)})\n"
                f"{function}\nrun_request_error_mock_specs\n"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"operations-request-errors.spec.ts|{project}" for project in projects
    ]


def test_asset_rental_mock_matrix_runs_without_database_seeding() -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    projects = _shell_array(source, "asset_rental_mock_projects")
    assert projects == [
        "desktop-1440", "desktop-1024", "tablet-768", "mobile-360", "touch-mobile-390",
    ]
    match = re.search(r"\nrun_asset_rental_mock_specs\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert match is not None
    assert "seed" not in match.group()
    result = subprocess.run(
        ["bash", "-eu", "-c", (
            "run_erp_cli() { return 91; }\n"
            'run_playwright() { printf \'%s|%s\\n\' "$1" "$2"; }\n'
            f"asset_rental_mock_projects=({shlex.join(projects)})\n"
            f"{match.group()}\nrun_asset_rental_mock_specs\n"
        )], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"{spec}|{project}"
        for project in projects
        for spec in ("asset-rentals.spec.ts", "asset-batch-hardware.spec.ts")
    ]


@pytest.mark.parametrize("mode", [None, "core", "all", "talent", "esports", "invited-housing"])
def test_actual_dispatch_executes_asset_rental_and_hardware_matrix_serially_without_seed(
    tmp_path: Path, mode: str | None,
) -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    projects = _shell_array(source, "asset_rental_mock_projects")
    function = re.search(r"\nrun_asset_rental_mock_specs\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert function is not None
    mode_assignment = re.search(r"^e2e_mode=.*$", source, re.MULTILINE)
    assert mode_assignment is not None
    dispatch = 'case "$e2e_mode" in' + source.rsplit('\ncase "$e2e_mode" in', 1)[1]
    harness = (
        f"active={shlex.quote(str(tmp_path / 'active-browser'))}\n"
        "run_core_matrix() { printf 'core\\n'; }\n"
        "run_cloud_authorization_specs() { printf 'cloud\\n'; }\n"
        "run_request_error_mock_specs() { printf 'request-errors\\n'; }\n"
        "run_person_profile_mock_specs() { printf 'person-profiles\\n'; }\n"
        "run_ui_theme_mock_specs() { printf 'ui-themes\\n'; }\n"
        "run_invited_housing_specs() { printf 'invited\\n'; }\n"
        "run_talent_specs() { printf 'talent\\n'; }\n"
        "run_esports_specs() { printf 'esports\\n'; }\n"
        "run_reactionx_specs() { printf 'reactionx\\n'; }\n"
        "run_erp_cli() { printf 'unexpected-seed\\n'; return 91; }\n"
        "run_playwright() {\n"
        '  if ! mkdir "$active"; then printf "overlapping-browser\\n"; return 92; fi\n'
        '  printf "start:%s|%s\\n" "$1" "$2"\n'
        "  sleep 0.01\n"
        '  printf "end:%s|%s\\n" "$1" "$2"\n'
        '  rmdir "$active"\n'
        "}\n"
        f"asset_rental_mock_projects=({shlex.join(projects)})\n"
        f"{function.group()}\n{mode_assignment.group()}\n{dispatch}\nwait\n"
    )
    env = {"PATH": os.environ["PATH"]}
    if mode is not None:
        env["E2E_CORE_MODE"] = mode
    result = subprocess.run(
        ["bash", "-eu", "-c", harness], env=env, capture_output=True,
        text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    if mode in (None, "core", "all"):
        matrix = [
            f"{phase}:{spec}|{project}"
            for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360", "touch-mobile-390")
            for spec in ("asset-rentals.spec.ts", "asset-batch-hardware.spec.ts")
            for phase in ("start", "end")
        ]
        expected = ["core", "cloud", "request-errors", *matrix, "person-profiles", "ui-themes", "invited"]
        if mode == "all":
            expected.extend(["talent", "esports", "reactionx"])
    else:
        expected = ["invited" if mode == "invited-housing" else mode]
    assert result.stdout.splitlines() == expected
    assert not (tmp_path / "active-browser").exists()


@pytest.mark.parametrize("mode", [None, "core", "all", "talent", "esports", "invited-housing"])
def test_actual_dispatch_executes_ui_themes_once_per_viewport_without_seed(
    tmp_path: Path, mode: str | None,
) -> None:
    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    projects = _shell_array(source, "ui_theme_mock_projects")
    assert projects == [
        "desktop-1440", "desktop-1024", "tablet-768", "mobile-360", "touch-mobile-390",
    ]
    function = re.search(r"\nrun_ui_theme_mock_specs\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert function is not None
    mode_assignment = re.search(r"^e2e_mode=.*$", source, re.MULTILINE)
    assert mode_assignment is not None
    dispatch = 'case "$e2e_mode" in' + source.rsplit('\ncase "$e2e_mode" in', 1)[1]
    harness = (
        f"active={shlex.quote(str(tmp_path / 'active-browser'))}\n"
        "run_core_matrix() { printf 'core\\n'; }\n"
        "run_cloud_authorization_specs() { printf 'cloud\\n'; }\n"
        "run_request_error_mock_specs() { printf 'request-errors\\n'; }\n"
        "run_asset_rental_mock_specs() { printf 'asset-rentals\\n'; }\n"
        "run_person_profile_mock_specs() { printf 'person-profiles\\n'; }\n"
        "run_invited_housing_specs() { printf 'invited\\n'; }\n"
        "run_talent_specs() { printf 'talent\\n'; }\n"
        "run_esports_specs() { printf 'esports\\n'; }\n"
        "run_reactionx_specs() { printf 'reactionx\\n'; }\n"
        "run_erp_cli() { printf 'unexpected-seed\\n'; return 91; }\n"
        "run_playwright() {\n"
        '  if ! mkdir "$active"; then printf "overlapping-browser\\n"; return 92; fi\n'
        '  printf "start:%s|%s\\n" "$1" "$2"\n'
        "  sleep 0.01\n"
        '  printf "end:%s|%s\\n" "$1" "$2"\n'
        '  rmdir "$active"\n'
        "}\n"
        f"ui_theme_mock_projects=({shlex.join(projects)})\n"
        f"{function.group()}\n{mode_assignment.group()}\n{dispatch}\nwait\n"
    )
    env = {"PATH": os.environ["PATH"]}
    if mode is not None:
        env["E2E_CORE_MODE"] = mode
    result = subprocess.run(
        ["bash", "-eu", "-c", harness], env=env, capture_output=True,
        text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    if mode in (None, "core", "all"):
        matrix = [
            f"{phase}:{spec}|{project}"
            for project in projects
            for spec in ("ui-themes.spec.ts", "ui-theme-foundation.spec.ts")
            for phase in ("start", "end")
        ]
        expected = [
            "core", "cloud", "request-errors", "asset-rentals", "person-profiles",
            *matrix, "invited",
        ]
        if mode == "all":
            expected.extend(["talent", "esports", "reactionx"])
    else:
        expected = ["invited" if mode == "invited-housing" else mode]
    assert result.stdout.splitlines() == expected
    assert not (tmp_path / "active-browser").exists()


@pytest.mark.parametrize("mode", [None, "core", "all", "talent", "esports", "invited-housing"])
def test_actual_dispatch_runs_person_profile_matrix_serially_without_seed(tmp_path, mode):
    source = CORE_LAUNCHER.read_text(encoding="utf-8")
    projects = _shell_array(source, "person_profile_mock_projects")
    function = re.search(r"\nrun_person_profile_mock_specs\(\) \{\n.*?\n\}", source, re.DOTALL)
    assert function is not None
    mode_assignment = re.search(r"^e2e_mode=.*$", source, re.MULTILINE)
    assert mode_assignment is not None
    dispatch = 'case "$e2e_mode" in' + source.rsplit('\ncase "$e2e_mode" in', 1)[1]
    harness = (
        f"active={shlex.quote(str(tmp_path / 'active-browser'))}\n"
        "run_core_matrix() { printf 'core\\n'; }\n"
        "run_cloud_authorization_specs() { printf 'cloud\\n'; }\n"
        "run_request_error_mock_specs() { printf 'request-errors\\n'; }\n"
        "run_asset_rental_mock_specs() { printf 'asset-rentals\\n'; }\n"
        "run_ui_theme_mock_specs() { printf 'ui-themes\\n'; }\n"
        "run_invited_housing_specs() { printf 'invited\\n'; }\n"
        "run_talent_specs() { printf 'talent\\n'; }\n"
        "run_esports_specs() { printf 'esports\\n'; }\n"
        "run_reactionx_specs() { printf 'reactionx\\n'; }\n"
        "run_erp_cli() { printf 'unexpected-seed\\n'; return 91; }\n"
        "run_playwright() {\n"
        '  if ! mkdir "$active"; then printf "overlapping-browser\\n"; return 92; fi\n'
        '  printf "start:%s|%s\\n" "$1" "$2"\n'
        "  sleep 0.01\n"
        '  printf "end:%s|%s\\n" "$1" "$2"\n'
        '  rmdir "$active"\n'
        "}\n"
        f"person_profile_mock_projects=({shlex.join(projects)})\n"
        f"{function.group()}\n{mode_assignment.group()}\n{dispatch}\nwait\n"
    )
    env = {"PATH": os.environ["PATH"]}
    if mode is not None:
        env["E2E_CORE_MODE"] = mode
    result = subprocess.run(
        ["bash", "-eu", "-c", harness], env=env, capture_output=True,
        text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    if mode in (None, "core", "all"):
        matrix = [
            f"{phase}:{spec}|{project}"
            for project in ("desktop-1440", "mobile-360")
            for spec in (
                "person-detail-chunk.spec.ts",
                "person-profile-assignments.spec.ts",
                "personnel-documents.spec.ts",
            )
            for phase in ("start", "end")
        ]
        expected = ["core", "cloud", "request-errors", "asset-rentals", *matrix, "ui-themes", "invited"]
        if mode == "all":
            expected.extend(["talent", "esports", "reactionx"])
    else:
        expected = ["invited" if mode == "invited-housing" else mode]
    assert result.stdout.splitlines() == expected
    assert not (tmp_path / "active-browser").exists()


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
        "run_request_error_mock_specs() { printf 'request-errors\\n'; }\n"
        "run_asset_rental_mock_specs() { printf 'asset-rentals\\n'; }\n"
        "run_person_profile_mock_specs() { printf 'person-profiles\\n'; }\n"
        "run_ui_theme_mock_specs() { printf 'ui-themes\\n'; }\n"
        "run_talent_specs() { printf 'talent\\n'; }\n"
        "run_esports_specs() { printf 'esports\\n'; }\n"
        "run_reactionx_specs() { printf 'reactionx\\n'; }\n"
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
    expected = invitations if mode == "invited-housing" else ["core", "cloud", "request-errors", "asset-rentals", "person-profiles", "ui-themes", *invitations]
    if mode == "all":
        expected.extend(["talent", "esports", "reactionx"])
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
        launcher_source = _text(SPEC_DELEGATES.get(launcher, launcher))
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
    care_status=0, departure_status=0, intake_roles_status=0, intake_identity_status=0,
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
        elif relative == "scripts/run-intake-activation-roles-e2e.sh":
            command += f"/usr/bin/env > {shlex.quote(str(tmp_path / 'intake-roles-env'))}\nexit {intake_roles_status}\n"
        elif relative == "scripts/run-intake-identity-teams-e2e.sh":
            command += f"/usr/bin/env > {shlex.quote(str(tmp_path / 'intake-identity-env'))}\nexit {intake_identity_status}\n"
        elif relative == "scripts/run-housing-care-gate.sh":
            command += f"/usr/bin/env > {shlex.quote(str(tmp_path / 'care-env'))}\nexit {care_status}\n"
        elif relative == "scripts/run-departure-reminders-gate.sh":
            command += f"/usr/bin/env > {shlex.quote(str(tmp_path / 'departure-env'))}\nexit {departure_status}\n"
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


def test_complete_gate_runs_care_once_with_no_shared_database_or_ambient_secret(tmp_path):
    result, calls = _private_store_gate_trace(tmp_path, environment={
        "HOME": str(tmp_path), "TEST_DATABASE_URL": "production-secret",
        "DATABASE_URL": "production-secret", "E2E_HOUSING_CARE_RUNTIME_LEDGER": "/unknown/ledger",
        "NODE_OPTIONS": "production-secret", "E2E_API_PORT": "32786",
        "FIELD_ENCRYPTION_MASTER_KEY": "production-secret",
    })
    assert result.returncode == 0, result.stderr
    invocation = "launcher scripts/run-housing-care-gate.sh "
    assert calls.count(invocation) == 1
    child = dict(line.split("=", 1) for line in (tmp_path / "care-env").read_text().splitlines())
    assert child["DOCKER_HOST"] == "unix:///fake.sock"
    assert child["E2E_PYTHON_BIN"] == str(tmp_path / "repository/.venv/bin/python")
    assert set(child) <= {"PATH", "HOME", "DOCKER_HOST", "E2E_PYTHON_BIN",
                          "PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_EXECUTABLE_PATH", "PWD", "SHLVL", "_"}
    assert not any(key in child for key in ("DATABASE_URL", "TEST_DATABASE_URL",
                   "E2E_HOUSING_CARE_RUNTIME_LEDGER", "FIELD_ENCRYPTION_MASTER_KEY", "NODE_OPTIONS"))
    reporting = calls.index("launcher scripts/run-housing-reporting-e2e.sh ")
    gate_call = calls.index(invocation)
    registration = calls.index("launcher scripts/run-self-service-registration-e2e.sh ")
    assert reporting < gate_call < registration


def test_complete_gate_does_not_hide_care_failure_or_run_later_suites(tmp_path):
    result, calls = _private_store_gate_trace(tmp_path, care_status=89)
    assert result.returncode == 89
    assert calls.count("launcher scripts/run-housing-care-gate.sh ") == 1
    assert not any(call.startswith(("launcher scripts/run-self-service-registration-e2e.sh ",
                                   "launcher scripts/run-assisted-personnel-e2e.sh ",
                                   "launcher scripts/backup-db.sh ")) for call in calls)
    assert calls[-1].endswith("down --remove-orphans --volumes")


def test_complete_gate_runs_departure_browser_once_with_private_inputs(tmp_path):
    result, calls = _private_store_gate_trace(tmp_path, environment={
        "HOME": str(tmp_path), "TEST_DATABASE_URL": "production-secret",
        "DATABASE_URL": "production-secret", "E2E_DEPARTURE_RUNTIME_LEDGER": "/foreign/ledger",
        "NODE_OPTIONS": "production-secret", "FIELD_LOOKUP_KEY": "production-secret",
    })
    assert result.returncode == 0, result.stderr
    invocation = "launcher scripts/run-departure-reminders-gate.sh "
    assert calls.count(invocation) == 1
    child = dict(line.split("=", 1) for line in (tmp_path / "departure-env").read_text().splitlines())
    assert child["DOCKER_HOST"] == "unix:///fake.sock"
    assert child["E2E_PYTHON_BIN"] == str(tmp_path / "repository/.venv/bin/python")
    assert set(child) <= {"PATH", "HOME", "DOCKER_HOST", "E2E_PYTHON_BIN",
                          "PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_EXECUTABLE_PATH", "PWD", "SHLVL", "_"}
    assert not any(key in child for key in ("DATABASE_URL", "TEST_DATABASE_URL",
                   "E2E_DEPARTURE_RUNTIME_LEDGER", "FIELD_LOOKUP_KEY", "NODE_OPTIONS"))
    care = calls.index("launcher scripts/run-housing-care-gate.sh ")
    departure = calls.index(invocation)
    registration = calls.index("launcher scripts/run-self-service-registration-e2e.sh ")
    assert care < departure < registration


def test_complete_gate_bubbles_departure_failure_before_later_suites(tmp_path):
    result, calls = _private_store_gate_trace(tmp_path, departure_status=88)
    assert result.returncode == 88
    assert calls.count("launcher scripts/run-departure-reminders-gate.sh ") == 1
    assert not any(call.startswith(("launcher scripts/run-self-service-registration-e2e.sh ",
                                   "launcher scripts/run-assisted-personnel-e2e.sh ",
                                   "launcher scripts/backup-db.sh ")) for call in calls)
    assert calls[-1].endswith("down --remove-orphans --volumes")


def test_departure_wrapper_executes_only_browser_owned_profile(tmp_path):
    wrapper = REPOSITORY_ROOT / "scripts/run-departure-reminders-gate.sh"
    python = tmp_path / "python"
    log = tmp_path / "argv"
    python.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(log))}\n",
        encoding="utf-8",
    )
    python.chmod(0o700)
    result = subprocess.run(
        ["bash", str(wrapper)], cwd=REPOSITORY_ROOT,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path),
             "E2E_PYTHON_BIN": str(python), "DATABASE_URL": "foreign-secret",
             "E2E_DEPARTURE_RUNTIME_LEDGER": "/foreign/ledger"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        str(REPOSITORY_ROOT / "scripts/housing-care-owned-gate.py"),
        "--python", str(python), "--profile", "departure-reminders", "--phase", "browser",
    ]


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


def test_complete_gate_dispatches_intake_roles_once_with_owned_private_inputs(tmp_path):
    result, calls = _private_store_gate_trace(tmp_path, environment={
        "HOME": str(tmp_path), "APP_ENV": "production", "E2E_DB_PORT": "56541",
        "TEST_DATABASE_URL": "production-secret", "DATABASE_URL": "production-secret",
        "ASSISTED_E2E_DISPOSABLE_DATABASE": "production", "E2E_PYTHON_BIN": "/untrusted/python",
        "E2E_API_PORT": "5432", "E2E_WEB_PORT": "5432", "FIELD_LOOKUP_KEY": "production-secret",
        "SMTP_HOST": "smtp.production.invalid", "NODE_OPTIONS": "production-secret", "VITE_SECRET": "production-secret",
        "PLAYWRIGHT_BROWSERS_PATH": "/synthetic/browser-cache", "PLAYWRIGHT_EXECUTABLE_PATH": "/synthetic/browser",
    })
    assert result.returncode == 0, result.stderr
    invocation = "launcher scripts/run-intake-activation-roles-e2e.sh "
    assert calls.count(invocation) == 1
    env = dict(line.split("=", 1) for line in (tmp_path / "intake-roles-env").read_text().splitlines())
    assert env["APP_ENV"] == "test"
    assert env["TEST_DATABASE_URL"] == "postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:56541/erp_e2e"
    assert env["ASSISTED_E2E_DISPOSABLE_DATABASE"] == "erp_e2e"
    assert env["E2E_PYTHON_BIN"] == str(tmp_path / "repository/.venv/bin/python")
    assert env["E2E_API_PORT"] == "18029" and env["E2E_WEB_PORT"] == "15209"
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == "/synthetic/browser-cache"
    assert env["PLAYWRIGHT_EXECUTABLE_PATH"] == "/synthetic/browser"
    assert set(env) <= {"PATH", "HOME", "APP_ENV", "TEST_DATABASE_URL", "ASSISTED_E2E_DISPOSABLE_DATABASE",
                        "E2E_PYTHON_BIN", "E2E_API_PORT", "E2E_WEB_PORT", "PLAYWRIGHT_BROWSERS_PATH",
                        "PLAYWRIGHT_EXECUTABLE_PATH", "PWD", "SHLVL", "_"}
    provision = next(i for i, call in enumerate(calls) if call.endswith("up -d --wait db"))
    stop = max(i for i, call in enumerate(calls) if call.endswith("stop proxy web api scheduler"))
    assisted = calls.index("launcher scripts/run-assisted-personnel-e2e.sh --suite assisted")
    intake = calls.index(invocation)
    evaluation = calls.index("launcher scripts/run-evaluation-page-e2e.sh ")
    assert provision < stop < assisted < intake < evaluation


def test_complete_gate_bubbles_intake_roles_failure_before_later_suites_and_backup(tmp_path):
    result, calls = _private_store_gate_trace(tmp_path, intake_roles_status=85)
    assert result.returncode == 85, result.stderr
    assert calls.count("launcher scripts/run-assisted-personnel-e2e.sh --suite assisted") == 1
    assert calls.count("launcher scripts/run-intake-activation-roles-e2e.sh ") == 1
    assert not any(call.startswith(("launcher scripts/run-evaluation-page-e2e.sh ",
                                   "launcher scripts/run-stage2-local-acceptance.sh ",
                                   "launcher scripts/backup-db.sh ",
                                   "launcher scripts/restore-db.sh ")) for call in calls)
    assert calls[-1].endswith("down --remove-orphans --volumes")


def test_complete_gate_dispatches_identity_once_with_owned_inputs_and_stops_on_failure(tmp_path):
    result, calls = _private_store_gate_trace(tmp_path, intake_identity_status=84, environment={
        "DATABASE_URL": "production-secret", "ASSISTED_E2E_DISPOSABLE_DATABASE": "production",
        "NODE_OPTIONS": "production-secret", "E2E_API_PORT": "5432", "E2E_WEB_PORT": "5432",
    })
    assert result.returncode == 84
    assert calls.count("launcher scripts/run-intake-identity-teams-e2e.sh ") == 1
    env = dict(line.split("=", 1) for line in (tmp_path / "intake-identity-env").read_text().splitlines())
    assert env["ASSISTED_E2E_DISPOSABLE_DATABASE"] == "erp_e2e"
    assert env["APP_ENV"] == "test"
    assert env["E2E_API_PORT"] == "18039" and env["E2E_WEB_PORT"] == "15219"
    assert "DATABASE_URL" not in env and "NODE_OPTIONS" not in env
    assert not any(call.startswith(("launcher scripts/run-evaluation-page-e2e.sh ",
                                   "launcher scripts/backup-db.sh ")) for call in calls)
    assert calls[-1].endswith("down --remove-orphans --volumes")


@pytest.mark.parametrize("arguments", [[], ["--grep", "IDT1"], ["--project=desktop-1440"], ["--suite", "team"]])
def test_identity_wrapper_has_no_partial_selector_and_preserves_delegate_exit(tmp_path, arguments):
    import shutil

    repository = tmp_path / "repo with spaces"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    wrapper = REPOSITORY_ROOT / "scripts/run-intake-identity-teams-e2e.sh"
    shutil.copy2(wrapper, scripts / wrapper.name)
    log = tmp_path / "identity-args"
    (scripts / "run-assisted-personnel-e2e.sh").write_text(
        "#!/bin/sh\n" + f"printf '%s\\n' \"$@\" > {shlex.quote(str(log))}\nexit 53\n")
    result = subprocess.run(["bash", str(scripts / wrapper.name), *arguments], cwd=repository,
                            env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=False)
    assert result.returncode == (2 if arguments else 53)
    if arguments:
        assert not log.exists()
    else:
        assert log.read_text().splitlines() == ["--suite", "intake-identity"]


def test_intake_roles_wrapper_dispatches_only_fixed_suite_and_preserves_failure(tmp_path):
    wrapper = REPOSITORY_ROOT / "scripts/run-intake-activation-roles-e2e.sh"
    assert wrapper.is_file(), "fixed intake-role suite wrapper missing"
    repository = tmp_path / "repo with spaces"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(wrapper, scripts / wrapper.name)
    log = tmp_path / "suite-args"
    (scripts / "run-assisted-personnel-e2e.sh").write_text(
        "#!/bin/sh\n" + f"printf '%s\\n' \"$@\" > {shlex.quote(str(log))}\nexit 53\n")
    result = subprocess.run(["bash", str(scripts / wrapper.name)], cwd=repository,
                            env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=False)
    assert result.returncode == 53
    assert log.read_text().splitlines() == ["--suite", "intake-roles"]
    log.unlink()
    refused = subprocess.run(["bash", str(scripts / wrapper.name), "--suite", "registration"], cwd=repository,
                             env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=False)
    assert refused.returncode == 2
    assert not log.exists(), "caller selectors must never reach the fixed delegate"


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
