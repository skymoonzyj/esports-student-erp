"""Control-plane tests only: every mutating/network CLI is replaced by a fake.

Run with --confcutdir=apps/api/tests/cli so repository DB fixtures cannot load.
Removing a refusal or leaking/stopping an unowned resource must fail these tests.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = ROOT / "scripts/run-assisted-personnel-e2e.sh"
SECRET = "never-print-password-713829"


@pytest.fixture
def harness(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    repo = tmp_path / "repo with spaces"
    (repo / "scripts").mkdir(parents=True)
    script = repo / "scripts" / LAUNCHER.name
    if LAUNCHER.is_file():
        script.write_bytes(LAUNCHER.read_bytes())
    for relative in (
        "apps/api/app/cli/seed_e2e.py",
        "apps/api/app/cli/seed_assisted_personnel_e2e.py",
        "apps/web/tests/assisted-personnel.spec.ts",
        "apps/web/playwright.config.ts",
        "apps/web/node_modules/@playwright/test/package.json",
    ):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// isolated dependency presence fixture\n")
    playwright = repo / "apps/web/node_modules/.bin/playwright"
    playwright.parent.mkdir(parents=True)
    playwright.write_text("#!/bin/sh\nexit 0\n")
    playwright.chmod(0o700)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    events = tmp_path / "events.jsonl"
    settings = tmp_path / "fake-settings.json"
    settings.write_text("{}")
    fake = bin_dir / "tool"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import ast, json, os, signal, stat, sys, time\n"
        "from pathlib import Path\n"
        "name=Path(sys.argv[0]).name\n"
        "args=sys.argv[1:]\n"
        f"settings=json.loads(Path({str(settings)!r}).read_text())\n"
        "if name=='python' and args and args[0]=='-c' and 'import alembic' in args[1]:\n"
        "    imports = [item.name for stmt in ast.walk(ast.parse(args[1])) if isinstance(stmt, ast.Import) for item in stmt.names]\n"
        "    if settings.get('FAKE_EMAIL_VALIDATOR_MISSING') == '1' and 'email_validator' in imports:\n"
        "        sys.exit(4)\n"
        "    sys.exit(int(settings.get('FAKE_PREFLIGHT_STATUS', '0')))\n"
        "if name=='python' and args and args[0] in ('-', '-c'):\n"
        f"    os.execv({sys.executable!r}, [{sys.executable!r}, *args])\n"
        "if name=='python' and args[:2]==['-m','app.cli.e2e_database_safety']:\n"
        f"    os.environ['PYTHONPATH']={str(ROOT / 'apps/api')!r}\n"
        f"    os.execv({sys.executable!r}, [{sys.executable!r}, *args])\n"
        "if name=='python' and args[:2]==['-m','app.cli.e2e_port_safety']:\n"
        f"    os.environ['PYTHONPATH']={str(ROOT / 'apps/api')!r}\n"
        f"    os.execv({sys.executable!r}, [{sys.executable!r}, *args])\n"
        "event={'name':name, 'args':args, 'pid':os.getpid(), 'pgid':os.getpgrp(), 'env':dict(os.environ)}\n"
        "private=os.environ.get('ASSISTED_PRIVATE_CONFIG')\n"
        "if private:\n"
        "    payload=json.loads(Path(private).read_text())\n"
        "    event['private']={'suite':payload['suite'],'noMedia':payload['noMedia'],'mode':stat.S_IMODE(Path(private).stat().st_mode),'credentialMode':stat.S_IMODE(Path(payload['credentialsFile']).stat().st_mode),'keys':sorted(json.loads(Path(payload['credentialsFile']).read_text()))}\n"
        "mail=os.environ.get('REGISTRATION_TEST_MAILBOX_DIR')\n"
        "if mail:\n"
        "    root=Path(mail).parent\n"
        "    event['runtime']=str(root)\n"
        "    event['root_mode']=stat.S_IMODE(root.stat().st_mode)\n"
        "    event['mail_mode']=stat.S_IMODE((Path(mail)/'verification-mailbox.jsonl').stat().st_mode)\n"
        f"with open({str(events)!r},'a') as out: out.write(json.dumps(event)+'\\n')\n"
        "if name=='python' and args==['-m','app.cli.seed_assisted_personnel_e2e','--verify-financial-baseline']:\n"
        f"    prior=[json.loads(line) for line in Path({str(events)!r}).read_text().splitlines()]\n"
        "    count=sum(item['args']==args for item in prior)\n"
        "    if str(count)==settings.get('FAKE_BASELINE_VERIFY_FAIL_AT'): sys.exit(8)\n"
        "if name in ('docker','ssh','scp','psql','pg_dump','pkill','killall'):\n"
        "    sys.exit(99)\n"
        f"print({SECRET!r}, flush=True)\n"
        "if name=='python' and args[:2]==['-m','uvicorn']:\n"
        "    print('Uvicorn running on http://127.0.0.1:'+args[args.index('--port')+1]+' (',flush=True)\n"
        "    while True: time.sleep(.1)\n"
        "if name=='vite':\n"
        "    print('Local:   http://127.0.0.1:'+args[args.index('--port')+1]+'/',flush=True)\n"
        "    while True: time.sleep(.1)\n"
        "if settings.get('FAKE_HOLD_NPM') and name=='npm':\n"
        "    while True: time.sleep(.1)\n"
        "if name=='npm': sys.exit(int(settings.get('FAKE_NPM_STATUS','0')))\n"
    )
    fake.chmod(0o700)
    for name in ("python", "node", "vite", "npm", "curl", "docker", "ssh", "scp", "psql", "pg_dump", "pkill", "killall"):
        (bin_dir / name).symlink_to(fake)
    sockets = [socket.socket(), socket.socket()]
    for probe in sockets:
        probe.bind(("127.0.0.1", 0))
    api_port, web_port = (str(probe.getsockname()[1]) for probe in sockets)
    for probe in sockets:
        probe.close()
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "E2E_PYTHON_BIN": str(bin_dir / "python"),
        "E2E_VITE_BIN": str(bin_dir / "vite"),
        "E2E_API_PORT": api_port,
        "E2E_WEB_PORT": web_port,
        "PLAYWRIGHT_BROWSERS_PATH": str(bin_dir),
        "TEST_DATABASE_URL": f"postgresql+psycopg://erp_e2e:{SECRET}@127.0.0.1:56529/assisted_test",
        "ASSISTED_E2E_DISPOSABLE_DATABASE": "assisted_test",
        "FAKE_EVENTS": str(events),
    }
    return script, env, events


def run(harness, updates=None, *, cwd=None, args=()):
    script, env, _ = harness
    (harness[2].parent / "fake-settings.json").write_text(json.dumps(updates or {}))
    return subprocess.run(
        ["/bin/bash", str(script), *args], env={**env, **(updates or {})},
        cwd=cwd or script.parents[1],
        capture_output=True, text=True, timeout=20, check=False,
    )


def records(events: Path) -> list[dict]:
    return [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []


LEGACY_SUITES = {
    "team": (("seed_e2e", "seed_team_management_e2e"), ("team-management.spec.ts",)),
    "registration": (("seed_e2e", "seed_registration_e2e"), (
        "self-service-registration.spec.ts", "self-service-registration-privacy.spec.ts",
    )),
    "housing-occupancy": (("seed_e2e",), (
        "housing-occupancy-lifecycle.spec.ts", "housing-occupancy-revocation.spec.ts",
    )),
    "housing-reporting": (("seed_e2e", "seed_housing_reporting_e2e"), (
        "housing-reporting-lifecycle.spec.ts", "housing-reporting-authorization.spec.ts",
    )),
}
LEGACY_COMMON = (
    "apps/web/tests/helpers.ts", "apps/web/tests/e2eCredentials.ts",
    "apps/api/app/cli/e2e_credentials.json",
)


def selected_fixture(harness, suite):
    repo = harness[0].parents[1]
    # Legacy selection must work without either still-pending assisted artifact.
    for relative in ("apps/api/app/cli/seed_assisted_personnel_e2e.py", "apps/web/tests/assisted-personnel.spec.ts"):
        (repo / relative).unlink()
    seeds, specs = LEGACY_SUITES[suite]
    dependencies = [*(f"apps/api/app/cli/{seed}.py" for seed in seeds),
                    *(f"apps/web/tests/{spec}" for spec in specs), *LEGACY_COMMON]
    if suite == "registration":
        dependencies.append("apps/api/app/cli/read_registration_test_code.py")
    if suite == "housing-reporting":
        dependencies.append("apps/web/tests/support/inspect-housing-report.py")
    for relative in dependencies:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic selected dependency\n")
    return dependencies


@pytest.mark.parametrize("args", [
    ("--suite",), ("--suite", "unknown"), ("--suite=team",), ("team",),
    ("--suite", "team", "extra"), ("--suite", "team", "--suite", "assisted"),
    ("--suite", "app.cli.seed_e2e"), ("--suite", "$(touch /tmp/never-execute)"),
])
def test_exact_suite_arguments_refuse_before_any_mutation(harness, args):
    result = run(harness, args=args)
    assert result.returncode == 2, result.stdout + result.stderr
    assert SECRET not in result.stdout + result.stderr
    assert not records(harness[2])


def test_invalid_private_no_media_mode_refuses_before_database_mutation(harness):
    result = run(harness, {"ASSISTED_E2E_NO_MEDIA": "arbitrary"})
    assert result.returncode == 2
    assert not records(harness[2])


def test_private_functional_suite_selects_credentials_and_disables_media_without_changing_lifecycle(harness):
    selected_fixture(harness, "team")
    root = harness[0].parents[1]
    (root / "apps/api/app/cli/e2e_credentials.json").write_bytes((ROOT / "apps/api/app/cli/e2e_credentials.json").read_bytes())
    result = run(harness, {"ASSISTED_E2E_NO_MEDIA": "1"}, args=("--suite", "team"))
    assert result.returncode == 0, result.stderr
    events = records(harness[2])
    browsers = [event for event in events if event["name"] == "npm"]
    assert len(browsers) == 2
    for event in browsers:
        assert event["args"][-1] == "--config=playwright.assisted-private.config.ts"
        assert event["private"]["suite"] == "team"
        assert event["private"]["noMedia"] is True
        assert event["private"]["mode"] == event["private"]["credentialMode"] == 0o600
        assert "team_manager_a" in event["private"]["keys"]
        assert "registration_existing" not in event["private"]["keys"]
        assert "DATABASE_URL" not in event["env"]
    assert any(event["name"] == "vite" for event in events)


@pytest.mark.parametrize("suite", LEGACY_SUITES)
@pytest.mark.parametrize("ambient_phone_signup", ["false", "true"])
def test_fixed_suite_matrix_reseeds_each_spec_and_keeps_test_environment_private(harness, suite, ambient_phone_signup):
    selected_fixture(harness, suite)
    result = run(harness, {
        "DATABASE_URL": "production-secret", "FIELD_LOOKUP_KEY": SECRET,
        "REGISTRATION_PHONE_SIGNUP_ENABLED": ambient_phone_signup,
    }, args=("--suite", suite))
    assert result.returncode == 0, result.stderr
    events = records(harness[2])
    projects = ("desktop-1440", "mobile-360") if suite == "team" else (
        "desktop-1440", "desktop-1024", "tablet-768", "mobile-360",
    )
    seeds, specs = LEGACY_SUITES[suite]
    expected = []
    for project in projects:
        for spec in specs:
            expected.extend(("python", ["-m", "app.cli." + seed]) for seed in seeds)
            expected.append(("npm", ["--prefix", "apps/web", "run", "test:e2e", "--", spec, "--project=" + project]))
    observed = [(event["name"], event["args"]) for event in events
                if event["name"] == "npm" or event["args"][:1] == ["-m"] and event["args"][1].startswith("app.cli.seed_")]
    assert observed == expected
    assert events[0]["args"] == ["-m", "alembic", "upgrade", "head"]
    api = next(event for event in events if event["args"][:2] == ["-m", "uvicorn"])
    secret_names = [name for name in api["env"] if name.endswith(("_KEY", "_PEPPER", "_TOKEN"))]
    assert len(secret_names) == 8
    assert len({api["env"][name] for name in secret_names}) == 8
    for event in events:
        if suite == "registration" and event["args"][:2] == ["-m", "uvicorn"]:
            assert event["env"].get("REGISTRATION_PHONE_SIGNUP_ENABLED") == "true"
        else:
            assert "REGISTRATION_PHONE_SIGNUP_ENABLED" not in event["env"]
        assert event["env"].get("FIELD_LOOKUP_KEY") != SECRET
        assert not {"ASSISTED_E2E_DISPOSABLE_DATABASE", "ASSISTED_E2E_BASELINE_FILE"} & event["env"].keys()
        if event["name"] in {"vite", "npm"}:
            assert not {"DATABASE_URL", "TEST_DATABASE_URL", *secret_names} & event["env"].keys()
        if event["name"] == "npm":
            assert event["env"]["E2E_PYTHON_BIN"] == harness[1]["E2E_PYTHON_BIN"]
            assert event["env"]["REGISTRATION_TEST_MAILBOX_DIR"].startswith(event["runtime"] + "/")
    assert SECRET not in result.stdout + result.stderr
    assert_cleaned(events)


@pytest.mark.parametrize("suite", LEGACY_SUITES)
def test_every_selected_suite_dependency_is_required_before_migration(harness, suite):
    dependencies = selected_fixture(harness, suite)
    repo = harness[0].parents[1]
    for relative in [*dependencies, "apps/web/playwright.config.ts",
                     "apps/web/node_modules/.bin/playwright", "apps/web/node_modules/@playwright/test/package.json"]:
        path = repo / relative
        contents, mode = path.read_bytes(), path.stat().st_mode
        path.unlink()
        try:
            result = run(harness, args=("--suite", suite))
            assert result.returncode == 2, relative + result.stdout + result.stderr
            assert not records(harness[2]), relative
        finally:
            path.write_bytes(contents)
            path.chmod(mode)


def test_selected_python_dependencies_refuse_before_mutation(harness):
    result = run(harness, {"FAKE_PREFLIGHT_STATUS": "9"})
    assert result.returncode == 2, result.stdout + result.stderr
    assert not records(harness[2])


@pytest.mark.parametrize("missing", [False, True])
def test_registration_email_validator_probe_refuses_only_when_dependency_is_missing(harness, missing):
    selected_fixture(harness, "registration")
    result = run(harness, {"FAKE_EMAIL_VALIDATOR_MISSING": "1" if missing else "0"}, args=("--suite", "registration"))
    assert result.returncode == (2 if missing else 0), result.stdout + result.stderr
    assert SECRET not in result.stdout + result.stderr
    events = records(harness[2])
    if missing:
        assert "selected Python toolchain is unavailable" in result.stderr
        assert not events, "missing helper dependency must refuse before migration/seeds/services"
    else:
        assert events[0]["args"] == ["-m", "alembic", "upgrade", "head"]
        assert len([event for event in events if event["name"] == "npm"]) == 8
        assert_cleaned(events)


def test_explicit_assisted_selection_retains_default_matrix(harness):
    result = run(harness, args=("--suite", "assisted"))
    assert result.returncode == 0, result.stderr
    npm = [event for event in records(harness[2]) if event["name"] == "npm"]
    assert [event["args"][-2:] for event in npm] == [
        ["assisted-personnel.spec.ts", "--project=desktop-1440"],
        ["assisted-personnel.spec.ts", "--project=mobile-360"],
    ]
    assert_cleaned(records(harness[2]))


@pytest.mark.parametrize("args", [(), ("--suite", "assisted")])
def test_assisted_baseline_order_and_seed_only_private_environment(harness, args):
    result = run(harness, {
        "ASSISTED_E2E_BASELINE_FILE": "/caller-controlled-baseline.json",
        "REGISTRATION_PHONE_SIGNUP_ENABLED": "true",
    }, args=args)
    assert result.returncode == 0, result.stderr
    events = records(harness[2])
    canonical_args = ["-m", "app.cli.seed_e2e"]
    seed_args = ["-m", "app.cli.seed_assisted_personnel_e2e"]
    steps = [event for event in events if event["name"] == "npm" or event["args"][:2] in (canonical_args, seed_args)]
    assert [(event["name"], event["args"]) for event in steps] == [
        ("python", canonical_args),
        ("python", seed_args),
        ("npm", ["--prefix", "apps/web", "run", "test:e2e", "--", "assisted-personnel.spec.ts", "--project=desktop-1440"]),
        ("python", [*seed_args, "--verify-financial-baseline"]),
        ("python", canonical_args),
        ("python", seed_args),
        ("npm", ["--prefix", "apps/web", "run", "test:e2e", "--", "assisted-personnel.spec.ts", "--project=mobile-360"]),
        ("python", [*seed_args, "--verify-financial-baseline"]),
    ]
    api = next(event for event in events if event["args"][:2] == ["-m", "uvicorn"])
    seed_keys = {"ASSISTED_E2E_DISPOSABLE_DATABASE", "ASSISTED_E2E_BASELINE_FILE"}
    secret_keys = {name for name in api["env"] if name.endswith(("_KEY", "_PEPPER", "_TOKEN"))}
    for event in events:
        assert "REGISTRATION_PHONE_SIGNUP_ENABLED" not in event["env"]
        if event["args"][:2] in (canonical_args, seed_args):
            assert event["name"] == "python"
            assert event["env"] == {
                **api["env"],
                "ASSISTED_E2E_DISPOSABLE_DATABASE": "assisted_test",
                "ASSISTED_E2E_BASELINE_FILE": event["runtime"] + "/assisted-baseline.json",
            }
        else:
            assert not seed_keys & event["env"].keys()
        if event["name"] in {"vite", "npm"}:
            assert not {"DATABASE_URL", "TEST_DATABASE_URL", *secret_keys} & event["env"].keys()
    assert SECRET not in result.stdout + result.stderr
    assert "/caller-controlled-baseline.json" not in result.stdout + result.stderr
    assert_cleaned(events)


@pytest.mark.parametrize("failure_at", ["1", "2"])
def test_assisted_baseline_verification_failure_stops_run_and_cleans(harness, failure_at):
    result = run(harness, {"FAKE_BASELINE_VERIFY_FAIL_AT": failure_at})
    assert result.returncode == 1, result.stdout + result.stderr
    project = "desktop-1440" if failure_at == "1" else "mobile-360"
    assert f"phase=assisted/{project}/assisted-personnel.spec.ts/verify-financial-baseline" in result.stderr
    events = records(harness[2])
    seed_args = ["-m", "app.cli.seed_assisted_personnel_e2e"]
    steps = [event for event in events if event["name"] == "npm" or event["args"][:2] == seed_args]
    assert [event["args"] for event in steps if event["name"] == "python"] == [
        seed_args, [*seed_args, "--verify-financial-baseline"],
    ] * int(failure_at)
    assert len([event for event in steps if event["name"] == "npm"]) == int(failure_at)
    assert steps[-1]["args"] == [*seed_args, "--verify-financial-baseline"]
    assert result.stdout.count("Assisted E2E passed:") == int(failure_at) - 1
    assert SECRET not in result.stdout + result.stderr
    assert_cleaned(events)


@pytest.mark.parametrize("suite", LEGACY_SUITES)
def test_selected_suite_failure_reports_only_fixed_phase_and_cleans(harness, suite):
    selected_fixture(harness, suite)
    result = run(harness, {"FAKE_NPM_STATUS": "7"}, args=("--suite", suite))
    assert result.returncode == 1, result.stderr
    assert f"phase={suite}/desktop-1440/{LEGACY_SUITES[suite][1][0]}/playwright" in result.stderr
    assert SECRET not in result.stdout + result.stderr
    assert_cleaned(records(harness[2]))


@pytest.mark.parametrize(
    "updates",
    [
        {"TEST_DATABASE_URL": f"postgresql+psycopg://erp_e2e:{SECRET}@127.0.0.1:56529/erp"},
        {"TEST_DATABASE_URL": f"postgresql+psycopg://erp_e2e:{SECRET}@production.example:56529/assisted_test"},
        {"TEST_DATABASE_URL": f"postgresql+psycopg://erp_e2e:{SECRET}@127.0.0.1:5432/assisted_test"},
        {"TEST_DATABASE_URL": f"postgresql+psycopg://erp_e2e:{SECRET}@127.0.0.1:56529/assisted_test?host=production.example"},
        {"TEST_DATABASE_URL": ""},
        {"ASSISTED_E2E_DISPOSABLE_DATABASE": ""},
        {"ASSISTED_E2E_DISPOSABLE_DATABASE": "different_test"},
        {"E2E_PYTHON_BIN": "/not/a/python"},
        {"E2E_PYTHON_BIN": "/usr/bin/true"},
        {"SMTP_HOST": "smtp.production.example", "SMTP_PASSWORD": SECRET},
        {"SMTP_PASSWORD_FILE": "/production/secret"},
        {"REGISTRATION_MAIL_SENDER": "smtp"},
        {"APP_ENV": "production"},
        {"E2E_API_PORT": "$(touch /tmp/assisted-should-not-exist)"},
        {"E2E_WEB_PORT": "65536"},
        {"PLAYWRIGHT_BROWSERS_PATH": ""},
        {"PLAYWRIGHT_BROWSERS_PATH": "relative-browser-cache"},
        {"PLAYWRIGHT_BROWSERS_PATH": "/not/an/installed/browser"},
        {"PLAYWRIGHT_EXECUTABLE_PATH": "/not/an/installed/browser"},
    ],
)
def test_launcher_refuses_unowned_database_ports_and_real_smtp(harness, updates):
    result = run(harness, updates)
    assert result.returncode == 2, result.stdout + result.stderr
    assert SECRET not in result.stdout + result.stderr
    assert not records(harness[2]), "refusal must precede mutation/startup/prohibited commands"


@pytest.mark.parametrize("port_name", ["E2E_API_PORT", "E2E_WEB_PORT"])
def test_occupied_port_is_preserved_before_any_mutation(harness, port_name):
    with socket.socket() as occupant:
        occupant.bind(("127.0.0.1", 0))
        occupant.listen()
        port = occupant.getsockname()[1]
        result = run(harness, {port_name: str(port)})
        assert result.returncode == 2
        assert occupant.getsockname()[1] == port
        assert not records(harness[2])


@pytest.mark.parametrize("relative", ["apps/api/app/cli/seed_assisted_personnel_e2e.py", "apps/web/tests/assisted-personnel.spec.ts"])
def test_pending_integration_refuses_before_database_mutation(harness, relative):
    (harness[0].parents[1] / relative).unlink()
    assert run(harness).returncode == 2
    assert not records(harness[2])


@pytest.mark.parametrize("location", ["caller", "repository", "web-env-dir"])
@pytest.mark.parametrize("filename", [".env", ".env.local", ".env.development", ".env.development.local"])
def test_disk_dotenv_refused_before_mutation_without_reading_values(harness, location, filename):
    repo = harness[0].parents[1]
    caller = harness[2].parent / "caller with spaces"
    caller.mkdir()
    env_dir = {"caller": caller, "repository": repo, "web-env-dir": repo / "apps/web"}[location]
    (env_dir / filename).write_text(
        f"VITE_PRIVATE_VALUE={SECRET}\n"
        f"EVALUATION_TOOL_PROXY_TARGET=https://{SECRET}@production.example\n"
    )
    result = run(harness, cwd=caller)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "dotenv" in result.stderr
    assert SECRET not in result.stdout + result.stderr
    assert not records(harness[2]), "disk configuration must be refused before migrations/seeds/services"


@pytest.mark.parametrize("kind", ["broken-symlink", "fifo"])
def test_disk_dotenv_special_entries_refused_without_opening(harness, kind):
    path = harness[0].parents[1] / "apps/web/.env.development.local"
    if kind == "broken-symlink":
        path.symlink_to("absent-secret-file")
    else:
        os.mkfifo(path)
    result = run(harness)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "dotenv" in result.stderr
    assert not records(harness[2])


def test_unselected_dotenv_mode_is_not_loaded_and_both_proxies_are_owned(harness):
    repo = harness[0].parents[1]
    for directory in (repo, repo / "apps/web"):
        (directory / ".env.production").write_text(f"VITE_PRIVATE_VALUE={SECRET}\n")
    result = run(harness)
    assert result.returncode == 0, result.stderr
    vite = next(event for event in records(harness[2]) if event["name"] == "vite")
    assert vite["args"][vite["args"].index("--mode") + 1] == "development"
    api_origin = f"http://127.0.0.1:{harness[1]['E2E_API_PORT']}"
    assert vite["env"]["ERP_API_PROXY_TARGET"] == api_origin
    assert vite["env"]["EVALUATION_TOOL_PROXY_TARGET"] == api_origin
    assert SECRET not in result.stdout + result.stderr
    assert_cleaned(records(harness[2]))


def assert_cleaned(events: list[dict]) -> None:
    for event in events:
        if "runtime" in event:
            assert event["root_mode"] == 0o700
            assert event["mail_mode"] == 0o600
            assert not Path(event["runtime"]).exists()
        if event["name"] == "vite" or event["args"][:2] == ["-m", "uvicorn"]:
            with pytest.raises(ProcessLookupError):
                os.kill(event["pid"], 0)
    assert not {e["name"] for e in events} & {"docker", "ssh", "scp", "psql", "pg_dump", "pkill", "killall"}


@pytest.mark.parametrize("status", ["0", "7"])
def test_private_runtime_fake_projects_and_cleanup_on_success_or_failure(harness, status):
    result = run(harness, {"FAKE_NPM_STATUS": status})
    assert result.returncode == (0 if status == "0" else 1), result.stderr
    assert SECRET not in result.stdout + result.stderr
    events = records(harness[2])
    assert_cleaned(events)
    npm = [event for event in events if event["name"] == "npm"]
    assert [event["args"][-1] for event in npm] == (
        ["--project=desktop-1440", "--project=mobile-360"] if status == "0" else ["--project=desktop-1440"]
    )
    assert all("assisted-personnel.spec.ts" in event["args"] for event in npm)
    assert all(event["env"]["PLAYWRIGHT_BROWSERS_PATH"] == harness[1]["PLAYWRIGHT_BROWSERS_PATH"] for event in npm)
    for event in npm:
        assert event["env"]["PLAYWRIGHT_OUTPUT_DIR"].startswith(event["runtime"] + "/")
        assert event["env"]["PLAYWRIGHT_HTML_REPORT_DIR"].startswith(event["runtime"] + "/")
    api = next(event for event in events if event["args"][:2] == ["-m", "uvicorn"])
    assert api["env"]["APP_ENV"] == "test"
    assert api["env"]["REGISTRATION_TEST_SERVER_HOST"] == "127.0.0.1"
    for event in events:
        assert not any("SMTP" in name for name in event["env"])
        if event["name"] in {"vite", "npm"}:
            assert not {"DATABASE_URL", "TEST_DATABASE_URL", "FIELD_ENCRYPTION_MASTER_KEY", "SESSION_TOKEN_PEPPER"} & event["env"].keys()
    seeds = [event for event in events if event["args"] == ["-m", "app.cli.seed_assisted_personnel_e2e"]]
    assert len(seeds) == len(npm)
    verifies = [event for event in events if event["args"] == [
        "-m", "app.cli.seed_assisted_personnel_e2e", "--verify-financial-baseline",
    ]]
    assert len(verifies) == (2 if status == "0" else 0)
    for event in events:
        if event["name"] != "curl":
            assert event["pgid"] == event["pid"], "all owned commands need separate process groups"


def test_inherited_production_secrets_and_tool_hooks_never_reach_children(harness):
    result = run(harness, {
        "DATABASE_URL": f"postgresql://prod:{SECRET}@production.example/erp",
        "FIELD_ENCRYPTION_MASTER_KEY": SECRET,
        "SESSION_TOKEN_PEPPER_FILE": "/production/secret",
        "NODE_OPTIONS": f"--require={SECRET}",
        "VITE_SMUGGLED_SECRET": SECRET,
        "HTTPS_PROXY": f"https://{SECRET}@production.example",
        "E2E_BASE_URL": "https://production.example",
        "E2E_API_URL": "https://production.example",
    })
    assert result.returncode == 0, result.stderr
    assert SECRET not in result.stdout + result.stderr
    for event in records(harness[2]):
        child_env = event["env"]
        assert not {"SESSION_TOKEN_PEPPER_FILE", "NODE_OPTIONS", "VITE_SMUGGLED_SECRET", "HTTPS_PROXY"} & child_env.keys()
        assert child_env["HOME"].startswith("/tmp/assisted-personnel-e2e.")
        assert child_env.get("FIELD_ENCRYPTION_MASTER_KEY") != SECRET
        assert "production.example" not in child_env.get("DATABASE_URL", "")
        assert "production.example" not in child_env.get("E2E_BASE_URL", "")
    assert_cleaned(records(harness[2]))


@pytest.mark.parametrize("suite", [None, *LEGACY_SUITES])
def test_signal_cleans_only_owned_children_and_runtime(harness, suite):
    if suite:
        selected_fixture(harness, suite)
    script, env, event_path = harness
    (event_path.parent / "fake-settings.json").write_text('{"FAKE_HOLD_NPM": true}')
    occupant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    child = subprocess.Popen(
        ["/bin/bash", str(script), *(["--suite", suite] if suite else [])], env={**env, "FAKE_HOLD_NPM": "1"},
        cwd=script.parents[1],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if any(event["name"] == "npm" for event in records(event_path)):
                break
            assert child.poll() is None
            time.sleep(.05)
        else:
            pytest.fail("fake Playwright did not start")
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 143
        assert SECRET not in stdout + stderr
        assert occupant.poll() is None
        assert_cleaned(records(event_path))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        occupant.terminate()
        occupant.wait()
