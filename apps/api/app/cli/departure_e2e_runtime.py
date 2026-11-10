"""Validate the controller-owned database before departure tests or seeding."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from sqlalchemy import make_url

from app.cli.e2e_database_safety import require_e2e_database_url
from app.core.config import Settings, get_settings

LABEL = "erp.assisted.restore.owner"
API_TESTS = (
    "tests/api/test_security_rbac_boundaries.py",
    "tests/api/test_security_people_mutations.py",
    "tests/api/test_security_housing_boundaries.py",
    "tests/api/test_security_money_bounds.py",
    "tests/unit/test_security_money_bounds.py",
    "tests/api/test_account_management_boundaries.py",
    "tests/api/test_people.py",
    "tests/api/test_guardians.py",
    "tests/api/test_person_housing.py",
    "tests/api/test_housing_room_status_export.py",
    "tests/cli/test_seed_departure_reminders_e2e.py",
    "tests/cli/test_departure_e2e_browser.py",
    "tests/cli/test_departure_owned_gate.py",
    "tests/cli/test_housing_care_owned_gate.py",
    "tests/unit/portal/test_departure_dates.py",
    "tests/unit/portal/test_departure_schemas.py",
    "tests/services/test_departure_access.py",
    "tests/services/test_departure_reminders.py",
    "tests/db/test_departure_read_consistency.py",
    "tests/db/test_migrations.py",
    "tests/api/test_departure_reminders.py",
    "tests/api/test_portal.py",
    "tests/api/test_permissions.py",
    "tests/services/test_enrollments.py",
    "tests/api/test_housing.py",
    "tests/db/test_housing_inventory_service.py",
    "tests/api/test_housing_cleaning.py",
    "tests/api/test_admissions.py",
    "tests/services/test_managed_people_housing.py",
    "tests/services/test_invited_housing_projection.py",
    "tests/services/test_invited_housing_access.py",
    "tests/services/test_housing_portal.py",
    "tests/services/test_portal_learning.py",
    "tests/services/test_portal_coach.py",
    "tests/unit/housing/test_access.py",
)


class DepartureRuntimeRefused(RuntimeError):
    pass


def _private_path(path: Path, mode: int) -> None:
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or stat.S_IMODE(details.st_mode) != mode
        or details.st_uid != os.getuid()
        or (mode == 0o700 and not stat.S_ISDIR(details.st_mode))
        or (mode == 0o600 and not stat.S_ISREG(details.st_mode))
    ):
        raise ValueError


def require_departure_database(settings: Settings) -> None:
    """Fail closed, without exposing credentials or trusting an ambient test DSN."""
    try:
        require_e2e_database_url(settings.database_url)
        ledger = Path(os.environ.get("E2E_DEPARTURE_RUNTIME_LEDGER", ""))
        if not ledger.is_absolute() or ledger.name != "runtime.json":
            raise ValueError
        _private_path(ledger, 0o600)
        _private_path(ledger.parent, 0o700)
        _private_path(ledger.parent / "owner", 0o600)
        if not re.fullmatch(
            r"assisted-restore-0069\.departure_gate_[A-Za-z0-9_]+", ledger.parent.name
        ):
            raise ValueError
        owned = json.loads(ledger.read_text())
        url = make_url(settings.database_url)
        owner = owned["owner"]
        endpoint = owned["docker_endpoint"]
        image = owned["postgres_image"]
        identifier, network, volume = (
            owned[key] for key in ("container_id", "network_id", "volume")
        )
        if (
            settings.app_env != "test"
            or owned["test_database_url"] != settings.database_url
            or os.environ.get("DATABASE_URL") != owned["test_database_url"]
            or os.environ.get("TEST_DATABASE_URL") != owned["test_database_url"]
            or owned["task_label"] != "departure-reminders-20261024"
            or re.fullmatch(r"[0-9a-f]{32}", owner) is None
            or (ledger.parent / "owner").read_text() != owner
            or any(
                re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (identifier, network, volume)
            )
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None
            or re.fullmatch(r"unix:///[^\r\n\x00]+", endpoint) is None
            or os.environ.get("DOCKER_HOST") != endpoint
            or url.drivername != "postgresql+psycopg"
            or url.host != "127.0.0.1"
            or url.username != "erp_e2e"
            or not url.password
            or url.database != "erp_departure_reminders_test"
            or url.port is None
            or not 1024 < url.port <= 65535
            or url.port in {5432, 55433}
            or url.query
        ):
            raise ValueError
        environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
        environment["DOCKER_HOST"] = endpoint

        def inspect(*command: str) -> dict[str, Any]:
            result = subprocess.run(
                ["docker", *command],
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            rows = json.loads(result.stdout)
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError
            return cast(dict[str, Any], rows[0])

        container = inspect("inspect", identifier)
        host = container["HostConfig"]
        network_settings = container["NetworkSettings"]
        attachments = network_settings["Networks"]
        if (
            container["Id"] != identifier
            or container["Image"] != image
            or container["Config"]["Labels"].get(LABEL) != owner
            or container["State"].get("Running") is not True
            or host.get("NetworkMode") != network
            or host.get("Binds")
            or host.get("PublishAllPorts")
            or host.get("PortBindings") != {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]}
            or network_settings["Ports"]
            != {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(url.port)}]}
            or len(attachments) != 1
            or next(iter(attachments.values()))["NetworkID"] != network
            or len(container["Mounts"]) != 1
            or container["Mounts"][0].get("Type") != "volume"
            or container["Mounts"][0].get("Name") != volume
        ):
            raise ValueError
        live_network = inspect("network", "inspect", network)
        if (
            live_network["Id"] != network
            or live_network["Labels"].get(LABEL) != owner
            or live_network.get("Driver") != "bridge"
            or live_network.get("Internal") is not False
            or set(live_network["Containers"]) != {identifier}
        ):
            raise ValueError
    except Exception:  # noqa: BLE001 - no DSN/command or private ledger in errors.
        raise DepartureRuntimeRefused(
            "Departure checks require the exact task-owned local database"
        ) from None


def run_api_tests(validated_database_url: str, *, full_suite: bool = False) -> int:
    # A fresh interpreter lets pytest install assertion rewriting before app or
    # plugin imports. Inherit only controller-generated test configuration, not
    # ambient Python/pytest hooks. The child stays in the controller's PGID.
    allowed = {
        "PATH",
        "HOME",
        "DOCKER_HOST",
        "APP_ENV",
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "E2E_DEPARTURE_RUNTIME_LEDGER",
        "E2E_PYTHON_BIN",
        "FIELD_ENCRYPTION_MASTER_KEY",
        "FIELD_LOOKUP_KEY",
        "SESSION_TOKEN_PEPPER",
        "REGISTRATION_VERIFICATION_PEPPER",
        "REGISTRATION_SESSION_TOKEN_PEPPER",
        "EVALUATION_TOOL_SERVICE_TOKEN",
        "EVALUATION_TOOL_WEB_SERVICE_TOKEN",
        "EVALUATION_TOOL_WORKER_SERVICE_TOKEN",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    # pytest's reset fixture reads TEST_DATABASE_URL, while app settings read
    # DATABASE_URL. Bind both to the same already-validated immutable value;
    # never re-read either ambient selector after the ownership guard.
    environment["DATABASE_URL"] = validated_database_url
    environment["TEST_DATABASE_URL"] = validated_database_url
    # Migration fixtures encrypt/digest legacy values with the same fixed test
    # key that conftest supplies when these fields are absent. The owned
    # controller's random keys remain intact for its browser and seed phases.
    migration_test_key = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
    environment["FIELD_ENCRYPTION_MASTER_KEY"] = migration_test_key
    environment["FIELD_LOOKUP_KEY"] = migration_test_key
    # This controller has already proved the private ledger and live bind.
    # Only its pytest child may enter the destructive seed acceptance case.
    environment["E2E_DEPARTURE_OWNED_ACCEPTANCE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--tb=short", "-ra", *(() if full_suite else API_TESTS)],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=False,
    )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("api", "browser", "all"), required=True)
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        validated_database_url = settings.database_url
        require_departure_database(settings)
    except DepartureRuntimeRefused:
        print("Departure checks require the exact task-owned local database", file=sys.stderr)
        return 2
    if args.phase == "api":
        return run_api_tests(validated_database_url)
    if args.phase == "all":
        status = run_api_tests(validated_database_url, full_suite=True)
        if status:
            return status
    from app.cli.departure_e2e_browser import run_browser

    return run_browser(settings)


if __name__ == "__main__":
    raise SystemExit(main())
