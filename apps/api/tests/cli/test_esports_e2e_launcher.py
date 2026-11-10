from __future__ import annotations

import base64
import json
import os
import re
import shlex
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.cli.summarize_esports_assessments_e2e import REQUIRED_CASE_IDS

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = REPOSITORY_ROOT / "scripts/run-esports-assessments-e2e.sh"
CORE_LAUNCHER = REPOSITORY_ROOT / "scripts/run-core-e2e.sh"
PLAYWRIGHT_CONFIG = REPOSITORY_ROOT / "apps/web/playwright.config.ts"
TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
CHILD_SECRET_NAMES = (
    "TEST_DATABASE_URL",
    "DATABASE_URL",
    "FIELD_ENCRYPTION_MASTER_KEY",
    "FIELD_LOOKUP_KEY",
    "REGISTRATION_VERIFICATION_PEPPER",
    "REGISTRATION_SESSION_TOKEN_PEPPER",
    "SESSION_TOKEN_PEPPER",
    "EVALUATION_TOOL_SERVICE_TOKEN",
    "EVALUATION_TOOL_WEB_SERVICE_TOKEN",
    "EVALUATION_TOOL_WORKER_SERVICE_TOKEN",
)


def _base_env() -> dict[str, str]:
    return {
        **os.environ,
        "TEST_DATABASE_URL": (
            "postgresql+psycopg://erp:secret@127.0.0.1:55433/erp_e2e"
        ),
        "FIELD_ENCRYPTION_MASTER_KEY": TEST_KEY,
        "FIELD_LOOKUP_KEY": TEST_KEY,
        "SESSION_TOKEN_PEPPER": TEST_KEY,
        "EVALUATION_TOOL_SERVICE_TOKEN": "e2e-v1-service-token",
        "EVALUATION_TOOL_WEB_SERVICE_TOKEN": "e2e-web-service-token",
        "EVALUATION_TOOL_WORKER_SERVICE_TOKEN": "e2e-worker-service-token",
    }


def _inner_bash(tmp_path: Path) -> tuple[Path, Path]:
    invocation = tmp_path / "inner-bash.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "bash"
    fake.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$E2E_CORE_MODE|$E2E_API_URL|$E2E_BASE_URL|$*\" > {shlex.quote(str(invocation))}\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    return fake_bin, invocation


def test_wrapper_executes_shared_runner_with_fixed_mode_and_loopback_origins(
    tmp_path: Path,
) -> None:
    assert stat.S_IMODE(LAUNCHER.stat().st_mode) == 0o755
    syntax = subprocess.run(
        ["/bin/bash", "-n", str(LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
    fake_bin, invocation = _inner_bash(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "E2E_API_PORT": "18014",
        "E2E_WEB_PORT": "15188",
        "E2E_CORE_MODE": "core",
    }

    result = subprocess.run(
        ["/bin/bash", str(LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert invocation.read_text(encoding="utf-8").strip() == (
        "esports|http://127.0.0.1:18014|http://127.0.0.1:15188|"
        f"{CORE_LAUNCHER}"
    )


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("E2E_API_URL", "https://production.example/api"),
        ("E2E_BASE_URL", "https://production.example"),
    ],
)
def test_wrapper_rejects_non_loopback_origin_before_exec(
    tmp_path: Path,
    variable: str,
    value: str,
) -> None:
    fake_bin, invocation = _inner_bash(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            variable: value,
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "只允许当前隔离 runner 的 loopback origin" in result.stderr
    assert not invocation.exists()


def test_core_rejects_unknown_mode_before_environment_or_processes(tmp_path: Path) -> None:
    python_wrapper, invocation_log = _python_wrapper(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(CORE_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        # Missing required secrets must not mask unknown-mode rejection. Any
        # premature Python/Vite child also leaves the executable sentinel log.
        env={
            "PATH": os.environ["PATH"],
            "E2E_CORE_MODE": "production",
            "E2E_PYTHON_BIN": str(python_wrapper),
            "E2E_VITE_BIN": str(python_wrapper),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == "E2E_CORE_MODE 只允许 core、talent、esports、reactionx、invited-housing 或 all\n"
    assert not invocation_log.exists()


def _python_wrapper(tmp_path: Path) -> tuple[Path, Path]:
    invocation_log = tmp_path / "python-invocations.log"
    wrapper = tmp_path / "python-wrapper"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(invocation_log))}\n"
        "if [[ \"${1:-}\" == -m && ( \"${2:-}\" == app.cli.e2e_database_safety "
        "|| \"${2:-}\" == app.cli.e2e_port_safety ) ]]; then\n"
        f"  exec {shlex.quote(os.fsdecode(os.fsencode(sys.executable)))} \"$@\"\n"
        "fi\n"
        "exit 91\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    return wrapper, invocation_log


def test_missing_required_secret_is_rejected_before_any_cli(tmp_path: Path) -> None:
    python_wrapper, invocation_log = _python_wrapper(tmp_path)
    fake_vite = tmp_path / "vite"
    fake_vite.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    fake_vite.chmod(0o700)
    env = {
        **_base_env(),
        "E2E_CORE_MODE": "esports",
        "E2E_PYTHON_BIN": str(python_wrapper),
        "E2E_VITE_BIN": str(fake_vite),
    }
    del env["FIELD_ENCRYPTION_MASTER_KEY"]

    result = subprocess.run(
        ["/bin/bash", str(CORE_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "FIELD_ENCRYPTION_MASTER_KEY 未设置" in result.stderr
    assert not invocation_log.exists()


@pytest.mark.parametrize("occupied_kind", ["api", "web"])
def test_occupied_port_is_rejected_before_migration(
    tmp_path: Path,
    occupied_kind: str,
) -> None:
    python_wrapper, invocation_log = _python_wrapper(tmp_path)
    fake_vite = tmp_path / "vite"
    fake_vite.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    fake_vite.chmod(0o700)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied = int(listener.getsockname()[1])
        api_port = occupied if occupied_kind == "api" else 18014
        web_port = occupied if occupied_kind == "web" else 15188
        result = subprocess.run(
            ["/bin/bash", str(CORE_LAUNCHER)],
            cwd=REPOSITORY_ROOT,
            env={
                **_base_env(),
                "E2E_CORE_MODE": "esports",
                "E2E_PYTHON_BIN": str(python_wrapper),
                "E2E_VITE_BIN": str(fake_vite),
                "E2E_API_PORT": str(api_port),
                "E2E_WEB_PORT": str(web_port),
                "E2E_API_URL": f"http://127.0.0.1:{api_port}",
                "E2E_BASE_URL": f"http://127.0.0.1:{web_port}",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    invocations = (
        invocation_log.read_text(encoding="utf-8")
        if invocation_log.exists()
        else ""
    )
    assert result.returncode != 0
    assert f"本地端口已被其他进程占用: {occupied}" in result.stderr
    assert "-m alembic" not in invocations


def test_unsafe_database_is_rejected_before_migration(tmp_path: Path) -> None:
    python_wrapper, invocation_log = _python_wrapper(tmp_path)
    fake_vite = tmp_path / "vite"
    fake_vite.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    fake_vite.chmod(0o700)

    result = subprocess.run(
        ["/bin/bash", str(CORE_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env={
            **_base_env(),
            "TEST_DATABASE_URL": "postgresql+psycopg://erp:secret@db/prod_erp",
            "E2E_CORE_MODE": "esports",
            "E2E_PYTHON_BIN": str(python_wrapper),
            "E2E_VITE_BIN": str(fake_vite),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    invocations = invocation_log.read_text(encoding="utf-8")
    assert result.returncode != 0
    assert "隔离测试库" in result.stderr
    assert "-m app.cli.e2e_database_safety" in invocations
    assert "-m alembic" not in invocations


def test_missing_python_fails_closed_before_any_cli(tmp_path: Path) -> None:
    missing_python = tmp_path / "missing-python"

    result = subprocess.run(
        ["/bin/bash", str(CORE_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env={
            **_base_env(),
            "E2E_CORE_MODE": "esports",
            "E2E_PYTHON_BIN": str(missing_python),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "本地 Python 虚拟环境不可用" in result.stderr


def test_missing_vite_fails_closed_before_migration(tmp_path: Path) -> None:
    python_wrapper, invocation_log = _python_wrapper(tmp_path)

    result = subprocess.run(
        ["/bin/bash", str(CORE_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env={
            **_base_env(),
            "E2E_CORE_MODE": "esports",
            "E2E_PYTHON_BIN": str(python_wrapper),
            "E2E_VITE_BIN": str(tmp_path / "missing-vite"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    invocations = invocation_log.read_text(encoding="utf-8")
    assert result.returncode == 2
    assert "本地 Vite 可执行文件不可用" in result.stderr
    assert "-m alembic" not in invocations


def _environment_recorder(path: Path, *, append: bool) -> str:
    redirect = ">>" if append else ">"
    lines = [
        f"printf '%s\\n' 'invocation' {redirect} {shlex.quote(str(path))}",
    ]
    lines.extend(
        f"printf '%s=%s\\n' {shlex.quote(name)} \"${{{name}-<unset>}}\" "
        f">> {shlex.quote(str(path))}"
        for name in CHILD_SECRET_NAMES
    )
    return "\n".join(lines)


def test_vite_and_playwright_children_receive_no_application_secrets(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    vite_capture = tmp_path / "vite-environment.log"
    browser_capture = tmp_path / "browser-environment.log"

    fake_python = fake_bin / "python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *\"uvicorn app.main:app\"* ]]; then\n"
        "  printf '%s\\n' 'Uvicorn running on http://127.0.0.1:18014 ('\n"
        "  trap 'exit 0' TERM INT\n"
        "  while true; do /bin/sleep 1; done\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o700)

    fake_vite = fake_bin / "vite"
    fake_vite.write_text(
        "#!/usr/bin/env bash\n"
        f"{_environment_recorder(vite_capture, append=False)}\n"
        "printf '%s\\n' 'Local:   http://127.0.0.1:15188/'\n"
        "trap 'exit 0' TERM INT\n"
        "while true; do /bin/sleep 1; done\n",
        encoding="utf-8",
    )
    fake_vite.chmod(0o700)

    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        "#!/usr/bin/env bash\n"
        f"{_environment_recorder(browser_capture, append=True)}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o700)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_curl.chmod(0o700)

    secret_env = {
        **_base_env(),
        "DATABASE_URL": "postgresql+psycopg://private:secret@db/private",
        "REGISTRATION_VERIFICATION_PEPPER": "registration-verification-secret",
        "REGISTRATION_SESSION_TOKEN_PEPPER": "registration-session-secret",
        "E2E_CORE_MODE": "esports",
        "E2E_PYTHON_BIN": str(fake_python),
        "E2E_VITE_BIN": str(fake_vite),
        "E2E_API_PORT": "18014",
        "E2E_WEB_PORT": "15188",
        "E2E_API_URL": "http://127.0.0.1:18014",
        "E2E_BASE_URL": "http://127.0.0.1:15188",
        "ESPORTS_E2E_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["/bin/bash", str(CORE_LAUNCHER)],
        cwd=REPOSITORY_ROOT,
        env=secret_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    vite_environment = vite_capture.read_text(encoding="utf-8")
    browser_environment = browser_capture.read_text(encoding="utf-8")
    vite_lines = vite_environment.splitlines()
    browser_lines = browser_environment.splitlines()
    assert browser_lines.count("invocation") == 6
    for name in CHILD_SECRET_NAMES:
        assert f"{name}=<unset>" in vite_lines
        assert browser_lines.count(f"{name}=<unset>") == 6
        assert secret_env[name] not in vite_environment
        assert secret_env[name] not in browser_environment


def test_production_image_does_not_copy_esports_e2e_cli_tools() -> None:
    dockerfile = (REPOSITORY_ROOT / "apps/api/Dockerfile").read_text(encoding="utf-8")
    production = dockerfile.split("FROM base AS production", 1)[1].split(
        "FROM base AS e2e", 1
    )[0]

    assert "seed_esports_assessments_e2e" not in production
    assert "summarize_esports_assessments_e2e" not in production


def test_playwright_projects_isolate_esports_specs_and_private_artifacts() -> None:
    source = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")

    # Resolve the real config: the five ordinary projects now share an esports
    # AND ReactionX ignore list. Check ownership/privacy, not its source alias.
    script = """
import config from './apps/web/playwright.config.ts'
const files = [
  'esports-pc.spec.ts', 'esports-touch.spec.ts', 'esports-recovery.spec.ts',
  'esports-capabilities.spec.ts', 'reactionx-integration.spec.ts',
  'platform-foundation.spec.ts', 'talent-tests.spec.ts',
]
function matches(pattern, file, fallback) {
  if (pattern === undefined) return fallback
  return (Array.isArray(pattern) ? pattern : [pattern]).some(item => {
    if (!(item instanceof RegExp)) throw new Error('expected regex project boundary')
    return item.test(file)
  })
}
process.stdout.write(JSON.stringify(config.projects.map(project => ({
  name: project.name,
  owns: files.filter(file => matches(project.testMatch, file, true)
    && !matches(project.testIgnore, file, false)),
  privacy: [project.use?.trace, project.use?.screenshot, project.use?.video],
}))))
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPOSITORY_ROOT, env=os.environ, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    projects = {project["name"]: project for project in json.loads(result.stdout)}
    ordinary = {"desktop-1440", "desktop-1024", "tablet-768", "mobile-360", "touch-mobile-390"}
    private = {
        "esports-desktop-1440": ["esports-pc.spec.ts", "esports-recovery.spec.ts", "esports-capabilities.spec.ts"],
        "esports-touch-844x390": ["esports-touch.spec.ts", "esports-capabilities.spec.ts"],
        "esports-webkit-capabilities": ["esports-capabilities.spec.ts"],
        "reactionx-desktop-1440": ["reactionx-integration.spec.ts"],
    }
    assert set(projects) == ordinary | set(private)
    for name in ordinary:
        assert projects[name]["owns"] == ["platform-foundation.spec.ts", "talent-tests.spec.ts"]
    for name, owned in private.items():
        assert projects[name]["owns"] == owned
        assert projects[name]["privacy"] == ["off", "off", "off"]
    assert 'name: "esports-desktop-1440"' in source
    assert 'name: "esports-touch-844x390"' in source
    assert 'name: "esports-webkit-capabilities"' in source
    assert 'grep: /EA-(?:PC|REC|CAP-(?:DESKTOP|NONPHONE))/' in source
    assert 'grep: /EA-(?:TOUCH|CAP-(?:RESPONSIVE|ROTATE))/' in source
    assert 'grep: /EA-CAP-WEBKIT/' in source
    assert 'viewport: { width: 844, height: 390 }' in source
    assert "hasTouch: true" in source
    assert "isMobile: true" in source
    assert 'browserName: "webkit"' in source
    global_config = source.split("export default", 1)[1].split("projects:", 1)[0]
    assert "launchOptions:" not in global_config

    webkit_block = source.split('name: "esports-webkit-capabilities"', 1)[1]
    assert "executablePath" not in webkit_block


def test_esports_touch_project_resolves_native_phone_context() -> None:
    script = """
import config from './apps/web/playwright.config.ts'
const project = config.projects?.find(item => item.name === 'esports-touch-844x390')
if (!project) throw new Error('missing esports touch project')
process.stdout.write(JSON.stringify(project.use))
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPOSITORY_ROOT,
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)
    assert re.search(r"iPhone|Android.*Mobile", context["userAgent"])
    assert context["hasTouch"] is True
    assert context["isMobile"] is True
    assert context["viewport"] == {"width": 844, "height": 390}
    assert context["defaultBrowserType"] == "chromium"
    assert context["deviceScaleFactor"] > 1
    assert context["screen"]["width"] > context["screen"]["height"]

    collection = subprocess.run(
        [
            str(REPOSITORY_ROOT / "apps/web/node_modules/.bin/playwright"),
            "test",
            "--list",
            "--project=esports-desktop-1440",
            "--project=esports-touch-844x390",
            "--project=esports-webkit-capabilities",
            "tests/esports-pc.spec.ts",
            "tests/esports-touch.spec.ts",
            "tests/esports-recovery.spec.ts",
            "tests/esports-capabilities.spec.ts",
        ],
        cwd=REPOSITORY_ROOT / "apps/web",
        env={**os.environ, "ESPORTS_E2E_FIXED_BOARD_ACCEPTANCE": "0"},
        capture_output=True,
        text=True,
        check=False,
    )
    expected_ids = {
        "EA-PC-REAL-001",
        "EA-PC-REAL-002",
        "EA-TOUCH-REAL-001",
        "EA-TOUCH-REAL-002",
        "EA-REC-FORMAL-001",
        "EA-REC-PRACTICE-002",
        "EA-REC-MULTITAB-003",
        "EA-CAP-DESKTOP-001",
        "EA-CAP-NONPHONE-002",
        "EA-CAP-RESPONSIVE-003",
        "EA-CAP-ROTATE-004",
        "EA-CAP-WEBKIT-005",
    }
    assert collection.returncode == 0, collection.stderr
    assert set(re.findall(r"› (EA-[A-Z][A-Z0-9-]*-\d{3})\b", collection.stdout)) == expected_ids
    assert "Total: 12 tests in 4 files" in collection.stdout
    default_pc_cases = tuple(re.findall(r"› (EA-PC-[A-Z0-9-]+)\b", collection.stdout))
    assert default_pc_cases == REQUIRED_CASE_IDS[("esports-pc.spec.ts", "esports-desktop-1440")]

    opt_in = subprocess.run(
        [
            str(REPOSITORY_ROOT / "apps/web/node_modules/.bin/playwright"),
            "test", "--list", "--project=esports-desktop-1440",
            "--grep=EA-PC-V5", "tests/esports-pc.spec.ts",
        ],
        cwd=REPOSITORY_ROOT / "apps/web",
        env={**os.environ, "ESPORTS_E2E_FIXED_BOARD_ACCEPTANCE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert opt_in.returncode == 0, opt_in.stderr
    assert re.findall(r"› (EA-PC-[A-Z0-9-]+)\b", opt_in.stdout) == ["EA-PC-V5-001"]
    assert "Total: 1 test in 1 file" in opt_in.stdout
