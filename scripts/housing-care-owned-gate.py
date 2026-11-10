"""Run housing-care E2Es on a newly owned local PostgreSQL, never an ambient DSN."""

from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

POSTGRES_IMAGE = (
    "sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"
)
SPEC = "housing-cleaning-photos-export.spec.ts"
LABEL = "erp.assisted.restore.owner"


class GateProfile(NamedTuple):
    database: str
    task_label: str
    ledger_environment: str
    launcher: str
    scratch_prefix: str
    network_prefix: str
    spec: str


PROFILES = {
    "housing-care": GateProfile(
        "erp_housing_care_test", "housing-care-20261023",
        "E2E_HOUSING_CARE_RUNTIME_LEDGER", "scripts/run-housing-care-e2e.sh",
        "assisted-restore-0069.care_gate_", "housing-care-gate-", SPEC,
    ),
    "departure-reminders": GateProfile(
        "erp_departure_reminders_test", "departure-reminders-20261024",
        "E2E_DEPARTURE_RUNTIME_LEDGER", "scripts/run-departure-reminders-checks.sh",
        "assisted-restore-0069.departure_gate_", "departure-reminders-gate-", "departure-reminders.spec.ts",
    ),
}


def ownership_reference(root):
    path = root / "apps/api/tests/cli/test_assisted_personnel_backup_docker.py"
    spec = importlib.util.spec_from_file_location("housing_care_gate_ownership", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_launcher(command, *, env, cwd, timeout=1800):
    child = subprocess.Popen(command, env=env, cwd=cwd, start_new_session=True)
    try:
        return subprocess.CompletedProcess(command, child.wait(timeout=timeout))
    except BaseException:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            # Do not reap/poll the session leader during the grace period: its
            # reserved PID anchors our PGID even if it exits before descendants.
            time.sleep(3)
        finally:
            try:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Darwin reports EPERM for a zombie-only group. Accept it
                    # only after proving this exact pinned PGID has no live
                    # members; never swallow permissions errors for live groups.
                    if sys.platform != "darwin":
                        raise
                    group = subprocess.run(
                        ["ps", "-o", "pgid=,stat=", "-g", str(child.pid)],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        check=False,
                    )
                    rows = [row.split() for row in group.stdout.splitlines()]
                    if group.returncode or not rows or any(
                        len(row) != 2
                        or row[0] != str(child.pid)
                        or not row[1].startswith("Z")
                        for row in rows
                    ):
                        raise
            finally:
                child.wait(timeout=3)
        raise


def run_gate(
    root,
    python_bin,
    image,
    environment,
    *,
    profile="housing-care",
    phase=None,
    process=subprocess.run,
    launcher=run_launcher,
    scratch_parent=None,
):
    """The only external mutation boundary is process; all lifecycle checks are real."""
    result = {
        "exit_code": 2,
        "cleanup_failed": False,
        "owned_containers_removed": 0,
        "owned_containers_remaining": 0,
        "private_scratch_removed": True,
        "spec": SPEC,
        "delegate": "scripts/run-housing-care-e2e.sh",
    }
    selected_phase = phase
    owner, network, scratch, phase = None, None, None, "preflight"
    old_umask = os.umask(0o077)
    clean = {key: environment[key] for key in ("PATH", "HOME") if environment.get(key)}

    def execute(command, **kwargs):
        kwargs.setdefault("env", clean)
        kwargs.setdefault("timeout", 60)
        return process(command, **kwargs)

    def docker(*args):
        return execute(["docker", *args], capture_output=True, check=True).stdout

    def inspect_network(*, empty=False):
        if not isinstance(network, str) or not re.fullmatch(r"[0-9a-f]{64}", network):
            raise ValueError("network_id_not_captured")
        details = json.loads(docker("network", "inspect", network))[0]
        members = set(details.get("Containers", {}))
        if (
            details.get("Id") != network
            or details.get("Labels", {}).get(LABEL) != owner.owner
            or details.get("Internal") is not False
            or details.get("Driver") != "bridge"
            or not members <= set(owner.records)
            or empty
            and members
        ):
            raise ValueError("network_ownership_mismatch")
        return details

    try:
        config = PROFILES.get(profile)
        if config is None:
            raise ValueError("unknown_profile")
        selected_phase = selected_phase or ("all" if profile == "housing-care" else "api")
        if selected_phase not in {"api", "browser", "all"}:
            raise ValueError("unknown_phase")
        result.update(spec=config.spec, delegate=config.launcher)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("immutable_image_required")
        host, context = (
            environment.get("DOCKER_HOST"),
            environment.get("DOCKER_CONTEXT"),
        )
        if context and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", context):
            raise ValueError("local_docker_endpoint_required")
        if context or not host:
            args = ["docker", "context", "inspect"] + ([context] if context else [])
            endpoint = execute(
                [*args, "--format", "{{.Endpoints.docker.Host}}"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip()
        else:
            endpoint = host
        if not re.fullmatch(r"unix:///[^\r\n\x00]+", endpoint):
            raise ValueError("local_docker_endpoint_required")
        clean["DOCKER_HOST"] = endpoint
        if (
            docker("image", "inspect", "--format", "{{.Id}}", image).decode().strip()
            != image
        ):
            raise ValueError("immutable_image_mismatch")
        reference = ownership_reference(root)
        # Loaded module-local seam only; never monkeypatch global subprocess.
        reference.subprocess = SimpleNamespace(run=execute)

        class GateOwner(reference.OwnedDocker):
            started = False
            mapped_port = None

            def guard(self, target):
                identifier = self.names.get(target, target)
                if identifier not in self.records or identifier in self.blocked:
                    raise ValueError("unowned_container_refused")
                details = self.inspect(identifier)
                label, expected_network, volumes = self.records[identifier]
                declared = details.get("HostConfig", {}).get("PortBindings", {})
                actual = details.get("NetworkSettings", {}).get("Ports", {})
                attachments = details.get("NetworkSettings", {}).get("Networks", {})
                allowed_ids = (
                    {expected_network} if self.started else {"", expected_network}
                )
                if (
                    len(attachments) != 1
                    or next(iter(attachments.values()), {}).get("NetworkID")
                    not in allowed_ids
                ):
                    raise ValueError("network_ownership_mismatch")
                requested = [{"HostIp": "127.0.0.1", "HostPort": ""}]
                if (
                    declared != {"5432/tcp": requested}
                    or details.get("Image") != image
                    or details.get("HostConfig", {}).get("PublishAllPorts")
                ):
                    raise ValueError("loopback_binding_ownership_mismatch")
                if self.started or actual:
                    binding = actual.get("5432/tcp", [])
                    if (
                        set(actual) != {"5432/tcp"}
                        or not isinstance(binding, list)
                        or len(binding) != 1
                        or binding[0].get("HostIp") != "127.0.0.1"
                        or not re.fullmatch(
                            r"[0-9]{1,5}", binding[0].get("HostPort", "")
                        )
                    ):
                        raise ValueError("loopback_binding_ownership_mismatch")
                    port = int(binding[0]["HostPort"])
                    if port <= 1024 or port > 65535 or port in {5432, 55433}:
                        raise ValueError("loopback_binding_ownership_mismatch")
                    if self.mapped_port is not None and port != self.mapped_port:
                        raise ValueError("loopback_binding_ownership_mismatch")
                    self.mapped_port = port
                normalized = copy.deepcopy(details)
                # Narrow gate-only exception: exact single loopback DB binding was
                # proved above. All original restore ownership checks stay strict.
                normalized["HostConfig"]["PortBindings"] = {}
                captured = reference.check_owned(
                    normalized, identifier, label, expected_network, volumes
                )
                if len(captured) != 1:
                    raise ValueError("one_anonymous_database_volume_required")
                if volumes is None:
                    # Bind the first inspected volume in the same observation,
                    # not an independent second inspect vulnerable to drift.
                    self.records[identifier] = (label, expected_network, captured)
                return identifier

        owner = GateOwner((image,), "", "", {})
        parent = (
            Path(scratch_parent)
            if scratch_parent is not None
            else Path(tempfile.gettempdir())
        )
        scratch = Path(
            tempfile.mkdtemp(prefix=config.scratch_prefix, dir=parent)
        )
        owner.scratch = scratch
        reference.private_write(scratch / "owner", owner.owner)
        phase = "create_owned_network"
        network = (
            docker(
                "network",
                "create",
                "--driver",
                "bridge",
                "--label",
                LABEL + "=" + owner.owner,
                config.network_prefix + owner.owner,
            )
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[0-9a-f]{64}", network):
            raise ValueError("network_id_not_captured")
        owner.write_event("network_captured", network_id=network)
        inspect_network(empty=True)
        phase = "create_owned_database"
        password = secrets.token_urlsafe(32)
        identifier = (
            docker(
                "create",
                "--pull=never",
                "--label",
                LABEL + "=" + owner.owner,
                "--network",
                network,
                "--publish",
                "127.0.0.1::5432",
                "--memory",
                "1g",
                "--cpus",
                "1",
                "--security-opt",
                "no-new-privileges:true",
                "-e",
                "POSTGRES_USER=erp_e2e",
                "-e",
                "POSTGRES_DB=" + config.database,
                "-e",
                "POSTGRES_PASSWORD=" + password,
                image,
            )
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ValueError("container_id_not_captured")
        owner.records[identifier] = ((LABEL, owner.owner), network, None)
        owner.write_event("captured", identifier)
        owner.guard(identifier)
        volumes = owner.records[identifier][2]
        owner.write_event("validated", identifier)
        owner.guard(identifier)
        docker("start", identifier)
        owner.started = True
        phase = "owned_database_readiness"
        for _ in range(60):
            owner.guard(identifier)
            inspect_network()
            ready = execute(
                [
                    "docker",
                    "exec",
                    identifier,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "erp_e2e",
                    "-d",
                    config.database,
                ],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise ValueError("owned_database_not_ready")
        dsn = f"postgresql+psycopg://erp_e2e:{password}@127.0.0.1:{owner.mapped_port}/{config.database}"
        ledger = scratch / "runtime.json"
        reference.private_write(
            ledger,
            json.dumps(
                {
                    "task_label": config.task_label,
                    "owner": owner.owner,
                    "postgres_image": image,
                    "container_id": identifier,
                    "volume": volumes[0],
                    "test_database_url": dsn,
                    "docker_endpoint": endpoint,
                    "network_id": network,
                }
            ),
        )
        child = dict(clean)
        for key in ("PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_EXECUTABLE_PATH"):
            if environment.get(key):
                child[key] = environment[key]
        child.update(
            APP_ENV="test",
            TEST_DATABASE_URL=dsn,
            E2E_PYTHON_BIN=python_bin,
            E2E_API_PORT="18144",
            E2E_WEB_PORT="15144",
        )
        child[config.ledger_environment] = str(ledger)
        if profile == "departure-reminders":
            child["DATABASE_URL"] = dsn
        for key in (
            "FIELD_ENCRYPTION_MASTER_KEY",
            "FIELD_LOOKUP_KEY",
            "SESSION_TOKEN_PEPPER",
            "REGISTRATION_VERIFICATION_PEPPER",
            "REGISTRATION_SESSION_TOKEN_PEPPER",
            "EVALUATION_TOOL_SERVICE_TOKEN",
            "EVALUATION_TOOL_WEB_SERVICE_TOKEN",
            "EVALUATION_TOOL_WORKER_SERVICE_TOKEN",
        ):
            child[key] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        owner.guard(identifier)
        inspect_network()
        phase = "existing_care_launcher" if profile == "housing-care" else f"departure_{selected_phase}_launcher"
        command = ["bash", str(root / config.launcher)]
        if profile == "departure-reminders":
            command.append(selected_phase)
        status = launcher(
            command,
            cwd=root,
            env=child,
            timeout=(5400 if selected_phase == "all" else 3600 if selected_phase == "browser" else 1800) if profile == "departure-reminders" else 1800,
        ).returncode
        result.update(
            exit_code=status,
            postgres_image=image,
            owner=owner.owner,
            owned_container_id=identifier,
            owned_network_id=network,
            anonymous_volume_id=volumes[0],
            database_port=owner.mapped_port,
        )
    except KeyboardInterrupt:
        result.update(exit_code=130, error_code="interrupted", failed_phase=phase)
    except Exception:  # noqa: BLE001 - never stringify command/config/private values.
        result.update(exit_code=2, error_code="owned_gate_failed", failed_phase=phase)
    finally:
        if owner is not None and scratch is not None:
            try:
                for identifier in list(owner.records):
                    owner.remove(identifier)
                if network is not None:
                    inspect_network(empty=True)
                    docker("network", "rm", network)
                    owner.write_event("network_removed", network_id=network)
                cleanup = owner.cleanup_owned(passed=True, parent=scratch.parent)
                result.update(cleanup)
            except Exception:  # noqa: BLE001 - ownership failure retains evidence/resources.
                result.update(
                    exit_code=result["exit_code"] or 2,
                    cleanup_failed=True,
                    owned_containers_remaining=len(owner.records),
                    owned_containers_removed=len(owner.removed),
                    private_scratch_removed=False,
                    retained_directory=scratch.name,
                )
        os.umask(old_umask)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--postgres-image", default=POSTGRES_IMAGE)
    parser.add_argument("--profile", choices=PROFILES, default="housing-care")
    parser.add_argument("--phase", choices=("api", "browser", "all"))
    args = parser.parse_args()
    signal.signal(
        signal.SIGTERM, lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    result = run_gate(
        Path(__file__).resolve().parents[1],
        args.python,
        args.postgres_image,
        os.environ,
        profile=args.profile,
        phase=args.phase,
    )
    print(json.dumps(result, sort_keys=True))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
