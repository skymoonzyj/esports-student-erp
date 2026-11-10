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
    assisted|intake-roles|intake-identity|team|registration|housing-occupancy|housing-reporting) ;;
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
import re
import secrets
import shutil
import signal
import stat
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
    "assisted": (("seed_e2e", "seed_assisted_personnel_e2e"), (
        "assisted-personnel.spec.ts", "profile-claims.spec.ts",
        "pending-profile-lifecycle.spec.ts", "person-housing.spec.ts",
    ), two_projects),
    "intake-roles": (("seed_e2e", "seed_assisted_personnel_e2e"), (
        "intake-activation-roles.spec.ts",
    ), two_projects),
    "intake-identity": (("seed_e2e", "seed_assisted_personnel_e2e"), (
        "intake-identity-teams.spec.ts",
    ), two_projects),
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
        # Darwin can return EPERM after the last signalable group member exits.
        # Its non-reaping ps observation can lag that transition. Keep the same
        # owned leader unreaped and briefly observe only; never retarget/retry a
        # signal or treat a persistently live inaccessible process as success.
        deadline = time.monotonic() + .5
        while not finished(child):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.05)


def stop_children():
    errors = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for child in children:
            try:
                signal_owned(child, sig)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(exc)
        if sig == signal.SIGTERM:
            time.sleep(.2)
    # Reap only after every owned group has received both cleanup attempts. An
    # inaccessible live leader must not hang cleanup or prevent the others from
    # being signalled/reaped; retain and report the first error after all efforts.
    for child in children:
        try:
            child.wait(timeout=1)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(exc)
    if errors:
        raise errors[0]


def checked(args, *, cwd, env, log):
    child = spawn(args, cwd=cwd, env=env, log=log)
    while not finished(child):
        time.sleep(.05)
    signal_owned(child, signal.SIGKILL)
    status = child.wait()
    children.remove(child)
    if status != 0:
        raise Failed("isolated command failed (private output discarded)")


def report_registration_boundary(result, test, spec, project):
    """Accept one small typed attachment; never open reporter-provided paths."""
    if (spec != "self-service-registration.spec.ts" or project != "mobile-360"
            or test.get("projectName") != "mobile-360"):
        return
    attachments = result.get("attachments")
    if not isinstance(attachments, list):
        return
    candidates = [item for item in attachments if isinstance(item, dict)
                  and item.get("name") == "registration-evaluation-boundary-v1"]
    if len(candidates) != 1:
        return
    attachment = candidates[0]
    if (set(attachment) != {"name", "contentType", "body"}
            or attachment.get("contentType") != "application/json"):
        return
    encoded = attachment.get("body")
    if not isinstance(encoded, str) or len(encoded) > 5464:
        return

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 4096:
            return
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, TypeError, RecursionError):
        return

    def integer(value, upper, lower=0):
        return type(value) is int and lower <= value <= upper

    fields = {"version", "stage", "route", "error_class", "elapsed_ms", "page_errors",
              "document_failures", "script_failures", "endpoints"}
    if not isinstance(data, dict) or set(data) != fields or type(data["version"]) is not int or data["version"] != 1:
        return
    if (not isinstance(data["stage"], str) or data["stage"] not in {
            "before_navigation", "navigation_complete", "context_response", "heading_ready"}
            or not isinstance(data["route"], str) or data["route"] not in {
                "root", "login", "password", "register_status", "evaluations", "other"}
            or not isinstance(data["error_class"], str) or data["error_class"] not in {
                "timeout", "network", "aborted", "other", "unknown"}):
        return
    if not integer(data["elapsed_ms"], 86_400_000) or any(
        not integer(data[key], 1000) for key in ("page_errors", "document_failures", "script_failures")
    ):
        return
    endpoints = data["endpoints"]
    names = ("auth_me", "auth_csrf", "evaluation_context")
    endpoint_fields = {"request", "response", "finished", "failed", "last_status", "last_elapsed_ms"}
    if not isinstance(endpoints, dict) or set(endpoints) != set(names):
        return
    for value in endpoints.values():
        if not isinstance(value, dict) or set(value) != endpoint_fields:
            return
        if any(not integer(value[key], 1000) for key in ("request", "response", "finished", "failed")):
            return
        if value["last_status"] is not None and not integer(value["last_status"], 599, 100):
            return
        if value["last_elapsed_ms"] is not None and not integer(value["last_elapsed_ms"], 86_400_000):
            return
    summary = (
        f"Registration evaluation boundary v1: stage={data['stage']} route={data['route']} "
        f"error_class={data['error_class']} elapsed_ms={data['elapsed_ms']} "
        f"page_errors={data['page_errors']} document_failures={data['document_failures']} "
        f"script_failures={data['script_failures']}"
    )
    for name in names:
        value = endpoints[name]
        status = value["last_status"] if value["last_status"] is not None else "none"
        elapsed = value["last_elapsed_ms"] if value["last_elapsed_ms"] is not None else "none"
        summary += (f" {name}=request:{value['request']},response:{value['response']},"
                    f"finished:{value['finished']},failed:{value['failed']},status:{status},elapsed_ms:{elapsed}")
    print(summary, file=sys.stderr)


def report_failed_test_locations(path, spec, project):
    """Reveal selected source locations and typed metadata, never reporter text."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        print("Assisted E2E diagnostic: structured test report unavailable", file=sys.stderr)
        return
    if not isinstance(data, dict):
        print("Assisted E2E diagnostic: structured test report unavailable", file=sys.stderr)
        return
    locations = []
    error_locations = []
    result_states = {"passed", "failed", "timedOut", "skipped", "interrupted"}
    outcomes = {"expected", "unexpected", "flaky", "skipped", "failed"}

    def array(value):
        return value if isinstance(value, list) else []

    def state(value, allowed):
        return value if isinstance(value, str) and value in allowed else "unknown"

    def number(value, lower, upper):
        return value if type(value) is int and lower <= value <= upper else "unknown"

    def source_line(location):
        if not isinstance(location, dict):
            return "unknown"
        filename = location.get("file")
        if not isinstance(filename, str) or Path(filename).name != spec:
            return "unknown"
        return number(location.get("line"), 1, 1_000_000)

    def walk(suite):
        if not isinstance(suite, dict):
            return
        for entry in array(suite.get("specs")):
            if not isinstance(entry, dict):
                continue
            failed = [test for test in array(entry.get("tests")) if isinstance(test, dict) and (
                state(test.get("status"), outcomes) in {"unexpected", "failed"} or any(
                    isinstance(result, dict) and state(result.get("status"), result_states)
                    in {"failed", "timedOut", "interrupted"}
                    for result in array(test.get("results"))
                )
            )]
            line = source_line(entry)
            if failed and line != "unknown":
                locations.append(line)
            for test in failed:
                for result in array(test.get("results")):
                    if not isinstance(result, dict):
                        continue
                    if line != "unknown":
                        report_registration_boundary(result, test, spec, project)
                    if line != "unknown":
                        # spec is from the runner's fixed manifest, not JSON file,
                        # title/project/error/message/body/attachment strings.
                        print(
                            f"Assisted E2E failed test metadata: file={spec} line={line} "
                            f"expected_status={state(test.get('expectedStatus'), result_states)} "
                            f"outcome={state(test.get('status'), outcomes)} "
                            f"status={state(result.get('status'), result_states)} "
                            f"duration_ms={number(result.get('duration'), 0, 86_400_000)} "
                            f"timeout_ms={number(test.get('timeout'), 0, 86_400_000)}",
                            file=sys.stderr,
                        )
                    errors = [*array(result.get("errors")), result.get("error")]
                    for error in errors:
                        if not isinstance(error, dict):
                            continue
                        error_line = source_line(error.get("location"))
                        if error_line != "unknown":
                            error_locations.append(error_line)
                        detail = str(error.get("stack", "")) + "\n" + str(error.get("message", ""))
                        for match in re.finditer(re.escape(spec) + r":(\d+):\d+", detail):
                            if len(match.group(1)) <= 7:
                                error_line = number(int(match.group(1)), 1, 1_000_000)
                                if error_line != "unknown":
                                    error_locations.append(error_line)
        for child in array(suite.get("suites")):
            walk(child)

    for suite in array(data.get("suites")):
        walk(suite)
    if locations:
        print("Assisted E2E failed test source lines: " + ",".join(map(str, sorted(set(locations)))), file=sys.stderr)
    else:
        print("Assisted E2E diagnostic: no failed source location in structured report", file=sys.stderr)
    if error_locations:
        print("Assisted E2E failure call-site lines: " + ",".join(map(str, sorted(set(error_locations)))), file=sys.stderr)


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


INTAKE_SCREENSHOTS = ("role-picker-clear.png", "role-picker-command.png",
                      "role-plan-clear.png", "role-plan-command.png")
if suite == "intake-identity":
    INTAKE_SCREENSHOTS = tuple(f"{name}-{theme}.png" for theme in ("clear", "command")
                               for name in ("identity-selection", "staff-roster"))


def verify_identity_report(path, spec, project):
    """A zero Playwright exit is insufficient: every fixed case must pass once."""
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get("errors") != []:
            raise ValueError
        found = []

        def walk(value):
            if not isinstance(value, dict):
                raise ValueError
            for entry in value.get("specs", []):
                if Path(entry.get("file", "")).name != spec:
                    raise ValueError
                tests = entry.get("tests")
                if not isinstance(tests, list) or len(tests) != 1:
                    raise ValueError
                test = tests[0]
                results = test.get("results")
                if (test.get("projectName") != project or test.get("expectedStatus") != "passed"
                        or test.get("status") != "expected" or not isinstance(results, list)
                        or len(results) != 1 or results[0].get("status") != "passed"
                        or results[0].get("retry") != 0):
                    raise ValueError
                found.append(entry.get("title"))
            for child in value.get("suites", []):
                walk(child)

        walk(data)
        expected = [f"IDT{case} {theme}" for theme in ("clear", "command") for case in range(1, 7)]
        if sorted(found) != sorted(expected):
            raise ValueError
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise Failed("fixed identity acceptance report incomplete") from None


def intake_artifact_destination():
    destination = root
    plan, leaf = (("2026-11-06-intake-identity-teams", "task-6-e2e-artifacts")
                  if suite == "intake-identity" else
                  ("2026-11-06-intake-identity-teams", "task-6-legacy-role-artifacts"))
    for name in (".superpowers", "sdd", plan, leaf):
        destination = destination / name
        try:
            mode = destination.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode) or name == leaf:
            # Never delete/archive/overwrite another invocation's evidence. Root
            # must explicitly archive prior output before a known rerun.
            raise Refused("prior or unsafe screenshot destination; archive prior evidence before rerun")
    return destination


def stage_intake_screenshots(results, project, staging):
    # Only these reviewed, synthetic picker/readonly-panel crops survive private
    # runtime cleanup. Never export traces, reports, raw logs or activation forms.
    images = []
    for name in INTAKE_SCREENSHOTS:
        candidates = list(results.rglob(name)) if results.is_dir() else []
        if len(candidates) != 1:
            raise Failed("missing or duplicate synthetic screenshot")
        for source in candidates:
            local_parts = [source, *list(source.parents)[:list(source.parents).index(results) + 1]]
            if (any(part.is_symlink() for part in local_parts)
                    or not stat.S_ISREG(source.lstat().st_mode) or source.stat().st_size > 5_000_000):
                raise Failed("unsafe synthetic screenshot")
            data = source.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise Failed("invalid synthetic screenshot")
            images.append((name, data))
    destination = staging / project
    destination.mkdir(mode=0o700, parents=True)
    for name, data in images:
        target = destination / name
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)


def publish_intake_screenshots(staging):
    # All requested projects and postchecks have passed. Build the complete
    # eight-crop directory on its destination filesystem, then publish once.
    destination = intake_artifact_destination()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".task-3-crops-publish.", dir=destination.parent) as temporary:
        publication = Path(temporary)
        for project in projects:
            (publication / project).mkdir(mode=0o700)
            for name in INTAKE_SCREENSHOTS:
                data = (staging / project / name).read_bytes()
                descriptor = os.open(publication / project / name,
                                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
        intake_artifact_destination()  # Recheck immediately before publication.
        publication.rename(destination)


def main():
    global phase
    if suite in {"intake-roles", "intake-identity"}:
        intake_artifact_destination()  # Before any DB/process mutation.
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
    dependencies.extend((
        "apps/web/tests/helpers.ts", "apps/web/tests/e2eCredentials.ts",
        "apps/api/app/cli/e2e_credentials.json",
    ))
    if suite in {"registration", "assisted", "intake-roles", "intake-identity"}:
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
        if suite in {"assisted", "intake-roles", "intake-identity"}:
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
                        "PLAYWRIGHT_JSON_OUTPUT_FILE": str(runtime / "playwright-results.json"),
                    }
                    phase = f"{suite}/{project}/{spec}/playwright"
                    try:
                        command = [npm, "--prefix", "apps/web", "run", "test:e2e", "--", spec,
                                   "--project=" + project, "--reporter=json", "--trace=off"]
                        checked(
                            command,
                            cwd=root, env=test_env, log=runtime / "playwright.log",
                        )
                    except Failed:
                        report_failed_test_locations(runtime / "playwright-results.json", spec, project)
                        raise
                    if suite == "intake-identity":
                        phase = f"{suite}/{project}/{spec}/verify-complete-report"
                        verify_identity_report(runtime / "playwright-results.json", spec, project)
                    if suite in {"assisted", "intake-roles", "intake-identity"}:
                        phase = f"{suite}/{project}/{spec}/verify-financial-baseline"
                        checked(
                            [python, "-m", "app.cli.seed_assisted_personnel_e2e", "--verify-financial-baseline"],
                            cwd=root / "apps/api", env=seed_env, log=runtime / "seed.log",
                        )
                    if suite in {"intake-roles", "intake-identity"}:
                        phase = f"{suite}/{project}/{spec}/stage-synthetic-screenshots"
                        stage_intake_screenshots(runtime / "results", project, runtime / "approved-crops")
                    if suite == "assisted":
                        phase = f"{suite}/{project}/{spec}/verify-protected-claims"
                        checked(
                            [python, "-m", "app.cli.seed_assisted_personnel_e2e", "--verify-protected-claims"],
                            cwd=root / "apps/api", env=seed_env, log=runtime / "seed.log",
                        )
                        phase = f"{suite}/{project}/{spec}/verify-pending-lifecycle"
                        checked(
                            [python, "-m", "app.cli.seed_assisted_personnel_e2e", "--verify-pending-lifecycle"],
                            cwd=root / "apps/api", env=seed_env, log=runtime / "seed.log",
                        )
                    completed += 1
                    print(f"Assisted E2E passed: phase={phase} count={completed}", flush=True)
            if suite == "intake-roles":
                phase = suite + "/publish-synthetic-screenshots"
                publish_intake_screenshots(runtime / "approved-crops")
        finally:
            # Signals may not interrupt cleanup; kill/wait only our own sessions.
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, signal.SIG_IGN)
            stop_children()
        if suite == "intake-identity":
            phase = suite + "/publish-synthetic-screenshots"
            publish_intake_screenshots(runtime / "approved-crops")
    if suite == "intake-identity":
        print("Intake identity E2E accepted: complete desktop/mobile matrix; financial baseline unchanged; owned runtime cleaned", flush=True)
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
except (Failed, OSError, subprocess.SubprocessError) as exc:
    # Never print exceptions/command lines: they may contain credentials or OTPs.
    # Typed bounded metadata only; no exception message, filename or raw traceback.
    categories = {
        Failed: "Failed", PermissionError: "PermissionError", FileNotFoundError: "FileNotFoundError",
        FileExistsError: "FileExistsError", NotADirectoryError: "NotADirectoryError",
        IsADirectoryError: "IsADirectoryError", ProcessLookupError: "ProcessLookupError",
        ChildProcessError: "ChildProcessError", InterruptedError: "InterruptedError",
        subprocess.CalledProcessError: "CalledProcessError", subprocess.TimeoutExpired: "TimeoutExpired",
    }
    category = categories.get(type(exc), "OSError" if isinstance(exc, OSError) else "SubprocessError")
    error_number = getattr(exc, "errno", None)
    error_number = error_number if type(error_number) is int and 0 <= error_number <= 4095 else 0
    source_line = 0
    frame = exc.__traceback__
    while frame:
        if frame.tb_frame.f_code.co_filename == __file__ and 1 <= frame.tb_lineno <= 100_000:
            source_line = frame.tb_lineno
        frame = frame.tb_next
    print(f"Assisted E2E exception metadata: class={category} errno={error_number} python_line={source_line}", file=sys.stderr)
    print(f"Assisted E2E failed: phase={phase}; private output discarded", file=sys.stderr)
    sys.exit(1)
PY
