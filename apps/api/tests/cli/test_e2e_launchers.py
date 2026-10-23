from __future__ import annotations

import base64
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
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
EVALUATION_PAGE_LAUNCHER = REPOSITORY_ROOT / "scripts/run-evaluation-page-e2e.sh"
LAUNCHERS = (
    HOUSING_ROOM_STATUS_LAUNCHER,
    HOUSING_OCCUPANCY_LAUNCHER,
    HOUSING_REPORTING_LAUNCHER,
    EVALUATION_PAGE_LAUNCHER,
)
TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")


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
        "grep",
        "mktemp",
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
        (HOUSING_ROOM_STATUS_LAUNCHER, 2, 4, 4, 0),
        (HOUSING_OCCUPANCY_LAUNCHER, 2, 8, 8, 0),
        (HOUSING_REPORTING_LAUNCHER, 2, 8, 8, 8),
        (EVALUATION_PAGE_LAUNCHER, 3, 1, 1, 0),
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
    assert (
        python_commands.count("-m app.cli.seed_housing_reporting_e2e")
        == expected_reporting_seed_runs
    )


@pytest.mark.parametrize(
    "launcher",
    [
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
