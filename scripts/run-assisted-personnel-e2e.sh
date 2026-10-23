#!/usr/bin/env bash
# Destructive isolated runner, not a provisioning tool. The caller must first
# verify its own disposable PostgreSQL project/container and stop other writers.
# ASSISTED_E2E_DISPOSABLE_DATABASE must equal the exact database name: an explicit
# reset/ownership attestation, NOT cryptographic or server-side ownership proof.
# No production fallback, Docker, deployment, external mail, or retained raw logs.
set -euo pipefail
set +x

# Fixed selectors only: reject caller commands/specs before invoking any tool.
if (( $# != 0 )); then
  if (( $# != 2 )) || [[ $1 != --suite ]]; then
    echo "Assisted E2E refused: exact --suite selector required" >&2
    exit 2
  fi
  case $2 in
    assisted|team|registration|housing-occupancy|housing-reporting) ;;
    *) echo "Assisted E2E refused: unknown fixed suite" >&2; exit 2 ;;
  esac
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
python_bin=${E2E_PYTHON_BIN:-$repo_root/.venv/bin/python}
if [[ -z ${E2E_PYTHON_BIN:-} && ! -x "$python_bin" ]]; then
  common=$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null || true)
  if [[ -n "$common" ]]; then
    [[ "$common" = /* ]] || common="$repo_root/$common"
    shared_root=$(cd "$(dirname "$common")" && pwd -P)
    python_bin="$shared_root/.venv/bin/python"
  fi
fi
if [[ ! -x "$python_bin" ]] || \
   [[ $("$python_bin" -c 'import sys; print("assisted-python-3" if sys.version_info >= (3, 11) else "refused")' 2>/dev/null) != assisted-python-3 ]]; then
  echo "Assisted E2E refused: valid Python 3.11+ required" >&2
  exit 2
fi
export ASSISTED_E2E_REPO_ROOT="$repo_root" E2E_PYTHON_BIN="$python_bin"
exec "$python_bin" - "$@" <<'PY'
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


class Refused(Exception):
    pass


class Failed(Exception):
    pass


class Interrupted(BaseException):
    def __init__(self, signum):
        self.signum = signum


root = Path(os.environ["ASSISTED_E2E_REPO_ROOT"])
python = os.environ["E2E_PYTHON_BIN"]
children = []
web_mode = "development"
suite = sys.argv[2] if len(sys.argv) == 3 else "assisted"
phase = suite + "/preflight"
# Each tuple is (seed modules in canonical order, specs, viewport projects).
four_projects = ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360")
two_projects = ("desktop-1440", "mobile-360")
seeds, specs, projects = {
    "assisted": (("seed_e2e", "seed_assisted_personnel_e2e"), ("assisted-personnel.spec.ts",), two_projects),
    "team": (("seed_e2e", "seed_team_management_e2e"), ("team-management.spec.ts",), two_projects),
    "registration": (("seed_e2e", "seed_registration_e2e"), (
        "self-service-registration.spec.ts", "self-service-registration-privacy.spec.ts",
    ), four_projects),
    "housing-occupancy": (("seed_e2e",), (
        "housing-occupancy-lifecycle.spec.ts", "housing-occupancy-revocation.spec.ts",
    ), four_projects),
    "housing-reporting": (("seed_e2e", "seed_housing_reporting_e2e"), (
        "housing-reporting-lifecycle.spec.ts", "housing-reporting-authorization.spec.ts",
    ), four_projects),
}[suite]


def refuse_disk_dotenv():
    # Current vite.config.ts explicitly loadEnv(mode, process.cwd(), ""); Vite
    # also loads these exact names from envDir (default Web root). Refuse names
    # using lstat only: never open secret values, follow a symlink, or read a FIFO.
    # Check the caller/repository roots too, before any child changes directory.
    for directory in {Path.cwd(), root, root / "apps/web"}:
        for name in (".env", ".env.local", f".env.{web_mode}", f".env.{web_mode}.local"):
            try:
                (directory / name).lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise Refused("disk dotenv configuration cannot be checked safely") from None
            raise Refused("disk dotenv configuration is forbidden for isolated Web runtime")


def interrupt(signum, _frame):
    raise Interrupted(signum)


def spawn(args, *, cwd, env, log):
    # Every process owns a new session/group, never the launcher's/parent's group.
    # Keep exited leaders unreaped until finally, preventing PID/group reuse.
    blocked = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM, signal.SIGHUP})
    try:
        with log.open("ab") as output:
            child = subprocess.Popen(
                args, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
                # This single-threaded supervisor unblocks its registration mask
                # in the forked child before exec (restore_signals is not enough).
                preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, blocked),
            )
        children.append(child)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
    return child


def finished(child):
    # WNOWAIT observes liveness without reaping the owned group leader.
    if hasattr(os, "waitid"):
        return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    # Darwin has no os.waitid. ps observes zombie status without reaping, so the
    # session/group ID remains protected until we terminate descendants and wait.
    result = subprocess.run(
        ["/bin/ps", "-o", "stat=", "-p", str(child.pid)],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"},
    )
    return not result.stdout.strip() or result.stdout.strip().startswith("Z")


def signal_owned(child, sig):
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin returns EPERM for a group containing only its zombie leader.
        # An alive process refusing termination is still a cleanup failure.
        if not finished(child):
            raise


def stop_children():
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for child in children:
            signal_owned(child, sig)
        if sig == signal.SIGTERM:
            time.sleep(.2)
    for child in children:
        child.wait()


def checked(args, *, cwd, env, log):
    child = spawn(args, cwd=cwd, env=env, log=log)
    while not finished(child):
        time.sleep(.05)
    signal_owned(child, signal.SIGKILL)
    status = child.wait()
    children.remove(child)
    if status != 0:
        raise Failed("isolated command failed (private output discarded)")


def ready(child, url, marker, log, env):
    for _ in range(120):
        if finished(child):
            raise Failed("owned service exited before readiness")
        if marker in log.read_text(errors="replace"):
            # No proxies/config files or non-loopback origins can reach curl.
            result = subprocess.run(
                ["curl", "--disable", "--noproxy", "*", "--max-time", "1", "--fail", "--silent", url],
                env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0 and not finished(child):
                return
        time.sleep(.1)
    raise Failed("owned service readiness timed out")


def main():
    global phase
    private_mode = os.environ.get("ASSISTED_E2E_NO_MEDIA", "")
    if private_mode not in ("", "1"):
        raise Refused("fixed private no-media mode required")
    if os.environ.get("APP_ENV", "test") != "test":
        raise Refused("APP_ENV must be test")
    for key, value in os.environ.items():
        upper = key.upper()
        if value and (
            "SMTP" in upper
            or ("MAIL" in upper and any(part in upper for part in ("SENDER", "TRANSPORT", "BACKEND")))
        ):
            raise Refused("production mail configuration is forbidden")
    refuse_disk_dotenv()
    dsn = os.environ.get("TEST_DATABASE_URL", "")
    try:
        url = urlsplit(dsn)
        db_name = url.path.removeprefix("/")
        if (
            url.scheme not in ("postgresql", "postgresql+psycopg")
            or url.hostname != "127.0.0.1"
            or not url.port or url.port <= 1024 or url.port == 5432
            or not db_name or db_name != os.environ.get("ASSISTED_E2E_DISPOSABLE_DATABASE")
        ):
            raise ValueError
    except ValueError:
        raise Refused("explicit owned disposable loopback database required") from None
    ports = []
    for key, default in (("E2E_API_PORT", "18019"), ("E2E_WEB_PORT", "15199")):
        raw = os.environ.get(key, default)
        if not raw.isascii() or not raw.isdigit() or not 1024 < int(raw) <= 65535:
            raise Refused("invalid isolated service port")
        ports.append(int(raw))
    if len({*ports, url.port}) != 3:
        raise Refused("API, Web, and database ports must differ")
    # Deliberate allowlist: no production secrets, *_FILE, SMTP, proxy, NODE_OPTIONS,
    # shell hooks, VITE_* injections, or inherited application configuration.
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root), "LANG": "C.UTF-8"}
    cli_base = {**base, "PYTHONPATH": str(root / "apps/api"), "TEST_DATABASE_URL": dsn}
    for args in (
        ["-m", "app.cli.e2e_database_safety"],
        *(["-m", "app.cli.e2e_port_safety", str(port)] for port in ports),
    ):
        result = subprocess.run(
            [python, *args], cwd=root / "apps/api", env=cli_base,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode:
            raise Refused("database or unoccupied-port safety check refused")
    dependencies = [
        *(f"apps/api/app/cli/{seed}.py" for seed in seeds),
        *(f"apps/web/tests/{spec}" for spec in specs),
        "apps/web/playwright.config.ts",
        "apps/web/node_modules/@playwright/test/package.json",
    ]
    if suite != "assisted":
        dependencies.extend((
            "apps/web/tests/helpers.ts", "apps/web/tests/e2eCredentials.ts",
            "apps/api/app/cli/e2e_credentials.json",
        ))
    if suite == "registration":
        dependencies.append("apps/api/app/cli/read_registration_test_code.py")
    if suite == "housing-reporting":
        dependencies.append("apps/web/tests/support/inspect-housing-report.py")
    for relative in dependencies:
        if not (root / relative).is_file():
            raise Refused("selected suite integration dependency is missing")
    vite = os.environ.get("E2E_VITE_BIN", str(root / "apps/web/node_modules/.bin/vite"))
    npm = shutil.which("npm", path=base["PATH"])
    node = shutil.which("node", path=base["PATH"])
    playwright = root / "apps/web/node_modules/.bin/playwright"
    if (
        not os.path.isabs(vite) or not os.path.isfile(vite) or not os.access(vite, os.X_OK)
        or not npm or not node or not playwright.is_file() or not os.access(playwright, os.X_OK)
    ):
        raise Refused("local Web toolchain is unavailable")
    # Third-party imports only: never import app modules/Settings/engine here.
    # XLSX inspection is the reporting suite's only additional Python tool.
    imports = "import alembic, uvicorn, sqlalchemy, psycopg, cryptography, pydantic_settings, argon2, fastapi, email_validator"
    if suite == "housing-reporting":
        imports += ", openpyxl"
    dependency_check = subprocess.run(
        [python, "-c", imports], cwd=root / "apps/api", env=base,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if dependency_check.returncode:
        raise Refused("selected Python toolchain is unavailable")
    browser_env = {}
    for name in ("PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_EXECUTABLE_PATH"):
        value = os.environ.get(name, "")
        if not value:
            continue
        path = Path(value)
        valid = path.is_dir() if name == "PLAYWRIGHT_BROWSERS_PATH" else path.is_file() and os.access(path, os.X_OK)
        if not path.is_absolute() or not valid:
            raise Refused("explicit existing local browser installation required")
        browser_env[name] = str(path.resolve())
    if not browser_env:
        raise Refused("explicit existing local browser installation required")
    # Preserve the preflight-resolved Node search order when HOME becomes private.
    base["PATH"] = str(Path(node).parent) + os.pathsep + base["PATH"]
    api_origin, web_origin = (f"http://127.0.0.1:{port}" for port in ports)
    os.umask(0o077)
    # Ignore inherited TMPDIR: private runtime must never be under Vite's root.
    with tempfile.TemporaryDirectory(prefix="assisted-personnel-e2e.", dir="/tmp") as temporary:
        runtime = Path(temporary)
        runtime.chmod(0o700)
        base["HOME"] = str(runtime)
        base["TMPDIR"] = str(runtime)
        private_config = runtime / "suite-config.json"
        if private_mode:
            canonical = json.loads((root / "apps/api/app/cli/e2e_credentials.json").read_text())
            keys = {"admin", "customer_service", "coach", "guardian", "student", "life_teacher"}
            prefix = {"team": "team_", "registration": "registration_", "housing-reporting": "housing_report_"}.get(suite)
            if prefix:
                keys.update(key for key in canonical if key.startswith(prefix))
            selected = {key: canonical[key] for key in keys}
            actors = None
            if suite == "assisted":
                sys.path.insert(0, str(root / "apps/api"))
                from app.cli.seed_assisted_personnel_e2e import ACCOUNTS
                actors = {key: ACCOUNTS[key].phone for key in ("coach", "manager", "administration")}
            credentials = runtime / "credentials.json"
            credentials.write_text(json.dumps(selected))
            credentials.chmod(0o600)
            private_config.write_text(json.dumps({"suite": suite, "baseURL": web_origin,
                "credentialsFile": str(credentials), "actors": actors, "noMedia": True}))
            private_config.chmod(0o600)
        mailbox = runtime / "mailbox"
        mailbox.mkdir(mode=0o700)
        (mailbox / ".registration-test-mailbox").write_text("self-service-registration-e2e\n")
        (mailbox / "verification-mailbox.jsonl").touch(mode=0o600)
        api_env = {
            **base, "APP_ENV": "test", "PYTHONPATH": str(root / "apps/api"),
            "DATABASE_URL": dsn, "SESSION_COOKIE_SECURE": "false",
            "REGISTRATION_TEST_MAILBOX_DIR": str(mailbox),
            "REGISTRATION_TEST_SERVER_HOST": "127.0.0.1",
            "ERP_WEB_BASE_URL": web_origin,
            "OPERATIONS_MONITOR_ENABLED": "false", "NO_COLOR": "1",
        }
        for name in (
            "FIELD_ENCRYPTION_MASTER_KEY", "FIELD_LOOKUP_KEY", "SESSION_TOKEN_PEPPER",
            "REGISTRATION_VERIFICATION_PEPPER", "REGISTRATION_SESSION_TOKEN_PEPPER",
            "EVALUATION_TOOL_SERVICE_TOKEN", "EVALUATION_TOOL_WEB_SERVICE_TOKEN",
            "EVALUATION_TOOL_WORKER_SERVICE_TOKEN",
        ):
            api_env[name] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        api_server_env = api_env
        if suite == "registration":
            # Match the original registration runner's API-only test capability;
            # never inherit this deployment flag or enable it for other suites.
            api_server_env = {**api_env, "REGISTRATION_PHONE_SIGNUP_ENABLED": "true"}
        seed_env = api_env
        if suite == "assisted":
            # Forward only the validated attestation and a fixed private path;
            # neither is inherited from callers or exposed to API/Web/browser.
            seed_env = {
                **api_env, "ASSISTED_E2E_DISPOSABLE_DATABASE": db_name,
                "ASSISTED_E2E_BASELINE_FILE": str(runtime / "assisted-baseline.json"),
            }
        web_env = {
            **base, "ERP_API_PROXY_TARGET": api_origin,
            "EVALUATION_TOOL_PROXY_TARGET": api_origin, "NO_COLOR": "1",
        }
        # TestMailboxVerificationSender overrides DB SMTP for every purpose. No
        # production sender env is permitted; no SMTP fallback is selected here.
        try:
            phase = suite + "/migration"
            checked([python, "-m", "alembic", "upgrade", "head"], cwd=root / "apps/api", env=api_env, log=runtime / "migration.log")
            phase = suite + "/api-start"
            api = spawn(
                [python, "-m", "uvicorn", "app.main:app", "--app-dir", "apps/api", "--host", "127.0.0.1", "--port", str(ports[0]), "--no-use-colors"],
                cwd=root, env=api_server_env, log=runtime / "api.log",
            )
            phase = suite + "/web-start"
            web = spawn(
                [vite, "--mode", web_mode, "--host", "127.0.0.1", "--port", str(ports[1]), "--strictPort", "--clearScreen", "false"],
                cwd=root / "apps/web", env=web_env, log=runtime / "web.log",
            )
            phase = suite + "/api-ready"
            ready(api, api_origin + "/api/v1/health/live", f"Uvicorn running on {api_origin} (", runtime / "api.log", base)
            phase = suite + "/web-ready"
            ready(web, web_origin + "/login", f"Local:   {web_origin}/", runtime / "web.log", base)
            completed = 0
            for project in projects:
                for spec in specs:
                    for seed in seeds:
                        phase = f"{suite}/{project}/{spec}/{seed}"
                        checked([python, "-m", "app.cli." + seed], cwd=root / "apps/api", env=seed_env, log=runtime / "seed.log")
                    # Inspected helpers need the mailbox reader/Python only;
                    # neither Web nor tests receive DB/crypto/application env.
                    test_env = {
                        **base, **browser_env, "APP_ENV": "test", "E2E_PYTHON_BIN": python,
                        "E2E_API_URL": api_origin, "E2E_BASE_URL": web_origin,
                        "REGISTRATION_TEST_MAILBOX_DIR": str(mailbox),
                        "PLAYWRIGHT_OUTPUT_DIR": str(runtime / "results"),
                        "PLAYWRIGHT_HTML_REPORT_DIR": str(runtime / "report"),
                    }
                    if private_mode:
                        test_env["ASSISTED_PRIVATE_CONFIG"] = str(private_config)
                    phase = f"{suite}/{project}/{spec}/playwright"
                    checked(
                        [npm, "--prefix", "apps/web", "run", "test:e2e", "--", spec, "--project=" + project,
                         *(["--config=playwright.assisted-private.config.ts"] if private_mode else [])],
                        cwd=root, env=test_env, log=runtime / "playwright.log",
                    )
                    if suite == "assisted":
                        phase = f"{suite}/{project}/{spec}/verify-financial-baseline"
                        checked(
                            [python, "-m", "app.cli.seed_assisted_personnel_e2e", "--verify-financial-baseline"],
                            cwd=root / "apps/api", env=seed_env, log=runtime / "seed.log",
                        )
                    completed += 1
                    print(f"Assisted E2E passed: phase={phase} count={completed}", flush=True)
        finally:
            # Signals may not interrupt cleanup; kill/wait only our own sessions.
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, signal.SIG_IGN)
            stop_children()
    return 0


for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, interrupt)
try:
    sys.exit(main())
except Refused as exc:
    print(f"Assisted E2E refused: {exc}", file=sys.stderr)
    sys.exit(2)
except Interrupted as exc:
    print(f"Assisted E2E interrupted: phase={phase}; owned runtime cleaned", file=sys.stderr)
    sys.exit(128 + exc.signum)
except (Failed, OSError, subprocess.SubprocessError):
    # Never print exceptions/command lines: they may contain credentials or OTPs.
    print(f"Assisted E2E failed: phase={phase}; private output discarded", file=sys.stderr)
    sys.exit(1)
PY
