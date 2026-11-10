"""Private, owned-process browser acceptance on an already validated test DB."""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from app.cli.departure_e2e_runtime import DepartureRuntimeRefused, require_departure_database
from app.cli.e2e_credentials import E2E_CREDENTIALS
from app.core.config import Settings

PROJECTS = ("desktop-1440", "tablet-768", "mobile-360")
__all__ = ["DepartureRuntimeRefused", "run_browser"]


def _safe_summary(value: str) -> str:
    for credential in E2E_CREDENTIALS.values():
        for private in (credential.identifier, credential.password):
            value = value.replace(private, "[redacted]")
    for key, private in os.environ.items():
        if private and any(marker in key for marker in ("TOKEN", "PEPPER", "KEY", "DATABASE_URL")):
            value = value.replace(private, "[redacted]")
    return re.sub(r"postgres(?:ql)?(?:\+\w+)?://\S+", "[redacted-dsn]", value)[:400]


def _report_command_failure(process: OwnedProcess, role: str) -> None:
    # Only a one-line exception cause, never traceback locals/SQL parameters.
    lines = process.log.read_text(errors="replace").splitlines()
    causes = [line for line in lines if re.match(r"^[\w.]+(?:Error|Refused|Violation):", line)]
    print(f"Departure {role} failed: {_safe_summary(causes[-1]) if causes else 'no safe diagnostic'}", flush=True)


def child_environment(settings: Settings) -> dict[str, str]:
    allowed = {
        "PATH", "HOME", "TMPDIR", "DOCKER_HOST", "E2E_DEPARTURE_RUNTIME_LEDGER",
        "FIELD_ENCRYPTION_MASTER_KEY", "FIELD_LOOKUP_KEY", "SESSION_TOKEN_PEPPER",
        "REGISTRATION_VERIFICATION_PEPPER", "REGISTRATION_SESSION_TOKEN_PEPPER",
        "EVALUATION_TOOL_SERVICE_TOKEN", "EVALUATION_TOOL_WEB_SERVICE_TOKEN",
        "EVALUATION_TOOL_WORKER_SERVICE_TOKEN", "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_EXECUTABLE_PATH",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(
        APP_ENV="test", DATABASE_URL=settings.database_url,
        TEST_DATABASE_URL=settings.database_url, SESSION_COOKIE_SECURE="false",
        E2E_PYTHON_BIN=sys.executable, NO_COLOR="1",
    )
    return environment


class OwnedProcess:
    """Never reap a session leader before ending its entire reserved PGID."""

    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str], log: Path):
        self.log = log
        self.status: int | None = None
        with os.fdopen(os.open(log, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as output:
            self.child = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    def running(self) -> bool:
        # ps observes without reaping. A zombie still pins the PID against reuse.
        state = subprocess.run(
            ["ps", "-p", str(self.child.pid), "-o", "stat="],
            capture_output=True, text=True, timeout=3, check=False,
        )
        return state.returncode == 0 and bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")

    def wait(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while self.running():
            if time.monotonic() >= deadline:
                raise RuntimeError("Departure owned command timed out")
            time.sleep(0.1)
        return self.close()

    def close(self) -> int:
        if self.status is not None:
            return self.status
        def end_group(requested_signal: int) -> None:
            try:
                os.killpg(self.child.pid, requested_signal)
            except ProcessLookupError:
                pass
            except PermissionError:
                # Darwin can reject TERM as well as KILL for a zombie-only
                # group. Live EPERM must still fail closed.
                state = subprocess.run(
                    ["ps", "-o", "pgid=,stat=", "-g", str(self.child.pid)],
                    capture_output=True, text=True, timeout=3, check=False,
                )
                rows = [line.split() for line in state.stdout.splitlines()]
                if sys.platform != "darwin" or state.returncode or not rows or any(
                    len(row) != 2 or row[0] != str(self.child.pid) or not row[1].startswith("Z")
                    for row in rows
                ):
                    raise

        end_group(signal.SIGTERM)
        time.sleep(0.2)
        end_group(signal.SIGKILL)
        clean = False
        try:
            for _ in range(10):
                state = subprocess.run(
                    ["ps", "-o", "pgid=,stat=", "-g", str(self.child.pid)],
                    capture_output=True, text=True, timeout=1, check=False,
                )
                rows = [line.split() for line in state.stdout.splitlines()]
                if (state.returncode == 1 and not rows) or (state.returncode == 0 and all(
                    len(row) == 2 and row[0] == str(self.child.pid) and row[1].startswith("Z")
                    for row in rows
                )):
                    clean = True
                    break
                time.sleep(0.05)
        finally:
            self.status = self.child.wait(timeout=3)
        if not clean:
            raise RuntimeError("Departure owned group still has live members")
        return self.status


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_service(process: OwnedProcess, url: str, bind_marker: str) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if not process.running():
            raise RuntimeError("Owned departure service exited before readiness")
        # A successful HTTP request alone could belong to an unrelated process.
        # Require our pinned child's successful strict-bind log as well.
        if bind_marker in process.log.read_text(errors="replace"):
            try:
                with urlopen(url, timeout=1) as response:
                    if response.status == 200 and process.running():
                        return
            except OSError:
                pass
        time.sleep(0.1)
    raise RuntimeError("Owned departure service did not become ready")


def _report_browser(log: Path, project: str) -> bool:
    """Emit counts only; cookies, credentials and failure payloads stay private."""
    try:
        report: dict[str, Any] = json.loads(log.read_text())
        stats = report["stats"]
        print(f"Departure browser {project}: {stats['expected']} passed, {stats['unexpected']} failed, {stats['skipped']} skipped", flush=True)
        for suite in report.get("suites", []):
            for spec in suite.get("specs", []):
                if not spec.get("ok"):
                    print(f"Departure browser failed at {Path(spec['file']).name}:{int(spec['line'])}", flush=True)
                    for test in spec.get("tests", []):
                        for result in test.get("results", []):
                            for output in result.get("stdout", []):
                                if output.get("text", "").startswith("Departure diagnostic "):
                                    print(_safe_summary(output["text"]), flush=True)
                            for error in result.get("errors") or [result.get("error", {})]:
                                lines = error.get("message", "").splitlines()
                                if not lines:
                                    continue
                                print(_safe_summary(lines[0]), flush=True)
                                location = error.get("location")
                                if isinstance(location, dict) and isinstance(location.get("line"), int):
                                    print(f"Departure failed step {Path(location['file']).name}:{location['line']}", flush=True)
                                frames = [line.strip() for line in error.get("stack", "").splitlines()
                                          if "/tests/" in line or ".spec.ts:" in line]
                                for frame in frames[:4]:
                                    print(_safe_summary(frame), flush=True)
        return (stats["expected"], stats["unexpected"], stats["skipped"], stats.get("flaky", 0)) == (4, 0, 0, 0)
    except (ValueError, OSError, KeyError, TypeError):
        print(f"Departure browser {project}: no complete result", flush=True)
        return False


def run_browser(settings: Settings) -> int:
    require_departure_database(settings)
    root = Path(__file__).resolve().parents[4]
    api = root / "apps/api"
    web = root / "apps/web"
    environment = child_environment(settings)
    processes: list[OwnedProcess] = []
    private: Path | None = None
    previous_term = signal.getsignal(signal.SIGTERM)
    starting = False
    termination_requested = False

    def interrupted(_signal: int, _frame: Any) -> None:
        nonlocal termination_requested
        if starting:
            # Defer until Popen returns and its reserved group is registered.
            # Blocking the signal mask would also leak that mask into children.
            termination_requested = True
            return
        raise RuntimeError("Departure browser interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        with tempfile.TemporaryDirectory(
            prefix="browser-", dir=Path(os.environ["E2E_DEPARTURE_RUNTIME_LEDGER"]).parent,
        ) as temporary:
            private = Path(temporary)
            private.chmod(0o700)
            ownership: list[dict[str, Any]] = []

            def spawn(command: list[str], directory: Path, name: str) -> OwnedProcess:
                nonlocal starting
                starting = True
                try:
                    process = OwnedProcess(command, cwd=directory, env=environment, log=private / name)
                    processes.append(process)
                    ownership.append({"pid": process.child.pid, "pgid": process.child.pid, "service": name, "command": command})
                    with os.fdopen(os.open(private / "processes.json", os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), "w") as record:
                        json.dump(ownership, record)
                finally:
                    starting = False
                if termination_requested:
                    raise RuntimeError("Departure browser interrupted")
                return process

            try:
                if spawn([sys.executable, "-m", "alembic", "upgrade", "head"], api, "migrate.log").wait(120):
                    return 1
                api_port, web_port = free_port(), free_port()
                while web_port == api_port:
                    web_port = free_port()
                api_origin, web_origin = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
                environment.update(
                    ERP_API_PROXY_TARGET=api_origin, E2E_API_URL=web_origin,
                    E2E_BASE_URL=web_origin, PLAYWRIGHT_OUTPUT_DIR=str(private / "results"),
                    PLAYWRIGHT_HTML_REPORT_DIR=str(private / "report"),
                )
                server = spawn([
                    sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                    "--port", str(api_port), "--no-use-colors",
                ], api, "api.log")
                vite = spawn([
                    str(web / "node_modules/.bin/vite"), "--host", "127.0.0.1", "--port",
                    str(web_port), "--strictPort", "--clearScreen", "false",
                ], web, "web.log")
                wait_service(server, f"{api_origin}/api/v1/health/live", f"Uvicorn running on {api_origin} (")
                wait_service(vite, f"{web_origin}/login", f"Local:   {web_origin}/")
                for project in PROJECTS:
                    seed = spawn([
                        sys.executable, "-m", "app.cli.seed_departure_reminders_e2e",
                    ], api, f"seed-{project}.log")
                    if seed.wait(120):
                        _report_command_failure(seed, "synthetic seed")
                        return 1
                    runner = spawn([
                        str(web / "node_modules/.bin/playwright"), "test", "departure-reminders.spec.ts",
                        f"--project={project}", "--workers=1", "--reporter=json",
                    ], web, f"playwright-{project}.json")
                    status = runner.wait(900)
                    verified = _report_browser(runner.log, project)
                    if status or not verified:
                        return status or 1
                return 0
            finally:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                failures = []
                for process in reversed(processes):
                    try:
                        process.close()
                    except Exception as error:  # noqa: BLE001 - attempt every owned cleanup
                        failures.append(type(error).__name__)
                if failures:
                    raise RuntimeError("Departure owned-process cleanup failed")
                print("Departure owned service groups removed", flush=True)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if private is not None:
            if private.exists():
                raise RuntimeError("Departure private browser artifacts remain")
            print("Departure private browser artifacts removed", flush=True)
