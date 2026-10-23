from __future__ import annotations

import base64
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from app.cli.e2e_port_safety import require_local_port_free

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
HOUSING_ROOM_STATUS_LAUNCHER = (
    REPOSITORY_ROOT / "scripts/run-housing-room-status-e2e.sh"
)
HOUSING_OCCUPANCY_LAUNCHER = (
    REPOSITORY_ROOT / "scripts/run-housing-occupancy-e2e.sh"
)
HOUSING_REPORTING_LAUNCHER = (
    REPOSITORY_ROOT / "scripts/run-housing-reporting-e2e.sh"
)
HOUSING_CARE_LAUNCHER = REPOSITORY_ROOT / "scripts/run-housing-care-e2e.sh"
EVALUATION_PAGE_LAUNCHER = REPOSITORY_ROOT / "scripts/run-evaluation-page-e2e.sh"
SYSTEM_EVALUATION_LAUNCHER = REPOSITORY_ROOT / "scripts/run-system-evaluation-e2e.sh"
EVALUATION_V2_CONTRACT_CHECKER = (
    REPOSITORY_ROOT / "scripts/check-evaluation-v2-contract.sh"
)
LAUNCHERS = (
    HOUSING_CARE_LAUNCHER,
    HOUSING_ROOM_STATUS_LAUNCHER,
    HOUSING_OCCUPANCY_LAUNCHER,
    HOUSING_REPORTING_LAUNCHER,
    EVALUATION_PAGE_LAUNCHER,
)
TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")


def _initialize_contract_worktree(root: Path, fixture_path: str, body: str) -> None:
    target = root / fixture_path
    target.parent.mkdir(parents=True)
    target.write_text(body, encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_v2_contract_checker_requires_absolute_git_roots_and_exact_bytes(
    tmp_path: Path,
) -> None:
    erp_root = tmp_path / "erp"
    score_root = tmp_path / "score"
    body = '{"api_version":"v2"}\n'
    _initialize_contract_worktree(
        erp_root,
        "apps/api/tests/contracts/evaluation_integration_v2.json",
        body,
    )
    _initialize_contract_worktree(
        score_root,
        "tests/contracts/evaluation_integration_v2.json",
        body,
    )

    matched = subprocess.run(
        [str(EVALUATION_V2_CONTRACT_CHECKER), str(erp_root), str(score_root)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert matched.returncode == 0
    assert matched.stdout == "v2 评价契约逐字节一致\n"
    assert matched.stderr == ""

    relative = subprocess.run(
        [str(EVALUATION_V2_CONTRACT_CHECKER), "erp", str(score_root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert relative.returncode == 2
    assert relative.stdout == ""
    assert relative.stderr == "必须提供两个绝对 Git 工作树根目录\n"


def test_v2_contract_checker_rejects_non_git_or_mismatched_roots(
    tmp_path: Path,
) -> None:
    erp_root = tmp_path / "erp"
    score_root = tmp_path / "score"
    _initialize_contract_worktree(
        erp_root,
        "apps/api/tests/contracts/evaluation_integration_v2.json",
        '{"api_version":"v2"}\n',
    )
    score_contract = score_root / "tests/contracts/evaluation_integration_v2.json"
    score_contract.parent.mkdir(parents=True)
    score_contract.write_text('{"api_version":"v1"}\n', encoding="utf-8")

    non_git = subprocess.run(
        [str(EVALUATION_V2_CONTRACT_CHECKER), str(erp_root), str(score_root)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert non_git.returncode == 2
    assert non_git.stderr == "评分工具目录不是 Git 工作树根目录\n"

    subprocess.run(
        ["git", "init", "--quiet", str(score_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    mismatch = subprocess.run(
        [str(EVALUATION_V2_CONTRACT_CHECKER), str(erp_root), str(score_root)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert mismatch.returncode == 1
    assert mismatch.stdout == ""
    assert mismatch.stderr == "v2 评价契约不一致\n"


def _unused_local_ports(count: int) -> list[int]:
    probes = [socket.socket(socket.AF_INET, socket.SOCK_STREAM) for _ in range(count)]
    try:
        for probe in probes:
            probe.bind(("127.0.0.1", 0))
        return [int(probe.getsockname()[1]) for probe in probes]
    finally:
        for probe in probes:
            probe.close()


def _path_without_lsof(tmp_path: Path) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in (
        "basename",
        "bash",
        "cmp",
        "curl",
        "dirname",
        "env",
        "git",
        "grep",
        "mktemp",
        "mkdir",
        "rm",
        "sleep",
        "tail",
    ):
        target = shutil.which(command)
        assert target is not None
        (bin_dir / command).symlink_to(target)
    return str(bin_dir)


def _python_wrapper(tmp_path: Path) -> tuple[Path, Path]:
    invocation_log = tmp_path / "python-invocations.log"
    wrapper = tmp_path / "python-wrapper"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${2:-}\" == app.cli.seed_housing_care_e2e ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && ( \"${2:-}\" == app.cli.e2e_database_safety "
        "|| \"${2:-}\" == app.cli.e2e_port_safety ) ]]; then\n"
        f"  exec {shlex.quote(os.fsdecode(os.fsencode(sys.executable)))} \"$@\"\n"
        "fi\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(invocation_log))}\n"
        "exit 91\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    return wrapper, invocation_log


def test_port_probe_accepts_a_reusable_port_with_only_time_wait_connections() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        with socket.create_connection(("127.0.0.1", port)) as client:
            connection, _ = listener.accept()
            with connection:
                connection.shutdown(socket.SHUT_WR)
                assert client.recv(1) == b""

    require_local_port_free(port)


@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda path: path.stem)
def test_launcher_starts_vite_directly_on_the_requested_port(
    launcher: Path,
) -> None:
    source = launcher.read_text(encoding="utf-8")

    assert '"$vite_bin" --host 127.0.0.1 --port "$web_port" --strictPort' in source
    assert "npm --prefix apps/web run dev" not in source


@pytest.mark.parametrize(
    ("launcher", "occupied_port_variable"),
    [
        (HOUSING_CARE_LAUNCHER, "E2E_API_PORT"),
        (HOUSING_CARE_LAUNCHER, "E2E_WEB_PORT"),
        (HOUSING_ROOM_STATUS_LAUNCHER, "E2E_API_PORT"),
        (HOUSING_ROOM_STATUS_LAUNCHER, "E2E_WEB_PORT"),
        (HOUSING_OCCUPANCY_LAUNCHER, "E2E_API_PORT"),
        (HOUSING_OCCUPANCY_LAUNCHER, "E2E_WEB_PORT"),
        (HOUSING_REPORTING_LAUNCHER, "E2E_API_PORT"),
        (HOUSING_REPORTING_LAUNCHER, "E2E_WEB_PORT"),
        (EVALUATION_PAGE_LAUNCHER, "E2E_API_PORT"),
        (EVALUATION_PAGE_LAUNCHER, "E2E_WEB_PORT"),
        (EVALUATION_PAGE_LAUNCHER, "E2E_SCORE_PORT"),
    ],
    ids=(
        "housing-care-api",
        "housing-care-web",
        "housing-room-api",
        "housing-room-web",
        "housing-occupancy-api",
        "housing-occupancy-web",
        "housing-reporting-api",
        "housing-reporting-web",
        "evaluation-api",
        "evaluation-web",
        "score",
    ),
)
def test_launcher_refuses_occupied_port_without_lsof_before_migration(
    tmp_path: Path,
    launcher: Path,
    occupied_port_variable: str,
) -> None:
    python_wrapper, invocation_log = _python_wrapper(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied_port = int(listener.getsockname()[1])
        free_ports = _unused_local_ports(3)
        configured_ports = {
            "E2E_API_PORT": str(free_ports[0]),
            "E2E_WEB_PORT": str(free_ports[1]),
            "E2E_SCORE_PORT": str(free_ports[2]),
            occupied_port_variable: str(occupied_port),
        }
        result = subprocess.run(
            [str(launcher)],
            cwd=REPOSITORY_ROOT,
            env={
                **os.environ,
                "PATH": _path_without_lsof(tmp_path),
                "E2E_PYTHON_BIN": str(python_wrapper),
                "E2E_VITE_BIN": str(python_wrapper),
                "E2E_SCORE_BIN": str(python_wrapper),
                **configured_ports,
                "SCORE_REPO": str(tmp_path),
                "TEST_DATABASE_URL": (
                    "postgresql+psycopg://erp:secret@127.0.0.1:55434/housing_test"
                ),
                "FIELD_ENCRYPTION_MASTER_KEY": TEST_KEY,
                "FIELD_LOOKUP_KEY": TEST_KEY,
                "SESSION_TOKEN_PEPPER": TEST_KEY,
                "EVALUATION_TOOL_SERVICE_TOKEN": (
                    "evaluation-local-service-token-2026-change-me"
                ),
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    invocations = (
        invocation_log.read_text(encoding="utf-8") if invocation_log.exists() else ""
    )
    assert result.returncode != 0
    assert f"本地端口已被其他进程占用: {occupied_port}" in result.stderr
    assert "-m alembic" not in invocations


def _run_launcher_against_old_server(
    tmp_path: Path,
    launcher: Path,
    service_count: int,
    *,
    child_lifetime_seconds: float,
) -> tuple[subprocess.CompletedProcess[str], str]:
    ports = _unused_local_ports(service_count)
    server_script = tmp_path / "old-health-server.py"
    ready_file = tmp_path / "old-health-server.ready"
    pid_file = tmp_path / "old-health-server.pid"
    server_script.write_text(
        "import signal, sys, threading\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
        "    def log_message(self, format, *args):\n"
        "        pass\n"
        "servers = [ThreadingHTTPServer(('127.0.0.1', int(port)), Handler) "
        "for port in sys.argv[2:]]\n"
        "for server in servers:\n"
        "    threading.Thread(target=server.serve_forever, daemon=True).start()\n"
        "open(sys.argv[1], 'w', encoding='utf-8').write('ready')\n"
        "signal.pause()\n",
        encoding="utf-8",
    )
    command_log = tmp_path / "commands.log"
    fake_process = tmp_path / "fake-process"
    fake_process.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(command_log))}\n"
        "if [[ \"$*\" == *\"test:e2e\"* ]]; then exit 23; fi\n"
        f"sleep {child_lifetime_seconds}\n"
        "exit 23\n",
        encoding="utf-8",
    )
    fake_process.chmod(0o700)

    safe_path = Path(_path_without_lsof(tmp_path))
    (safe_path / "npm").symlink_to(fake_process)
    score_repo = tmp_path / "score-repo"
    score_contract_dir = score_repo / "tests/contracts"
    score_contract_dir.mkdir(parents=True)
    shutil.copyfile(
        REPOSITORY_ROOT / "apps/api/tests/contracts/evaluation_integration_v1.json",
        score_contract_dir / "evaluation_integration_v1.json",
    )

    wrapper = tmp_path / "race-python-wrapper"
    quoted_python = shlex.quote(os.fsdecode(os.fsencode(sys.executable)))
    quoted_server = shlex.quote(str(server_script))
    quoted_ready = shlex.quote(str(ready_file))
    quoted_pid = shlex.quote(str(pid_file))
    quoted_ports = " ".join(shlex.quote(str(port)) for port in ports)
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${2:-}\" == app.cli.seed_housing_care_e2e ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == app.cli.e2e_database_safety ]]; then\n"
        f"  exec {quoted_python} \"$@\"\n"
        "fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == app.cli.e2e_port_safety ]]; then\n"
        f"  {quoted_python} \"$@\" || exit $?\n"
        f"  if [[ \"${{3:-}}\" == {shlex.quote(str(ports[-1]))} ]]; then\n"
        f"    {quoted_python} {quoted_server} {quoted_ready} {quoted_ports} "
        ">/dev/null 2>&1 &\n"
        f"    printf '%s\\n' \"$!\" > {quoted_pid}\n"
        "    for _ in {1..100}; do\n"
        f"      [[ -f {quoted_ready} ]] && exit 0\n"
        "      sleep 0.02\n"
        "    done\n"
        "    exit 94\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == alembic ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == app.cli.seed_e2e ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == uvicorn ]]; then\n"
        f"  sleep {child_lifetime_seconds}\n"
        "  exit 23\n"
        "fi\n"
        "exit 95\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)

    result: subprocess.CompletedProcess[str]
    try:
        result = subprocess.run(
            [str(launcher)],
            cwd=REPOSITORY_ROOT,
            env={
                **os.environ,
                "PATH": str(safe_path),
                "E2E_PYTHON_BIN": str(wrapper),
                "E2E_VITE_BIN": str(fake_process),
                "E2E_SCORE_BIN": str(fake_process),
                "E2E_API_PORT": str(ports[0]),
                "E2E_WEB_PORT": str(ports[1]),
                "E2E_SCORE_PORT": str(ports[-1]),
                "SCORE_REPO": str(score_repo),
                "TEST_DATABASE_URL": (
                    "postgresql+psycopg://erp:secret@127.0.0.1:55434/housing_test"
                ),
                "FIELD_ENCRYPTION_MASTER_KEY": TEST_KEY,
                "FIELD_LOOKUP_KEY": TEST_KEY,
                "SESSION_TOKEN_PEPPER": TEST_KEY,
                "EVALUATION_TOOL_SERVICE_TOKEN": (
                    "evaluation-local-service-token-2026-change-me"
                ),
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    finally:
        if pid_file.exists():
            with suppress(ProcessLookupError):
                os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)

    commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
    return result, commands


@pytest.mark.parametrize(
    ("launcher", "service_count"),
    [
        (HOUSING_CARE_LAUNCHER, 2),
        (HOUSING_ROOM_STATUS_LAUNCHER, 2),
        (HOUSING_OCCUPANCY_LAUNCHER, 2),
        (HOUSING_REPORTING_LAUNCHER, 2),
        (EVALUATION_PAGE_LAUNCHER, 3),
    ],
    ids=lambda value: value.stem if isinstance(value, Path) else None,
)
def test_launcher_does_not_accept_old_healthy_server_after_child_exits(
    tmp_path: Path,
    launcher: Path,
    service_count: int,
) -> None:
    result, commands = _run_launcher_against_old_server(
        tmp_path,
        launcher,
        service_count,
        child_lifetime_seconds=0,
    )

    assert result.returncode != 0
    assert "进程在就绪前退出" in result.stderr
    assert "test:e2e" not in commands


@pytest.mark.parametrize(
    ("launcher", "service_count"),
    [
        (HOUSING_CARE_LAUNCHER, 2),
        (HOUSING_ROOM_STATUS_LAUNCHER, 2),
        (HOUSING_OCCUPANCY_LAUNCHER, 2),
        (HOUSING_REPORTING_LAUNCHER, 2),
        (EVALUATION_PAGE_LAUNCHER, 3),
    ],
    ids=lambda value: value.stem if isinstance(value, Path) else None,
)
def test_launcher_does_not_accept_old_healthy_server_while_child_is_alive(
    tmp_path: Path,
    launcher: Path,
    service_count: int,
) -> None:
    result, commands = _run_launcher_against_old_server(
        tmp_path,
        launcher,
        service_count,
        child_lifetime_seconds=0.8,
    )

    assert result.returncode != 0
    assert "进程在就绪前退出" in result.stderr
    assert "test:e2e" not in commands


@pytest.mark.parametrize(
    (
        "launcher",
        "service_count",
        "expected_e2e_runs",
        "expected_seed_runs",
        "expected_reporting_seed_runs",
    ),
    [
        (HOUSING_CARE_LAUNCHER, 2, 5, 0, 0),
        (HOUSING_ROOM_STATUS_LAUNCHER, 2, 4, 4, 0),
        (HOUSING_OCCUPANCY_LAUNCHER, 2, 20, 16, 0),
        (HOUSING_REPORTING_LAUNCHER, 2, 8, 8, 8),
        (EVALUATION_PAGE_LAUNCHER, 3, 8, 8, 0),
    ],
    ids=lambda value: value.stem if isinstance(value, Path) else None,
)
def test_launcher_accepts_bind_markers_from_its_healthy_children(
    tmp_path: Path,
    launcher: Path,
    service_count: int,
    expected_e2e_runs: int,
    expected_seed_runs: int,
    expected_reporting_seed_runs: int,
) -> None:
    ports = _unused_local_ports(service_count)
    server_script = tmp_path / "owned-health-server.py"
    server_script.write_text(
        "import sys\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
        "    def log_message(self, format, *args):\n"
        "        pass\n"
        "mode = sys.argv[1]\n"
        "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "server = ThreadingHTTPServer(('127.0.0.1', port), Handler)\n"
        "if mode == 'vite':\n"
        "    print(f'  Local:   http://127.0.0.1:{port}/', flush=True)\n"
        "else:\n"
        "    print(\n"
        "        f'INFO: Uvicorn running on http://127.0.0.1:{port} '\n"
        "        '(Press CTRL+C to quit)',\n"
        "        flush=True,\n"
        "    )\n"
        "server.serve_forever()\n",
        encoding="utf-8",
    )

    command_log = tmp_path / "commands.log"
    fake_service = tmp_path / "fake-service"
    quoted_python = shlex.quote(os.fsdecode(os.fsencode(sys.executable)))
    quoted_server = shlex.quote(str(server_script))
    fake_service.write_text(
        "#!/usr/bin/env bash\n"
        "mode=vite\n"
        "if [[ \"${1:-}\" == main:app ]]; then mode=uvicorn; fi\n"
        f"exec {quoted_python} {quoted_server} \"$mode\" \"$@\"\n",
        encoding="utf-8",
    )
    fake_service.chmod(0o700)

    safe_path = Path(_path_without_lsof(tmp_path))
    fake_npm = safe_path / "npm"
    fake_npm.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(command_log))}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o700)

    score_repo = tmp_path / "score-repo"
    score_contract_dir = score_repo / "tests/contracts"
    score_contract_dir.mkdir(parents=True)
    shutil.copyfile(
        REPOSITORY_ROOT / "apps/api/tests/contracts/evaluation_integration_v1.json",
        score_contract_dir / "evaluation_integration_v1.json",
    )

    python_command_log = tmp_path / "python-commands.log"
    wrapper = tmp_path / "owned-python-wrapper"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(python_command_log))}\n"
        "if [[ \"${2:-}\" == app.cli.seed_housing_care_e2e ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && ( \"${2:-}\" == app.cli.e2e_database_safety "
        "|| \"${2:-}\" == app.cli.e2e_port_safety ) ]]; then\n"
        f"  exec {quoted_python} \"$@\"\n"
        "fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == alembic ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == app.cli.seed_e2e ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && "
        "\"${2:-}\" == app.cli.seed_housing_reporting_e2e ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == uvicorn ]]; then\n"
        f"  exec {quoted_python} {quoted_server} uvicorn \"$@\"\n"
        "fi\n"
        "exit 95\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)

    result = subprocess.run(
        [str(launcher)],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "PATH": str(safe_path),
            "E2E_PYTHON_BIN": str(wrapper),
            "E2E_VITE_BIN": str(fake_service),
            "E2E_SCORE_BIN": str(fake_service),
            "E2E_API_PORT": str(ports[0]),
            "E2E_WEB_PORT": str(ports[1]),
            "E2E_SCORE_PORT": str(ports[-1]),
            "SCORE_REPO": str(score_repo),
            "TEST_DATABASE_URL": (
                "postgresql+psycopg://erp:secret@127.0.0.1:55434/housing_test"
            ),
            "FIELD_ENCRYPTION_MASTER_KEY": TEST_KEY,
            "FIELD_LOOKUP_KEY": TEST_KEY,
            "SESSION_TOKEN_PEPPER": TEST_KEY,
            "EVALUATION_TOOL_SERVICE_TOKEN": (
                "evaluation-local-service-token-2026-change-me"
            ),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    commands = command_log.read_text(encoding="utf-8")
    python_commands = python_command_log.read_text(encoding="utf-8").splitlines()
    assert result.returncode == 0, result.stderr
    assert commands.count("test:e2e") == expected_e2e_runs
    assert python_commands.count("-m app.cli.seed_e2e") == expected_seed_runs
    if launcher == HOUSING_CARE_LAUNCHER:
        assert python_commands.count("-m app.cli.seed_housing_care_e2e") == 5
        assert python_commands.count("-m app.cli.seed_housing_care_e2e --check-database") == 1
    expected_void_seeds = 4 if launcher == HOUSING_OCCUPANCY_LAUNCHER else 0
    assert python_commands.count("-m app.cli.seed_e2e --housing-history-void") == expected_void_seeds
    if launcher == HOUSING_OCCUPANCY_LAUNCHER:
        spec_names = (
            "housing-occupancy-lifecycle.spec.ts",
            "housing-occupancy-revocation.spec.ts",
            "profile-housing-edit.spec.ts",
            "person-housing.spec.ts",
            "housing-history-void.spec.ts",
        )
        expected_commands = [
            f"--prefix apps/web run test:e2e -- {spec} --project={project}"
            for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360")
            for spec in spec_names
        ]
        assert commands.splitlines() == expected_commands
    assert (
        python_commands.count("-m app.cli.seed_housing_reporting_e2e")
        == expected_reporting_seed_runs
    )


@pytest.mark.parametrize(
    "launcher",
    [
        HOUSING_CARE_LAUNCHER,
        HOUSING_ROOM_STATUS_LAUNCHER,
        HOUSING_OCCUPANCY_LAUNCHER,
        HOUSING_REPORTING_LAUNCHER,
    ],
    ids=lambda path: path.stem,
)
def test_launcher_rejects_bare_vite_url_from_child_that_did_not_bind(
    tmp_path: Path,
    launcher: Path,
) -> None:
    api_port, web_port = _unused_local_ports(2)
    server_script = tmp_path / "health-server.py"
    ready_file = tmp_path / "old-web.ready"
    pid_file = tmp_path / "old-web.pid"
    server_script.write_text(
        "import sys\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
        "    def log_message(self, format, *args):\n"
        "        pass\n"
        "port = int(sys.argv[1])\n"
        "server = ThreadingHTTPServer(('127.0.0.1', port), Handler)\n"
        "if len(sys.argv) == 3:\n"
        "    open(sys.argv[2], 'w', encoding='utf-8').write('ready')\n"
        "else:\n"
        "    print(\n"
        "        f'INFO: Uvicorn running on http://127.0.0.1:{port} '\n"
        "        '(Press CTRL+C to quit)',\n"
        "        flush=True,\n"
        "    )\n"
        "server.serve_forever()\n",
        encoding="utf-8",
    )
    bare_vite_script = tmp_path / "bare-vite.py"
    bare_vite_script.write_text(
        "import sys, time\n"
        "port = sys.argv[sys.argv.index('--port') + 1]\n"
        "print(f'http://127.0.0.1:{port}/', flush=True)\n"
        "time.sleep(0.8)\n"
        "raise SystemExit(23)\n",
        encoding="utf-8",
    )

    command_log = tmp_path / "commands.log"
    quoted_python = shlex.quote(os.fsdecode(os.fsencode(sys.executable)))
    quoted_server = shlex.quote(str(server_script))
    quoted_bare_vite = shlex.quote(str(bare_vite_script))
    fake_vite = tmp_path / "fake-vite"
    fake_vite.write_text(
        "#!/usr/bin/env bash\n"
        f"exec {quoted_python} {quoted_bare_vite} \"$@\"\n",
        encoding="utf-8",
    )
    fake_vite.chmod(0o700)

    safe_path = Path(_path_without_lsof(tmp_path))
    fake_npm = safe_path / "npm"
    fake_npm.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(command_log))}\n"
        "exit 23\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o700)

    wrapper = tmp_path / "web-race-python-wrapper"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${2:-}\" == app.cli.seed_housing_care_e2e ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == app.cli.e2e_database_safety ]]; then\n"
        f"  exec {quoted_python} \"$@\"\n"
        "fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == app.cli.e2e_port_safety ]]; then\n"
        f"  {quoted_python} \"$@\" || exit $?\n"
        f"  if [[ \"${{3:-}}\" == {shlex.quote(str(web_port))} ]]; then\n"
        f"    {quoted_python} {quoted_server} {web_port} {shlex.quote(str(ready_file))} "
        ">/dev/null 2>&1 &\n"
        f"    printf '%s\\n' \"$!\" > {shlex.quote(str(pid_file))}\n"
        "    for _ in {1..100}; do\n"
        f"      [[ -f {shlex.quote(str(ready_file))} ]] && exit 0\n"
        "      sleep 0.02\n"
        "    done\n"
        "    exit 94\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == alembic ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == app.cli.seed_e2e ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == uvicorn ]]; then\n"
        f"  exec {quoted_python} {quoted_server} {api_port}\n"
        "fi\n"
        "exit 95\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)

    try:
        result = subprocess.run(
            [str(launcher)],
            cwd=REPOSITORY_ROOT,
            env={
                **os.environ,
                "PATH": str(safe_path),
                "E2E_PYTHON_BIN": str(wrapper),
                "E2E_VITE_BIN": str(fake_vite),
                "E2E_API_PORT": str(api_port),
                "E2E_WEB_PORT": str(web_port),
                "TEST_DATABASE_URL": (
                    "postgresql+psycopg://erp:secret@127.0.0.1:55434/housing_test"
                ),
                "FIELD_ENCRYPTION_MASTER_KEY": TEST_KEY,
                "FIELD_LOOKUP_KEY": TEST_KEY,
                "SESSION_TOKEN_PEPPER": TEST_KEY,
                "EVALUATION_TOOL_SERVICE_TOKEN": (
                    "evaluation-local-service-token-2026-change-me"
                ),
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    finally:
        if pid_file.exists():
            with suppress(ProcessLookupError):
                os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)

    commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
    assert result.returncode != 0
    assert "Web 进程在就绪前退出" in result.stderr
    assert "test:e2e" not in commands


def _system_evaluation_launcher_environment(
    tmp_path: Path,
    *,
    worker_mode: str = "ready",
) -> tuple[dict[str, str], Path, Path, Path]:
    assert SYSTEM_EVALUATION_LAUNCHER.is_file(), (
        "system-evaluation E2E launcher has not been implemented"
    )
    ports = _unused_local_ports(3)
    server_script = tmp_path / "owned-system-evaluation-server.py"
    server_script.write_text(
        "import sys\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
        "    def log_message(self, format, *args):\n"
        "        pass\n"
        "mode = sys.argv[1]\n"
        "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "server = ThreadingHTTPServer(('127.0.0.1', port), Handler)\n"
        "if mode == 'vite':\n"
        "    print(f'  Local:   http://127.0.0.1:{port}/', flush=True)\n"
        "else:\n"
        "    print(\n"
        "        f'INFO: Uvicorn running on http://127.0.0.1:{port} '\n"
        "        '(Press CTRL+C to quit)',\n"
        "        flush=True,\n"
        "    )\n"
        "server.serve_forever()\n",
        encoding="utf-8",
    )

    command_log = tmp_path / "system-evaluation-commands.log"
    python_log = tmp_path / "system-evaluation-python.log"
    worker_pid_file = tmp_path / "score-worker.pid"
    quoted_python = shlex.quote(os.fsdecode(os.fsencode(sys.executable)))
    quoted_server = shlex.quote(str(server_script))
    quoted_command_log = shlex.quote(str(command_log))
    quoted_python_log = shlex.quote(str(python_log))
    quoted_worker_pid = shlex.quote(str(worker_pid_file))

    erp_python = tmp_path / "system-evaluation-erp-python"
    erp_python.write_text(
        "#!/usr/bin/env bash\n"
        f"export PYTHONPATH={shlex.quote(str(REPOSITORY_ROOT / 'apps/api'))}\n"
        f"printf '%s\\n' \"$*\" >> {quoted_python_log}\n"
        f"if [[ \"${{1:-}}\" == -c ]]; then exec {quoted_python} \"$@\"; fi\n"
        "if [[ \"${1:-}\" == -m && ( \"${2:-}\" == app.cli.e2e_database_safety "
        "|| \"${2:-}\" == app.cli.e2e_port_safety ) ]]; then\n"
        f"  exec {quoted_python} \"$@\"\n"
        "fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == app.evaluations.media_store ]]; then\n"
        "  if [[ \"${FAKE_IDENTITY_INIT_FAILURE:-}\" == 1 ]]; then exit 97; fi\n"
        f"  exec {quoted_python} \"$@\"\n"
        "fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == alembic ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && ( \"${2:-}\" == app.cli.seed_e2e "
        "|| \"${2:-}\" == app.cli.seed_system_evaluation_e2e ) ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == uvicorn ]]; then\n"
        f"  {quoted_python} -m app.evaluations.media_store --verify-store-identity || exit $?\n"
        f"  printf 'api|%s|%s|%s\\n' \"${{EVALUATION_TOOL_SERVICE_TOKEN:-}}\" "
        "\"${EVALUATION_TOOL_WEB_SERVICE_TOKEN:-}\" "
        f"\"${{EVALUATION_TOOL_WORKER_SERVICE_TOKEN:-}}\" >> {quoted_command_log}\n"
        f"  exec {quoted_python} {quoted_server} uvicorn \"$@\"\n"
        "fi\n"
        "exit 95\n",
        encoding="utf-8",
    )
    erp_python.chmod(0o700)

    fake_vite = tmp_path / "system-evaluation-vite"
    fake_vite.write_text(
        "#!/usr/bin/env bash\n"
        f"exec {quoted_python} {quoted_server} vite \"$@\"\n",
        encoding="utf-8",
    )
    fake_vite.chmod(0o700)

    score_python = tmp_path / "system-evaluation-score-python"
    score_python.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == -m && \"${2:-}\" == uvicorn ]]; then\n"
        f"  printf 'score-web|%s|%s|%s\\n' \"${{ERP_SERVICE_TOKEN:-}}\" "
        "\"${ERP_WEB_SERVICE_TOKEN:-}\" \"${ERP_WORKER_SERVICE_TOKEN:-}\" "
        f">> {quoted_command_log}\n"
        f"  exec {quoted_python} {quoted_server} uvicorn \"$@\"\n"
        "fi\n"
        "if [[ \"${1##*/}\" == worker.py && \" $* \" == *\" --check \"* ]]; then\n"
        f"  printf 'worker-check|%s|%s|%s\\n' \"${{ERP_SERVICE_TOKEN:-}}\" "
        "\"${ERP_WEB_SERVICE_TOKEN:-}\" \"${ERP_WORKER_SERVICE_TOKEN:-}\" "
        f">> {quoted_command_log}\n"
        "  case \"${FAKE_WORKER_MODE:-ready}\" in\n"
        "    ready|worker-exits|ignore-term) printf 'score-worker ready\\n'; exit 0 ;;\n"
        "    near-marker) printf 'score-worker ready stale\\n'; exit 0 ;;\n"
        "    failed-check) printf 'score-worker ready\\n'; exit 42 ;;\n"
        "    *) exit 43 ;;\n"
        "  esac\n"
        "fi\n"
        "if [[ \"${1##*/}\" == worker.py ]]; then\n"
        f"  printf 'worker|%s|%s|%s\\n' \"${{ERP_SERVICE_TOKEN:-}}\" "
        "\"${ERP_WEB_SERVICE_TOKEN:-}\" \"${ERP_WORKER_SERVICE_TOKEN:-}\" "
        f">> {quoted_command_log}\n"
        f"  printf '%s\\n' \"$$\" > {quoted_worker_pid}\n"
        "  printf 'score-worker ready\\n'\n"
        "  if [[ \"${FAKE_WORKER_MODE:-ready}\" == worker-exits ]]; then exit 23; fi\n"
        "  if [[ \"${FAKE_WORKER_MODE:-ready}\" == ignore-term ]]; then\n"
        "    trap '' TERM\n"
        "    while :; do sleep 1; done\n"
        "  fi\n"
        "  exec sleep 30\n"
        "fi\n"
        "exit 96\n",
        encoding="utf-8",
    )
    score_python.chmod(0o700)

    score_root = tmp_path / "score-root"
    contract_body = (
        REPOSITORY_ROOT
        / "apps/api/tests/contracts/evaluation_integration_v2.json"
    ).read_text(encoding="utf-8")
    _initialize_contract_worktree(
        score_root,
        "tests/contracts/evaluation_integration_v2.json",
        contract_body,
    )
    (score_root / "worker.py").write_text("# launcher fixture\n", encoding="utf-8")

    safe_path = Path(_path_without_lsof(tmp_path))
    fake_npm = safe_path / "npm"
    fake_npm.write_text(
        "#!/usr/bin/env bash\n"
        f"printf 'npm|%s|%s|%s\\n' \"$*\" "
        "\"${E2E_SYSTEM_EVALUATION_AUTOMATIC_PNG:-}\" "
        "\"${E2E_SYSTEM_EVALUATION_MANUAL_PNG:-}\" "
        f">> {quoted_command_log}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o700)

    service_token = "evaluation-v1-service-token-for-system-e2e"
    web_token = "evaluation-web-service-token-for-system-e2e"
    worker_token = "evaluation-worker-service-token-for-system-e2e"
    return (
        {
            **os.environ,
            "PATH": str(safe_path),
            "E2E_PYTHON_BIN": str(erp_python),
            "E2E_VITE_BIN": str(fake_vite),
            "E2E_SCORE_PYTHON_BIN": str(score_python),
            "E2E_API_PORT": str(ports[0]),
            "E2E_WEB_PORT": str(ports[1]),
            "E2E_SCORE_PORT": str(ports[2]),
            "SCORE_REPO": str(score_root),
            "TEST_DATABASE_URL": (
                "postgresql+psycopg://erp:secret@127.0.0.1:55434/housing_test"
            ),
            "FIELD_ENCRYPTION_MASTER_KEY": TEST_KEY,
            "FIELD_LOOKUP_KEY": TEST_KEY,
            "SESSION_TOKEN_PEPPER": TEST_KEY,
            "EVALUATION_TOOL_SERVICE_TOKEN": service_token,
            "EVALUATION_TOOL_WEB_SERVICE_TOKEN": web_token,
            "EVALUATION_TOOL_WORKER_SERVICE_TOKEN": worker_token,
            "FAKE_WORKER_MODE": worker_mode,
        },
        command_log,
        python_log,
        worker_pid_file,
    )


def _system_evaluation_default_sibling_worktrees(
    tmp_path: Path,
    score_source: Path,
) -> tuple[Path, Path]:
    score_root = tmp_path / "game-score-system-evaluation-processing"
    score_source.rename(score_root)

    erp_root = tmp_path / "erp-root"
    contract_body = (
        REPOSITORY_ROOT / "apps/api/tests/contracts/evaluation_integration_v2.json"
    ).read_text(encoding="utf-8")
    _initialize_contract_worktree(
        erp_root,
        "apps/api/tests/contracts/evaluation_integration_v2.json",
        contract_body,
    )
    (erp_root / "apps/web").mkdir(parents=True)
    scripts_root = erp_root / "scripts"
    scripts_root.mkdir()
    launcher = scripts_root / SYSTEM_EVALUATION_LAUNCHER.name
    contract_checker = scripts_root / EVALUATION_V2_CONTRACT_CHECKER.name
    shutil.copy2(SYSTEM_EVALUATION_LAUNCHER, launcher)
    shutil.copy2(EVALUATION_V2_CONTRACT_CHECKER, contract_checker)
    launcher.chmod(0o700)
    contract_checker.chmod(0o700)
    return launcher, erp_root


def test_system_evaluation_launcher_initializes_identity_before_api_start(tmp_path: Path) -> None:
    env, command_log, _, _ = _system_evaluation_launcher_environment(tmp_path)
    result = subprocess.run(
        [str(SYSTEM_EVALUATION_LAUNCHER)], cwd=REPOSITORY_ROOT, env=env,
        capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "npm|" in command_log.read_text(encoding="utf-8")


def test_system_evaluation_launcher_stops_when_identity_initialization_fails(tmp_path: Path) -> None:
    env, command_log, python_log, _ = _system_evaluation_launcher_environment(tmp_path)
    env["FAKE_IDENTITY_INIT_FAILURE"] = "1"
    result = subprocess.run(
        [str(SYSTEM_EVALUATION_LAUNCHER)], cwd=REPOSITORY_ROOT, env=env,
        capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 97
    assert "-m uvicorn" not in python_log.read_text(encoding="utf-8")
    assert not command_log.exists()


def test_system_evaluation_launcher_defaults_to_shared_venv_in_linked_worktree(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    (primary / "scripts").mkdir(parents=True)
    (primary / "apps/api").mkdir(parents=True)
    shutil.copyfile(
        SYSTEM_EVALUATION_LAUNCHER,
        primary / "scripts/run-system-evaluation-e2e.sh",
    )
    (primary / "scripts/run-system-evaluation-e2e.sh").chmod(0o700)
    (primary / "apps/api/.keep").write_text("fixture\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet", str(primary)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(primary), "add", "scripts", "apps/api/.keep"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(primary),
            "-c",
            "user.name=E2E Test",
            "-c",
            "user.email=e2e@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", "--quiet", str(linked)],
        check=True,
        capture_output=True,
        text=True,
    )

    invocation_log = tmp_path / "shared-python.log"
    shared_python = primary / ".venv/bin/python"
    shared_python.parent.mkdir(parents=True)
    shared_python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(invocation_log))}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    shared_python.chmod(0o700)

    env = {**os.environ}
    env.pop("E2E_PYTHON_BIN", None)
    env["TEST_DATABASE_URL"] = (
        "postgresql+psycopg://erp:secret@127.0.0.1:55434/housing_test"
    )
    result = subprocess.run(
        [str(linked / "scripts/run-system-evaluation-e2e.sh")],
        cwd=linked,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode != 0
    assert "本地 ERP Python 虚拟环境不可用" not in result.stderr
    assert invocation_log.read_text(encoding="utf-8").strip() == (
        "-m app.cli.e2e_database_safety"
    )


@pytest.mark.parametrize(
    ("changed_name", "source_name"),
    [
        ("EVALUATION_TOOL_SERVICE_TOKEN", None),
        ("EVALUATION_TOOL_WEB_SERVICE_TOKEN", None),
        ("EVALUATION_TOOL_WORKER_SERVICE_TOKEN", None),
        ("EVALUATION_TOOL_WEB_SERVICE_TOKEN", "EVALUATION_TOOL_SERVICE_TOKEN"),
        ("EVALUATION_TOOL_WORKER_SERVICE_TOKEN", "EVALUATION_TOOL_SERVICE_TOKEN"),
        ("EVALUATION_TOOL_WORKER_SERVICE_TOKEN", "EVALUATION_TOOL_WEB_SERVICE_TOKEN"),
    ],
)
def test_system_evaluation_launcher_refuses_missing_or_reused_secrets_before_migration(
    tmp_path: Path,
    changed_name: str,
    source_name: str | None,
) -> None:
    env, _, python_log, _ = _system_evaluation_launcher_environment(tmp_path)
    if source_name is None:
        env.pop(changed_name)
    else:
        env[changed_name] = env[source_name]

    result = subprocess.run(
        [str(SYSTEM_EVALUATION_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    invocations = python_log.read_text(encoding="utf-8") if python_log.exists() else ""
    assert result.returncode != 0
    assert "-m alembic" not in invocations
    assert "app.cli.seed_system_evaluation_e2e" not in invocations


@pytest.mark.parametrize(
    "port_name",
    ["E2E_API_PORT", "E2E_WEB_PORT", "E2E_SCORE_PORT"],
)
def test_system_evaluation_launcher_refuses_occupied_ports_before_migration(
    tmp_path: Path,
    port_name: str,
) -> None:
    env, _, python_log, _ = _system_evaluation_launcher_environment(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied_port = int(listener.getsockname()[1])
        env[port_name] = str(occupied_port)
        result = subprocess.run(
            [str(SYSTEM_EVALUATION_LAUNCHER)],
            cwd=REPOSITORY_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    invocations = python_log.read_text(encoding="utf-8")
    assert result.returncode != 0
    assert f"本地端口已被其他进程占用: {occupied_port}" in result.stderr
    assert "-m alembic" not in invocations


@pytest.mark.parametrize("worker_mode", ["failed-check", "near-marker", "worker-exits"])
def test_system_evaluation_launcher_requires_live_worker_and_fresh_exact_marker(
    tmp_path: Path,
    worker_mode: str,
) -> None:
    env, command_log, _, _ = _system_evaluation_launcher_environment(
        tmp_path,
        worker_mode=worker_mode,
    )

    result = subprocess.run(
        [str(SYSTEM_EVALUATION_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
    assert result.returncode != 0
    assert "npm|" not in commands
    assert "Worker" in result.stderr or "worker" in result.stderr


def test_system_evaluation_launcher_tracks_services_and_runs_all_four_viewports(
    tmp_path: Path,
) -> None:
    env, command_log, python_log, worker_pid_file = (
        _system_evaluation_launcher_environment(tmp_path)
    )

    result = subprocess.run(
        [str(SYSTEM_EVALUATION_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    commands = command_log.read_text(encoding="utf-8")
    python_commands = python_log.read_text(encoding="utf-8").splitlines()
    assert result.returncode == 0, result.stderr
    assert commands.count("npm|") == 8
    for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360"):
        assert commands.count(f"--project={project}") == 2
    assert commands.count("system-evaluation-processing.spec.ts") == 4
    assert commands.count("system-evaluation-privacy.spec.ts") == 4
    assert commands.count("/.system-evaluation-e2e/automatic.png") == 8
    assert commands.count("/.system-evaluation-e2e/manual.png") == 8
    assert python_commands.count("-m app.cli.seed_e2e") == 8
    assert python_commands.count("-m app.cli.seed_system_evaluation_e2e") == 8

    service_token = env["EVALUATION_TOOL_SERVICE_TOKEN"]
    web_token = env["EVALUATION_TOOL_WEB_SERVICE_TOKEN"]
    worker_token = env["EVALUATION_TOOL_WORKER_SERVICE_TOKEN"]
    assert f"api|{service_token}|{web_token}|{worker_token}" in commands
    assert f"score-web|{service_token}|{web_token}|" in commands
    assert f"worker|||{worker_token}" in commands
    assert f"worker-check|||{worker_token}" in commands

    assert worker_pid_file.exists()
    worker_pid = int(worker_pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


def test_system_evaluation_launcher_bounds_cleanup_for_term_ignoring_worker(
    tmp_path: Path,
) -> None:
    env, _, _, worker_pid_file = _system_evaluation_launcher_environment(
        tmp_path,
        worker_mode="ignore-term",
    )

    started = time.monotonic()
    result = subprocess.run(
        [str(SYSTEM_EVALUATION_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 8
    worker_pid = int(worker_pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


def test_system_evaluation_launcher_defaults_to_sibling_score_worktree(
    tmp_path: Path,
) -> None:
    env, command_log, _, _ = _system_evaluation_launcher_environment(tmp_path)
    score_source = Path(env.pop("SCORE_REPO"))
    launcher, erp_root = _system_evaluation_default_sibling_worktrees(
        tmp_path,
        score_source,
    )

    result = subprocess.run(
        [str(launcher)],
        cwd=erp_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
    assert result.returncode == 0, result.stderr
    assert commands.count("npm|") == 8
