from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = REPOSITORY_ROOT / "scripts/run-self-service-registration-e2e.sh"
WEB_HELPERS = REPOSITORY_ROOT / "apps/web/tests/helpers.ts"
PLAYWRIGHT_CONFIG = REPOSITORY_ROOT / "apps/web/playwright.config.ts"


def test_registration_launcher_is_local_serial_and_secret_safe() -> None:
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
    assert ': "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"' in source
    assert "-m app.cli.e2e_database_safety" in source
    assert source.count("-m app.cli.e2e_port_safety") == 1
    assert "projects=(desktop-1440 desktop-1024 tablet-768 mobile-360)" in source
    assert (
        "specs=(self-service-registration.spec.ts "
        "self-service-registration-privacy.spec.ts)"
    ) in source
    assert source.count("run_erp_cli -m app.cli.seed_e2e") == 1
    assert source.count("run_erp_cli -m app.cli.seed_registration_e2e") == 1
    assert 'for spec in "${specs[@]}"; do' in source
    assert 'run_playwright "$spec" "$project"' in source
    assert "REGISTRATION_TEST_MAILBOX_DIR" in source
    assert "REGISTRATION_PHONE_SIGNUP_ENABLED=true" in source
    assert SENTINEL_NAME in source
    assert "-u TEST_DATABASE_URL" in source
    assert "-u DATABASE_URL" in source
    assert 'PLAYWRIGHT_OUTPUT_DIR="$tmp_dir/playwright-results"' in source
    assert 'PLAYWRIGHT_HTML_REPORT_DIR="$tmp_dir/playwright-report"' in source
    assert "app.cli.redact_registration_e2e_output" in source
    assert "docker" not in source.casefold()
    assert "captured-code.json" not in source


def test_registration_code_reader_subprocess_uses_an_environment_allowlist() -> None:
    source = WEB_HELPERS.read_text(encoding="utf-8")

    assert "...process.env" not in source
    assert 'APP_ENV: "test"' in source
    assert "REGISTRATION_TEST_MAILBOX_DIR: mailbox" in source
    assert "TEST_DATABASE_URL" not in source
    assert "DATABASE_URL" not in source


def test_registration_playwright_artifacts_can_be_scoped_to_runner_temp_dir() -> None:
    source = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")

    assert "process.env.PLAYWRIGHT_OUTPUT_DIR" in source
    assert "process.env.PLAYWRIGHT_HTML_REPORT_DIR" in source


def test_registration_e2e_redactor_removes_complete_sensitive_values() -> None:
    from app.cli.redact_registration_e2e_output import redact_text

    secrets = (
        "bearer-secret-token",
        "session-secret",
        "secondary-cookie-secret",
        "csrf-secret",
        "password-secret",
        "246810",
        "postgresql+psycopg://user:pass@localhost/private_db",
    )
    value = (
        "Authorization: Bearer bearer-secret-token\n"
        "Cookie: erp_session=session-secret; other=secondary-cookie-secret\n"
        "X-CSRF-Token: csrf-secret\n"
        '{"password": "password-secret", "code": "246810"}\n'
        "DATABASE_URL=postgresql+psycopg://user:pass@localhost/private_db"
    )

    redacted = redact_text(value)

    for secret in secrets:
        assert secret not in redacted
    assert "[REDACTED]" in redacted
    assert "[DATABASE_URL]" in redacted


def test_registration_e2e_redactor_removes_registration_csrf_header_value() -> None:
    from app.cli.redact_registration_e2e_output import redact_text

    secret = "registration-csrf-secret"

    redacted = redact_text(f"X-Registration-CSRF-Token: {secret}\n")

    assert redacted == "X-Registration-CSRF-Token: [REDACTED]\n"
    assert secret not in redacted


def test_registration_e2e_redactor_removes_unlabeled_browser_secrets() -> None:
    from app.cli.redact_registration_e2e_output import redact_text

    invitation_token = "A" * 43
    verification_code = "246810"
    password = "Valid-password-2026"
    value = (
        f"navigating to /guardian-invitation#{invitation_token}\n"
        f"locator.fill(\"{password}\")\n"
        f"received text: {verification_code}\n"
    )

    redacted = redact_text(value)

    assert invitation_token not in redacted
    assert verification_code not in redacted
    assert password not in redacted
    assert redacted.count("[REDACTED]") == 3


SENTINEL_NAME = ".registration-test-mailbox"
