from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = REPOSITORY_ROOT / "scripts/run-local-container-acceptance.sh"
OPERATIONS_DOC = REPOSITORY_ROOT / "docs/operations/local-container-acceptance.md"
COMPOSE_E2E = REPOSITORY_ROOT / "compose.e2e.yaml"
BAIT_DATABASE_URL = "postgresql+psycopg://production-user:production-password@db/production"
BAIT_SERVICE_TOKEN = "production-evaluation-service-token-do-not-leak"
BAIT_FIELD_SECRET = "production-field-encryption-secret-do-not-leak"
EXPECTED_ALEMBIC_HEAD = "0090_administration_team_mgmt"


@dataclass(frozen=True)
class FakeToolchain:
    env: dict[str, str]
    docker_log: Path
    curl_log: Path
    daemon_unavailable: Path
    compose_plugin_unavailable: Path
    legacy_compose: Path
    compose_failure: Path
    curl_failure: Path


def _write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _fake_toolchain(tmp_path: Path) -> FakeToolchain:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.jsonl"
    curl_log = tmp_path / "curl.jsonl"
    daemon_unavailable = tmp_path / "daemon-unavailable"
    compose_plugin_unavailable = tmp_path / "compose-plugin-unavailable"
    legacy_compose = tmp_path / "legacy-compose"
    compose_failure = tmp_path / "compose-failure"
    curl_failure = tmp_path / "curl-failure"

    fake_docker = f"""
        #!/usr/bin/env python3
        from __future__ import annotations

        import json
        import os
        import stat
        import sys
        from pathlib import Path

        argv = sys.argv[1:]
        client = Path(sys.argv[0]).name
        is_compose = client == "docker-compose" or (
            bool(argv) and argv[0] == "compose"
        )
        env_file = None
        if "--env-file" in argv:
            env_file = Path(argv[argv.index("--env-file") + 1])
        entry = {{
            "client": client,
            "argv": argv,
            "http_port": os.environ.get("E2E_HTTP_PORT"),
            "db_port": os.environ.get("E2E_DB_PORT"),
            "tmp_mode": (
                stat.S_IMODE(env_file.parent.stat().st_mode)
                if env_file is not None and env_file.parent.is_dir()
                else None
            ),
            "env_file_mode": (
                stat.S_IMODE(env_file.stat().st_mode)
                if env_file is not None and env_file.is_file()
                else None
            ),
            "environment": dict(os.environ) if is_compose else {{}},
        }}
        with Path({str(docker_log)!r}).open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, sort_keys=True) + "\\n")

        if client == "docker" and argv == ["--version"]:
            print("Docker version 27.0.0, build test")
            raise SystemExit(0)
        if client == "docker" and argv == ["info"]:
            if Path({str(daemon_unavailable)!r}).exists():
                raise SystemExit(1)
            print("fake daemon available")
            raise SystemExit(0)
        if client == "docker" and argv == ["compose", "version"]:
            if Path({str(compose_plugin_unavailable)!r}).exists():
                raise SystemExit(1)
            print("Docker Compose version v2.29.0")
            raise SystemExit(0)
        if client == "docker" and argv == ["compose", "version", "--short"]:
            if Path({str(compose_plugin_unavailable)!r}).exists():
                raise SystemExit(1)
            print("2.29.0")
            raise SystemExit(0)
        if client == "docker-compose" and argv == ["version"]:
            version = (
                "1.29.2"
                if Path({str(legacy_compose)!r}).exists()
                else "2.29.2"
            )
            print(f"Docker Compose version v{{version}}")
            raise SystemExit(0)
        if client == "docker-compose" and argv == ["version", "--short"]:
            version = (
                "1.29.2"
                if Path({str(legacy_compose)!r}).exists()
                else "2.29.2"
            )
            print(version)
            raise SystemExit(0)

        command = " ".join(argv)
        failure_path = Path({str(compose_failure)!r})
        if failure_path.exists() and failure_path.read_text().strip() in command:
            print('{{"EVALUATION_TOOL_SERVICE_TOKEN":"{BAIT_SERVICE_TOKEN}"}}')
            print('- FIELD_ENCRYPTION_MASTER_KEY: {BAIT_FIELD_SECRET}', file=sys.stderr)
            raise SystemExit(41)
        if "SELECT version_num FROM" in command and "alembic_version" in command:
            print("{EXPECTED_ALEMBIC_HEAD}")
        elif "target_coach_person_ids" in command and Path({str(tmp_path / 'missing-scope-column')!r}).exists():
            print("f")
        elif "requires_verification" in command and Path({str(tmp_path / 'missing-guardian-access-column')!r}).exists():
            print("f")
        elif "-Atc" in argv:
            print("t")
        """
    _write_executable(bin_dir / "docker", fake_docker)
    _write_executable(bin_dir / "docker-compose", fake_docker)
    _write_executable(
        bin_dir / "curl",
        f"""
        #!/usr/bin/env python3
        from __future__ import annotations

        import json
        import os
        import sys
        from pathlib import Path

        with Path({str(curl_log)!r}).open("a", encoding="utf-8") as log:
            log.write(json.dumps(sys.argv[1:]) + "\\n")
        if Path({str(curl_failure)!r}).exists():
            print("DATABASE_URL={BAIT_DATABASE_URL}")
            print("EVALUATION_TOOL_SERVICE_TOKEN={BAIT_SERVICE_TOKEN}", file=sys.stderr)
            print("FIELD_ENCRYPTION_MASTER_KEY={BAIT_FIELD_SECRET}", file=sys.stderr)
            raise SystemExit(22)
        """,
    )

    summary_path = tmp_path / "local-container-acceptance-summary.json"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CONTAINER_ACCEPTANCE_PYTHON_BIN": sys.executable,
        "LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH": str(summary_path),
        "APP_ENV": "production",
        "DATABASE_URL": BAIT_DATABASE_URL,
        "FIELD_ENCRYPTION_MASTER_KEY": BAIT_FIELD_SECRET,
        "FIELD_LOOKUP_KEY": "production-field-lookup-secret-do-not-leak",
        "SESSION_TOKEN_PEPPER": "production-session-pepper-do-not-leak",
        "REGISTRATION_VERIFICATION_PEPPER": "production-registration-secret-do-not-leak",
        "EVALUATION_TOOL_SERVICE_TOKEN": BAIT_SERVICE_TOKEN,
        "COMPOSE_FILE": "compose.yaml:compose.prod.yaml",
        "COMPOSE_ENV_FILES": ".env.production",
        "COMPOSE_PROJECT_NAME": "production-project",
    }
    env.pop("ERP_BIND_ADDRESS", None)
    return FakeToolchain(
        env=env,
        docker_log=docker_log,
        curl_log=curl_log,
        daemon_unavailable=daemon_unavailable,
        compose_plugin_unavailable=compose_plugin_unavailable,
        legacy_compose=legacy_compose,
        compose_failure=compose_failure,
        curl_failure=curl_failure,
    )


def _run_launcher(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert LAUNCHER.is_file(), "local container acceptance launcher is missing"
    return subprocess.run(
        [str(LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _json_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _compose_subcommand(argv: list[str]) -> str | None:
    for candidate in ("config", "up", "exec", "down"):
        if candidate in argv:
            return candidate
    return None


def _is_compose_event(event: dict[str, object]) -> bool:
    argv = event["argv"]
    assert isinstance(argv, list)
    return event["client"] == "docker-compose" or "compose" in argv


def _compose_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        event for event in events if _is_compose_event(event) and _compose_subcommand(event["argv"])
    ]


def test_launcher_has_the_disposable_compose_safety_contract() -> None:
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
    assert "set -euo pipefail" in source
    assert "umask 077" in source
    assert "docker compose version" in source
    assert "command -v docker-compose" in source
    assert "/opt/homebrew/bin/docker-compose" in source
    assert '"$docker_bin" --version' in source
    assert '"$docker_bin" info' in source
    assert 'project_name="esports-erp-container-acceptance-' in source
    assert "compose.yaml" in source
    assert "compose.e2e.yaml" in source
    assert "compose.prod.yaml" not in source
    assert "config --quiet" in source
    assert source.index("config --quiet") < source.index("up --build --wait")
    assert "exec -T api alembic upgrade head" in source
    assert "app.cli.e2e_database_safety" in source
    assert source.index("app.cli.e2e_database_safety") < source.index(
        "exec -T api alembic upgrade head"
    )
    assert "postgresql+psycopg://erp_e2e:erp-e2e-only@db:5432/erp_e2e" in source
    assert 'source "$repo_root/scripts/db-schema-manifest.sh"' in source
    assert "/healthz" in source
    assert "/api/v1/health/live" in source
    assert "trap cleanup EXIT" in source
    assert source.index("trap cleanup EXIT") < source.index(
        'source "$repo_root/scripts/db-schema-manifest.sh"'
    )
    assert "down --volumes --remove-orphans" in source
    assert "COMPOSE_DISABLE_ENV_FILE=1" in source
    assert '--env-file "$empty_env_file"' in source
    assert "env -i" in source
    assert 'chmod 700 "$tmp_dir"' in source
    assert 'chmod 600 "$summary_path"' in source
    assert 'chmod 700 "$summary_dir"' not in source
    assert "--connect-timeout" in source
    assert "--max-time" in source
    assert "app.cli.redact_registration_e2e_output" in source
    assert "NamedTemporaryFile" in source
    assert 'with_name(f".{path.name}.tmp")' not in source
    assert "FROM public.alembic_version" in source
    assert "FROM public.roles" in source
    assert "FROM public.permissions" in source
    assert source.count("namespace_row.nspname = 'public'") >= 3
    assert "ERP_BIND_ADDRESS" in source
    for forbidden in ("playwright", "test:e2e", "npm --prefix"):
        assert forbidden not in source.casefold()


def test_launcher_runs_config_before_up_and_always_destroys_the_project(
    tmp_path: Path,
) -> None:
    tools = _fake_toolchain(tmp_path)

    result = _run_launcher(tools.env)

    assert result.returncode == 0, result.stderr
    events = _json_lines(tools.docker_log)
    compose_events = _compose_events(events)
    subcommands = [_compose_subcommand(event["argv"]) for event in compose_events]
    assert subcommands[0] == "config"
    assert "up" in subcommands
    assert subcommands[-1] == "down"
    assert subcommands.index("config") < subcommands.index("up")

    all_arguments = [event["argv"] for event in compose_events]
    assert any("--quiet" in argv for argv in all_arguments if "config" in argv)
    assert any(
        all(option in argv for option in ("--build", "--wait"))
        for argv in all_arguments
        if "up" in argv
    )
    assert any(
        all(option in argv for option in ("--volumes", "--remove-orphans"))
        for argv in all_arguments
        if "down" in argv
    )
    assert any(
        "exec" in argv and "api" in argv and argv[-3:] == ["alembic", "upgrade", "head"]
        for argv in all_arguments
    )
    database_safety_indices = [
        index for index, argv in enumerate(all_arguments) if "app.cli.e2e_database_safety" in argv
    ]
    assert database_safety_indices
    database_safety_index = database_safety_indices[0]
    alembic_index = next(
        index
        for index, argv in enumerate(all_arguments)
        if argv[-3:] == ["alembic", "upgrade", "head"]
    )
    assert database_safety_index < alembic_index
    assert (
        "TEST_DATABASE_URL="
        "postgresql+psycopg://erp_e2e:erp-e2e-only@db:5432/erp_e2e"
        in all_arguments[database_safety_index]
    )
    assert any(
        "exec" in argv and "db" in argv and "alembic_version" in " ".join(argv)
        for argv in all_arguments
    )

    project_names = {argv[argv.index("-p") + 1] for argv in all_arguments if "-p" in argv}
    assert len(project_names) == 1
    assert next(iter(project_names)).startswith("esports-erp-container-acceptance-")
    for argv in all_arguments:
        assert str(REPOSITORY_ROOT / "compose.yaml") in argv
        assert str(REPOSITORY_ROOT / "compose.e2e.yaml") in argv
        assert all("compose.prod.yaml" not in argument for argument in argv)

    runtime_events = [event for event in compose_events if event["tmp_mode"] is not None]
    assert runtime_events
    assert {event["tmp_mode"] for event in runtime_events} == {0o700}
    assert {event["env_file_mode"] for event in runtime_events} == {0o600}
    http_ports = {int(event["http_port"]) for event in runtime_events}
    db_ports = {int(event["db_port"]) for event in runtime_events}
    assert len(http_ports) == len(db_ports) == 1
    assert http_ports != db_ports
    assert all(0 < port < 65536 for port in http_ports | db_ports)

    curl_calls = [json.loads(line) for line in tools.curl_log.read_text().splitlines()]
    assert any(call[-1].endswith("/healthz") for call in curl_calls)
    assert any(call[-1].endswith("/api/v1/health/live") for call in curl_calls)
    assert all("--connect-timeout" in call and "--max-time" in call for call in curl_calls)

    summary_path = Path(tools.env["LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH"])
    assert stat.S_IMODE(summary_path.stat().st_mode) == 0o600
    summary_text = summary_path.read_text(encoding="utf-8")
    summary = json.loads(summary_text)
    assert summary["status"] == "passed"
    assert summary["cleanup_completed"] is True
    assert summary["compose_files"] == ["compose.yaml", "compose.e2e.yaml"]
    assert re.fullmatch(r"[0-9a-f]{40}", summary["git_sha"])
    assert re.fullmatch(r"[0-9a-f]{64}", summary["source_tree_sha256"])
    assert isinstance(summary["checks"], dict)
    assert set(summary["checks"].values()) == {"passed"}
    for secret in (
        "production-user",
        "production-password",
        BAIT_SERVICE_TOKEN,
        BAIT_FIELD_SECRET,
    ):
        assert secret not in summary_text


def test_launcher_cleans_up_and_records_a_redacted_failure_summary(
    tmp_path: Path,
) -> None:
    tools = _fake_toolchain(tmp_path)
    tools.curl_failure.touch()

    result = _run_launcher(tools.env)

    assert result.returncode != 0
    events = _json_lines(tools.docker_log)
    compose_commands = [_compose_subcommand(event["argv"]) for event in _compose_events(events)]
    assert compose_commands[-1] == "down"
    summary_text = Path(tools.env["LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH"]).read_text(
        encoding="utf-8"
    )
    summary = json.loads(summary_text)
    assert summary["status"] == "failed"
    assert summary["cleanup_completed"] is True
    assert summary["exit_code"] == result.returncode
    assert isinstance(summary["checks"], dict)
    assert summary["checks"]["proxy_health"] == "failed"
    assert summary["checks"]["api_health"] == "pending"
    combined_output = result.stdout + result.stderr + summary_text
    for bait in (BAIT_DATABASE_URL, BAIT_SERVICE_TOKEN, BAIT_FIELD_SECRET):
        assert bait not in combined_output
    assert "[DATABASE_URL]" in result.stderr
    assert "[REDACTED]" in result.stderr


def test_launcher_stops_before_compose_when_the_daemon_is_unavailable(
    tmp_path: Path,
) -> None:
    tools = _fake_toolchain(tmp_path)
    tools.daemon_unavailable.touch()

    result = _run_launcher(tools.env)

    assert result.returncode == 3
    events = _json_lines(tools.docker_log)
    flattened = [argument for event in events for argument in event["argv"]]
    assert "info" in flattened
    assert not {"config", "up", "exec", "down"}.intersection(flattened)
    assert not Path(tools.env["LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH"]).exists()


def test_launcher_rejects_a_non_loopback_bind_before_docker_access(
    tmp_path: Path,
) -> None:
    tools = _fake_toolchain(tmp_path)
    tools.env["ERP_BIND_ADDRESS"] = "0.0.0.0"

    result = _run_launcher(tools.env)

    assert result.returncode == 2
    assert "127.0.0.1" in result.stderr
    assert not tools.docker_log.exists()


def test_compose_commands_use_only_the_e2e_environment_allowlist(
    tmp_path: Path,
) -> None:
    tools = _fake_toolchain(tmp_path)

    result = _run_launcher(tools.env)

    assert result.returncode == 0, result.stderr
    events = _json_lines(tools.docker_log)
    compose_process_events = [event for event in events if _is_compose_event(event)]
    assert compose_process_events
    forbidden_variables = (
        "APP_ENV",
        "DATABASE_URL",
        "FIELD_ENCRYPTION_MASTER_KEY",
        "FIELD_LOOKUP_KEY",
        "SESSION_TOKEN_PEPPER",
        "REGISTRATION_VERIFICATION_PEPPER",
        "EVALUATION_TOOL_SERVICE_TOKEN",
        "COMPOSE_FILE",
        "COMPOSE_ENV_FILES",
        "COMPOSE_PROJECT_NAME",
        "LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH",
    )
    for event in compose_process_events:
        environment = event["environment"]
        assert isinstance(environment, dict)
        assert environment.get("COMPOSE_DISABLE_ENV_FILE") == "1"
        for forbidden in forbidden_variables:
            assert forbidden not in environment

    compose_events = _compose_events(events)
    assert compose_events
    for event in compose_events:
        environment = event["environment"]
        assert isinstance(environment, dict)
        assert environment.get("ERP_BIND_ADDRESS") == "127.0.0.1"
        assert str(environment.get("E2E_HTTP_PORT", "")).isdigit()
        assert str(environment.get("E2E_DB_PORT", "")).isdigit()


def test_launcher_falls_back_to_a_path_docker_compose_client(tmp_path: Path) -> None:
    tools = _fake_toolchain(tmp_path)
    tools.compose_plugin_unavailable.touch()

    result = _run_launcher(tools.env)

    assert result.returncode == 0, result.stderr
    compose_events = _compose_events(_json_lines(tools.docker_log))
    assert compose_events
    assert {event["client"] for event in compose_events} == {"docker-compose"}


def test_launcher_rejects_legacy_standalone_compose_before_mutation(
    tmp_path: Path,
) -> None:
    tools = _fake_toolchain(tmp_path)
    tools.compose_plugin_unavailable.touch()
    tools.legacy_compose.touch()

    result = _run_launcher(tools.env)

    assert result.returncode == 3
    assert "Compose v2" in result.stderr
    events = _json_lines(tools.docker_log)
    assert not _compose_events(events)


@pytest.mark.parametrize(
    ("failure_marker", "failed_check", "next_check"),
    [
        ("config --quiet", "compose_config", "compose_up"),
        ("up --build --wait", "compose_up", "database_safety"),
        (
            "app.cli.e2e_database_safety",
            "database_safety",
            "alembic_upgrade",
        ),
        ("alembic upgrade head", "alembic_upgrade", "migration_chain"),
        ("test -f /app/migrations/versions", "migration_chain", "proxy_health"),
        (
            "SELECT version_num FROM public.alembic_version",
            "schema_manifest",
            None,
        ),
    ],
)
def test_launcher_records_each_compose_failure_and_cleans_up(
    tmp_path: Path,
    failure_marker: str,
    failed_check: str,
    next_check: str | None,
) -> None:
    tools = _fake_toolchain(tmp_path)
    tools.compose_failure.write_text(failure_marker, encoding="utf-8")

    result = _run_launcher(tools.env)

    assert result.returncode != 0
    events = _json_lines(tools.docker_log)
    assert _compose_subcommand(_compose_events(events)[-1]["argv"]) == "down"
    summary_text = Path(tools.env["LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH"]).read_text(
        encoding="utf-8"
    )
    summary = json.loads(summary_text)
    assert summary["status"] == "failed"
    assert summary["cleanup_completed"] is True
    assert summary["checks"][failed_check] == "failed"
    if next_check is not None:
        assert summary["checks"][next_check] == "pending"
    combined_output = result.stdout + result.stderr + summary_text
    assert BAIT_SERVICE_TOKEN not in combined_output
    assert BAIT_FIELD_SECRET not in combined_output


def test_launcher_reports_cleanup_failure_as_a_failed_acceptance(
    tmp_path: Path,
) -> None:
    tools = _fake_toolchain(tmp_path)
    tools.compose_failure.write_text("down --volumes --remove-orphans", encoding="utf-8")

    result = _run_launcher(tools.env)

    assert result.returncode != 0
    summary_text = Path(tools.env["LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH"]).read_text(
        encoding="utf-8"
    )
    summary = json.loads(summary_text)
    assert summary["status"] == "failed"
    assert summary["cleanup_completed"] is False
    assert set(summary["checks"].values()) == {"passed"}
    combined_output = result.stdout + result.stderr + summary_text
    assert BAIT_SERVICE_TOKEN not in combined_output
    assert BAIT_FIELD_SECRET not in combined_output


def test_launcher_generates_a_new_project_name_for_each_run(tmp_path: Path) -> None:
    tools = _fake_toolchain(tmp_path)

    first = _run_launcher(tools.env)
    second = _run_launcher(tools.env)

    assert first.returncode == second.returncode == 0
    project_names = {
        argv[argv.index("-p") + 1]
        for event in _compose_events(_json_lines(tools.docker_log))
        if "-p" in (argv := event["argv"])
    }
    assert len(project_names) == 2


def test_launcher_does_not_repermission_an_existing_summary_parent(
    tmp_path: Path,
) -> None:
    tools = _fake_toolchain(tmp_path)
    summary_parent = tmp_path / "existing-summary-parent"
    summary_parent.mkdir()
    summary_parent.chmod(0o755)
    tools.env["LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH"] = str(summary_parent / "summary.json")

    result = _run_launcher(tools.env)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(summary_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE((summary_parent / "summary.json").stat().st_mode) == 0o600


def test_launcher_fails_without_claiming_a_summary_was_written(
    tmp_path: Path,
) -> None:
    tools = _fake_toolchain(tmp_path)
    summary_parent = tmp_path / "read-only-summary-parent"
    summary_parent.mkdir()
    summary_parent.chmod(0o500)
    summary_path = summary_parent / "summary.json"
    tools.env["LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH"] = str(summary_path)

    try:
        result = _run_launcher(tools.env)
    finally:
        summary_parent.chmod(0o700)

    assert result.returncode != 0
    assert "无法写入本地容器验收摘要" in result.stderr
    assert "本地容器验收摘要已写入指定路径" not in result.stdout
    assert not summary_path.exists()
    events = _json_lines(tools.docker_log)
    assert _compose_subcommand(_compose_events(events)[-1]["argv"]) == "down"


def _service_block(source: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)",
        source,
    )
    assert match is not None
    return match.group(1)


def _environment_value(service_block: str, variable: str) -> str:
    match = re.search(
        rf"(?m)^      {re.escape(variable)}:\s*([^\s]+)\s*$",
        service_block,
    )
    assert match is not None
    return match.group(1).strip('"')


def test_compose_e2e_tokens_are_complete_distinct_and_valid_for_each_python_service() -> None:
    from app.core.config import Settings

    source = COMPOSE_E2E.read_text(encoding="utf-8")
    token_names = (
        "EVALUATION_TOOL_SERVICE_TOKEN",
        "EVALUATION_TOOL_WEB_SERVICE_TOKEN",
        "EVALUATION_TOOL_WORKER_SERVICE_TOKEN",
    )
    service_tokens: dict[str, dict[str, str]] = {}
    for service in ("api", "scheduler"):
        block = _service_block(source, service)
        service_tokens[service] = {
            token_name: _environment_value(block, token_name) for token_name in token_names
        }
        tokens = tuple(service_tokens[service].values())
        assert all(
            len(token) >= 32 and not any(char.isspace() for char in token) for token in tokens
        )
        assert len(set(tokens)) == 3
    assert service_tokens["api"] == service_tokens["scheduler"]

    settings = Settings(
        app_env="test",
        database_url="postgresql+psycopg://erp_e2e@127.0.0.1/erp_e2e",
        session_cookie_secure=False,
        field_encryption_master_key="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        field_lookup_key="Hy4tLCsqKSgnJiUkIyIhIB8eHRwbGhkYFxYVFBMSERA=",
        session_token_pepper="EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8=",
        evaluation_tool_service_token=service_tokens["api"][token_names[0]],
        evaluation_tool_web_service_token=service_tokens["api"][token_names[1]],
        evaluation_tool_worker_service_token=service_tokens["api"][token_names[2]],
    )
    settings.validate_runtime_secrets()


def test_compose_e2e_keeps_scheduler_out_of_the_default_safety_gate() -> None:
    source = COMPOSE_E2E.read_text(encoding="utf-8")
    scheduler = _service_block(source, "scheduler")

    assert "profiles:" in scheduler
    assert "manual-scheduler" in scheduler


def test_container_log_redactor_removes_labeled_urls_tokens_and_keys() -> None:
    from app.cli.redact_registration_e2e_output import redact_text

    value = (
        f"DATABASE_URL={BAIT_DATABASE_URL}\n"
        f"EVALUATION_TOOL_SERVICE_TOKEN={BAIT_SERVICE_TOKEN}\n"
        f"FIELD_ENCRYPTION_MASTER_KEY={BAIT_FIELD_SECRET}\n"
    )

    redacted = redact_text(value)

    for bait in (BAIT_DATABASE_URL, BAIT_SERVICE_TOKEN, BAIT_FIELD_SECRET):
        assert bait not in redacted
    assert "[DATABASE_URL]" in redacted
    assert redacted.count("[REDACTED]") == 2


def test_container_log_redactor_removes_json_yaml_and_lowercase_secrets() -> None:
    from app.cli.redact_registration_e2e_output import redact_text

    json_token = "json-service-token-that-must-never-leak"
    yaml_key = "yaml-field-key-that-must-never-leak"
    lowercase_secret = "lowercase-pepper-that-must-never-leak"
    value = (
        '{"EVALUATION_TOOL_SERVICE_TOKEN":"'
        + json_token
        + '", "FIELD_LOOKUP_KEY": "'
        + yaml_key
        + '"}\n'
        + "- session_token_pepper: "
        + lowercase_secret
        + "\n"
    )

    redacted = redact_text(value)

    for secret in (json_token, yaml_key, lowercase_secret):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_container_log_redactor_preserves_json_structure_and_is_idempotent() -> None:
    from app.cli.redact_registration_e2e_output import redact_text

    value = '{"EVALUATION_TOOL_SERVICE_TOKEN":"json-service-token"}\n'

    redacted = redact_text(value)

    assert redacted == '{"EVALUATION_TOOL_SERVICE_TOKEN":[REDACTED]}\n'
    assert redact_text(redacted) == redacted


def test_operations_doc_defines_the_local_container_evidence_boundary() -> None:
    assert OPERATIONS_DOC.is_file()
    source = OPERATIONS_DOC.read_text(encoding="utf-8")
    for required in (
        "scripts/run-local-container-acceptance.sh",
        "compose.yaml",
        "compose.e2e.yaml",
        "esports-erp-container-acceptance-",
        "127.0.0.1",
        "0700",
        "0600",
        "--volumes",
        "--remove-orphans",
        "Docker daemon",
        "docker-compose",
        "e2e_database_safety",
        "COMPOSE_DISABLE_ENV_FILE",
        ".env",
        "宿主环境",
        "逐项状态",
        "source_tree_sha256",
        "脱敏",
        "超时",
        "不运行浏览器",
        "不使用 compose.prod.yaml",
    ):
        assert required in source
