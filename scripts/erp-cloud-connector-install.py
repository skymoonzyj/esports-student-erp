#!/usr/bin/env python3
"""Explicitly register an ERP-dedicated OpenList; default is deployable pending."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from cloud_auth.recovery import (
    IMAGE,
    ORIGIN,
    DockerRuntime,
    RecoveryError,
    container_bind_user,
    docker_inspect_absent,
    load_registry,
    protected_file,
    remove_owned_container,
    verify_admin_credential,
)


def install(
    app_dir: Path,
    *,
    mode: str,
    project: str = "esports-erp-staging",
    connector_id: str | None = None,
    admin_secret_file: Path | None = None,
):
    app_dir = app_dir.resolve(strict=True)
    registry = app_dir / "cloud-connector/registry.json"
    if mode == "create":
        return create(app_dir, project=project, connector_id=connector_id)
    if mode == "pending":
        return {
            "state": "managed" if registry.exists() else "pending",
            "qr_capable": False,
        }
    if mode != "managed" or connector_id is None or admin_secret_file is None:
        raise RecoveryError("connector_explicit_registration_required")
    UUID(connector_id)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
        raise RecoveryError("connector_project_invalid")
    root = registry.parent
    data = root / "data"
    secret = root / "admin-secret"
    # Existing instances must already be explicitly labeled as ERP dedicated.
    # Never discover/adopt the optional legacy connector by its listening port.
    container = project + "-cloud-connector"
    result = subprocess.run(
        ["docker", "inspect", container], capture_output=True, check=True, timeout=15
    )
    details = json.loads(result.stdout)[0]
    labels = details["Config"].get("Labels", {})
    if (
        labels.get("erp.cloud.connector") != connector_id
        or labels.get("erp.cloud.dedicated") != "true"
        or details["Config"]["Image"] not in (IMAGE, details["Image"])
    ):
        raise RecoveryError("connector_ownership_mismatch")
    if details["Config"]["Image"] != IMAGE:
        immutable = json.loads(
            subprocess.run(
                ["docker", "image", "inspect", details["Image"]],
                capture_output=True,
                check=True,
                timeout=15,
            ).stdout
        )[0]
        if (
            immutable.get("Config", {})
            .get("Labels", {})
            .get("org.opencontainers.image.version")
            != "v4.2.6"
        ):
            raise RecoveryError("connector_image_version_invalid")
    ports = details.get("HostConfig", {}).get("PortBindings", {})
    if ports != {"5244/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5244"}]}:
        raise RecoveryError("connector_origin_invalid")
    mounts = details.get("Mounts", [])
    if (
        len(mounts) != 1
        or mounts[0].get("Type") != "bind"
        or Path(mounts[0]["Source"]).resolve() != data.resolve()
        or mounts[0].get("Destination") != "/opt/openlist/data"
    ):
        raise RecoveryError("connector_mount_invalid")
    if root.is_symlink() or data.is_symlink() or not data.is_dir():
        raise RecoveryError("connector_directory_invalid")
    protected_file(admin_secret_file)
    if admin_secret_file.resolve() != secret.resolve():
        raise RecoveryError("connector_secret_location_invalid")
    value = {
        "version": 1,
        "managed": True,
        "state": "managed",
        "connector_version": "4.2.6",
        "connector_id": connector_id,
        "container_name": container,
        "image": IMAGE,
        "image_id": details["Image"],
        "origin": ORIGIN,
        "data_dir": str(data),
        "admin_secret_file": str(secret),
        "objects": [],
    }
    if registry.exists():
        old = load_registry(registry)
        if old["connector_id"] != connector_id or old["image_id"] != value["image_id"]:
            raise RecoveryError("connector_existing_registration_mismatch")
        return {"state": "managed", "qr_capable": False}
    # All validation precedes any permission/config mutations.
    os.chmod(root, 0o700)
    os.chmod(data, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".registry-", dir=root)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, registry)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"state": "managed", "qr_capable": False}


def create(app_dir, *, project, connector_id):
    if connector_id is None or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
        raise RecoveryError("connector_explicit_registration_required")
    UUID(connector_id)
    root = app_dir / "cloud-connector"
    if root.exists() or root.is_symlink():
        raise RecoveryError("connector_existing_directory_not_adopted")
    container = project + "-cloud-connector"
    existing = subprocess.run(
        ["docker", "inspect", container], capture_output=True, check=False, timeout=15
    )
    if existing.returncode != 1 or not docker_inspect_absent(
        existing.stderr, container
    ):
        raise RecoveryError("connector_existing_container_not_adopted")
    # Require a local fixed-version image. Pulling/installing software is explicit,
    # never triggered by an ERP button or a default pending deployment.
    available = subprocess.run(
        ["docker", "image", "inspect", IMAGE],
        capture_output=True,
        check=False,
        timeout=15,
    )
    if available.returncode:
        if available.returncode != 1 or not docker_inspect_absent(
            available.stderr, IMAGE
        ):
            raise RecoveryError("connector_image_inspect_failed")
        subprocess.run(
            ["docker", "pull", IMAGE], capture_output=True, check=True, timeout=120
        )
        available = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True,
            check=True,
            timeout=15,
        )
    image = json.loads(available.stdout)[0]
    if (
        image.get("Config", {})
        .get("Labels", {})
        .get("org.opencontainers.image.version")
        != "v4.2.6"
    ):
        raise RecoveryError("connector_image_version_invalid")
    root.mkdir(mode=0o700)
    data = root / "data"
    data.mkdir(mode=0o700)
    secret = root / "admin-secret"
    with secret.open("x") as stream:
        os.chmod(secret, 0o600)
        stream.write(secrets.token_urlsafe(48))
        stream.flush()
        os.fsync(stream.fileno())
    bootstrap = root / ".bootstrap-env"
    attempted = False
    labels = {
        "erp.cloud.connector": connector_id,
        "erp.cloud.dedicated": "true",
        "erp.cloud.install": uuid4().hex,
    }
    try:
        with bootstrap.open("x") as stream:
            os.chmod(bootstrap, 0o600)
            stream.write(
                "OPENLIST_ADMIN_PASSWORD="
                + secret.read_text()
                + "\nUMASK=077\nLOG_ENABLE=false\n"
            )
        user = container_bind_user(data, image["Id"])
        attempted = True
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container,
                "--label",
                "erp.cloud.connector=" + connector_id,
                "--label",
                "erp.cloud.dedicated=true",
                "--label",
                "erp.cloud.install=" + labels["erp.cloud.install"],
                "--user",
                user,
                "--restart",
                "unless-stopped",
                "--log-driver",
                "none",
                "--security-opt",
                "no-new-privileges:true",
                "--cap-drop",
                "ALL",
                "--env-file",
                str(bootstrap),
                "--mount",
                "type=bind,src=" + str(data) + ",dst=/opt/openlist/data",
                "--publish",
                "127.0.0.1:5244:5244",
                image["Id"],
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        verify_admin_credential(DockerRuntime().call, container, secret.read_text())
        result = install(
            app_dir,
            mode="managed",
            project=project,
            connector_id=connector_id,
            admin_secret_file=secret,
        )
        registered = load_registry(root / "registry.json")
        if registered["image_id"] != image["Id"]:
            raise RecoveryError("connector_image_changed_during_create")
        return result
    except BaseException:
        if attempted and remove_owned_container(
            DockerRuntime().call, container, labels, image["Id"], data
        ):
            (root / "registry.json").unlink(missing_ok=True)
        # Retain only this newly-created private directory for operator diagnosis;
        # never remove an unknown/external directory or container.
        raise
    finally:
        bootstrap.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("pending", "managed", "create"), default="pending"
    )
    parser.add_argument("--project", default="esports-erp-staging")
    parser.add_argument("--connector-id")
    parser.add_argument("--admin-secret-file", type=Path)
    args = parser.parse_args()
    try:
        with host_locks(args.app_dir):
            print(json.dumps(install(**vars(args))))
    except (RecoveryError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise SystemExit(
            "connector_install_failed; existing connector left unchanged"
        ) from None


@contextlib.contextmanager
def host_locks(app_dir):
    with contextlib.ExitStack() as stack:
        for name, mode in (
            (".cloud-backup.lock", fcntl.LOCK_EX),
            (".backup.lock", fcntl.LOCK_EX),
            (".deploy.lock", fcntl.LOCK_SH),
            (".cloud-connector.lock", fcntl.LOCK_EX),
        ):
            stream = stack.enter_context(
                (app_dir.resolve(strict=True) / name).open("a")
            )
            try:
                fcntl.flock(stream, mode | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RecoveryError("connector_or_deployment_busy") from None
        yield


if __name__ == "__main__":
    main()
