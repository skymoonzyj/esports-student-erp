"""Cold, portable snapshots of explicitly ERP-owned connectors (stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import TypedDict
from uuid import UUID, uuid4

IMAGE = "openlistteam/openlist:v4.2.6"
ORIGIN = "http://127.0.0.1:5244"
SOURCE_FIELDS = ("provider", "protocol_version", "mode", "auth_config_id",
                 "auth_config_revision", "app_revision")
LEGACY_OBJECT_FIELDS = {"operation_id", "storage_id", "webdav_user_id", "mount_path"}


class RecoveryError(RuntimeError):
    pass


class ContainerAbsent(RecoveryError):
    pass


def validate_recovery_source(value):
    """Recognized legacy records stay legacy; partial/new unknown sources fail closed."""
    present = set(value) & set(SOURCE_FIELDS)
    if not present:
        return False
    if present != set(SOURCE_FIELDS):
        raise RecoveryError("connector_source_invalid")
    provider, version, mode = value["provider"], value["protocol_version"], value["mode"]
    revision, app_revision = value["auth_config_revision"], value["app_revision"]
    valid = provider in ("baidu", "115") and type(version) is int and version in (1, 2)
    if mode == "own_app":
        valid = valid and (type(app_revision) is int and app_revision >= 1
                          and type(revision) is int and revision == app_revision
                          and value["auth_config_id"] == "own-app:" + provider)
    elif mode == "openlist_builtin_import":
        valid = valid and (version == 2 and app_revision is None and type(revision) is int
                          and revision == 1
                          and value["auth_config_id"] == "openlist-builtin-import-v1:" + provider)
    else:
        valid = False
    if not valid:
        raise RecoveryError("connector_source_invalid")
    return True


def validate_snapshot_journal(saved):
    if not isinstance(saved, dict):
        raise RecoveryError("connector_recovery_ownership_mismatch")
    if "version" not in saved:
        # This is the sole identified pre-journal-version shape.
        if set(saved) != {"connector_id", "was_running", "fence"}:
            raise RecoveryError("connector_recovery_ownership_mismatch")
    elif type(saved["version"]) is not int or saved["version"] != 1:
        raise RecoveryError("connector_recovery_ownership_mismatch")
    if "intent" in saved and (
        saved["intent"] != "restore_handoff" or saved.get("phase") == "begin_pending"
    ):
        raise RecoveryError("connector_recovery_ownership_mismatch")


def validate_portable_object(item):
    if not isinstance(item, dict):
        raise RecoveryError("connector_source_invalid")
    sourced = validate_recovery_source(item)
    expected = LEGACY_OBJECT_FIELDS | ({"candidate_id", *SOURCE_FIELDS} if sourced else set())
    if set(item) != expected:
        raise RecoveryError("connector_source_invalid")
    return sourced


def docker_inspect_absent(stderr, target):
    """Only an exact named daemon absence response, never permission/connectivity."""
    return (
        re.fullmatch(
            rb"(?:error response from daemon: |error: )?no such (?:object|container|image):\s*"
            + re.escape(target.encode()),
            stderr.strip(),
            flags=re.IGNORECASE,
        )
        is not None
    )


class ConnectorSnapshotManifest(TypedDict):
    version: int
    image_id: str
    registry: dict
    files: dict


def protected_file(path: Path) -> Path:
    if (
        path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
    ):
        raise RecoveryError("connector_protected_file_invalid")
    return path


def load_registry(path: Path) -> dict:
    if path.parent.is_symlink():
        raise RecoveryError("connector_directory_invalid")
    protected_file(path)
    value = json.loads(path.read_text())
    UUID(value["connector_id"])
    if (
        value.get("version") != 1
        or value.get("managed") is not True
        or value.get("image") != IMAGE
        or value.get("origin") != ORIGIN
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", value.get("image_id", ""))
        or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,80}", value.get("container_name", "")
        )
    ):
        raise RecoveryError("connector_registry_invalid")
    root = path.parent.resolve(strict=True)
    for key, basename in (("data_dir", "data"), ("admin_secret_file", "admin-secret")):
        target = Path(value[key])
        if target.is_symlink() or target.resolve(strict=True) != root / basename:
            raise RecoveryError("connector_path_invalid")
    data = Path(value["data_dir"])
    if not data.is_dir() or stat.S_IMODE(data.stat().st_mode) != 0o700:
        raise RecoveryError("connector_directory_invalid")
    protected_file(Path(value["admin_secret_file"]))
    # Only nonsecret ownership references may appear in registry metadata.
    for item in value.get("objects", []):
        if not isinstance(item, dict) or set(item) - (
            LEGACY_OBJECT_FIELDS | {"candidate_id"} | set(SOURCE_FIELDS)
        ):
            raise RecoveryError("connector_object_registry_invalid")
        validate_portable_object(item)
    return value


def inventory(root: Path, *, deadline=None) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RecoveryError("connector_special_file_rejected")
        if path.is_file() and path != root / "manifest.json":
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    if deadline is not None and time.monotonic() >= deadline:
                        raise RecoveryError("connector_pause_timeout")
                    hasher.update(chunk)
            result[str(path.relative_to(root))] = {
                "size": path.stat().st_size,
                "sha256": hasher.hexdigest(),
            }
    return result


def snapshot_connector(
    registry: Path,
    destination: Path,
    *,
    deadline: float | None = None,
    references=None,
    credentials=None,
) -> ConnectorSnapshotManifest:
    """Caller MUST stop the registered container and hold maintenance/host locks."""
    value = load_registry(registry)
    destination.mkdir(mode=0o700)  # Never merge into an existing recovery directory.
    sources = [(Path(value["admin_secret_file"]), destination / "admin-secret")]
    source_data = Path(value["data_dir"])
    (destination / "data").mkdir(mode=0o700)
    for source in sorted(source_data.rglob("*")):
        if source.is_symlink() or not (source.is_file() or source.is_dir()):
            raise RecoveryError("connector_special_file_rejected")
        target = destination / "data" / source.relative_to(source_data)
        if source.is_dir():
            target.mkdir(mode=0o700)
        else:
            sources.append((source, target))
    for source, target in sources:
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            os.chmod(target, 0o600)
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise RecoveryError("connector_pause_timeout")
                chunk = incoming.read(1024 * 1024)
                if not chunk:
                    break
                outgoing.write(chunk)
    portable = {
        key: value[key]
        for key in (
            "version",
            "managed",
            "connector_id",
            "container_name",
            "image",
            "image_id",
            "origin",
        )
    }
    portable.update(
        data_dir="data",
        admin_secret_file="admin-secret",
        objects=value.get("objects", []),
        state="managed",
        connector_version="4.2.6",
    )
    if references is not None:
        portable["objects"] = [
            {
                key: ref[key]
                for key in (
                    "operation_id",
                    "storage_id",
                    "webdav_user_id",
                    "mount_path",
                )
            }
            | ({key: ref[key] for key in ("candidate_id", *SOURCE_FIELDS)}
               if validate_recovery_source(ref) else {})
            for ref in references
            if ref["connector_id"] == value["connector_id"]
            and ref["state"] in ("configured", "verified", "active")
        ]
    if credentials is not None:
        selected = [
            item
            for item in credentials
            if item["connector_id"] == value["connector_id"]
        ]
        for item in selected:
            validate_recovery_source(item)
        atomic_json(destination / "candidate-credentials.json", selected)
    manifest = ConnectorSnapshotManifest(
        version=2 if any(validate_recovery_source(item) for item in portable["objects"]) else 1,
        image_id=value["image_id"],
        registry=portable,
        files=inventory(destination, deadline=deadline),
    )
    return manifest


def verify_connector_snapshot(
    payload: Path, manifest: ConnectorSnapshotManifest
) -> None:
    if type(manifest.get("version")) is not int or manifest["version"] not in (1, 2) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", manifest.get("image_id", "")
    ):
        raise RecoveryError("connector_manifest_invalid")
    if (
        manifest["registry"].get("data_dir") != "data"
        or manifest["registry"].get("admin_secret_file") != "admin-secret"
    ):
        raise RecoveryError("connector_path_invalid")
    for item in manifest["registry"].get("objects", []):
        if validate_portable_object(item) != (manifest["version"] == 2):
            raise RecoveryError("connector_source_invalid")
    if not (payload / "data").is_dir() or "admin-secret" not in manifest["files"]:
        raise RecoveryError("connector_recovery_file_missing")
    if inventory(payload) != manifest["files"]:
        raise RecoveryError("connector_checksum_mismatch")
    credentials = payload / "candidate-credentials.json"
    if credentials.exists():
        for item in json.loads(protected_file(credentials).read_text()):
            has_source = validate_recovery_source(item)
            if has_source != (manifest["version"] == 2):
                raise RecoveryError("connector_source_invalid")
            if item.get("connector_id") != manifest["registry"]["connector_id"]:
                raise RecoveryError("connector_credential_ownership_invalid")
            if has_source:
                references = [ref for ref in manifest["registry"]["objects"]
                              if ref.get("candidate_id") == item.get("candidate_id")]
                if len(references) != 1 or any(
                    references[0][key] != item[key] for key in SOURCE_FIELDS
                ):
                    raise RecoveryError("connector_source_invalid")


def cold_snapshot(
    registry, destination, runtime, bridge, export_database, *, clock=time.monotonic
):
    """ERP snapshot is exported while stopped; original running state restored."""
    value = load_registry(registry)
    running = runtime.running(value)
    journal = registry.parent / "snapshot-state.json"
    if journal.exists() or journal.is_symlink():
        # Recovery must be performed under the same full host lock hierarchy.
        raise RecoveryError("connector_interrupted_snapshot_pending")
    saved = {
        "version": 1,
        "connector_id": value["connector_id"],
        "operation_id": str(uuid4()),
        "was_running": running,
        "phase": "begin_pending",
        "fence": None,
    }
    # Persist intent before any mutating RPC. A lost ACK keeps the only UUID that
    # can retrieve the same fence; failed fence persistence is also retryable.
    atomic_json(journal, saved)
    fence = bridge.begin_snapshot(value["connector_id"], saved["operation_id"])
    validate_snapshot_fence(fence, value["connector_id"], saved["operation_id"])
    saved.update(phase="fenced", fence=fence)
    atomic_json(journal, saved)
    stopped = False
    started = clock()
    try:
        if running:
            stopped = True  # stop may fail after actually stopping: still restart.
            runtime.stop(value, timeout=10)
        metadata = export_database()
        if isinstance(metadata, dict) and any(
            ref["state"] in ("configured", "verified", "active")
            and ref["connector_id"] != value["connector_id"]
            for ref in metadata.get("cloud_connector_references", [])
        ):
            raise RecoveryError("connector_database_instance_mismatch")
        # Reserve restart plus inspection inside the total 30-second pause.
        manifest = snapshot_connector(
            registry,
            destination,
            deadline=started + 23,
            references=(metadata or {}).get("cloud_connector_references"),
            credentials=(metadata or {}).pop("cloud_connector_credentials", None),
        )
        if isinstance(fence, dict):
            manifest["snapshot_fence"] = fence
            manifest["was_running"] = running
        if clock() - started >= 30:
            raise RecoveryError("connector_pause_timeout")
        return manifest, metadata
    finally:
        if stopped:
            runtime.start(value)
            if not runtime.running(value):
                raise RecoveryError("connector_restart_failed")
        saved["phase"] = "release_pending"
        atomic_json(journal, saved)
        if not bridge.end_snapshot(fence):
            raise RecoveryError("connector_maintenance_release_failed")
        journal.unlink()
        if clock() - started >= 30:
            raise RecoveryError("connector_pause_timeout")


def atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def recover_interrupted_snapshot(registry, runtime, bridge):
    journal = registry.parent / "snapshot-state.json"
    if not journal.exists() and not journal.is_symlink():
        return
    value = load_registry(registry)
    saved = json.loads(protected_file(journal).read_text())
    validate_snapshot_journal(saved)
    if (
        saved.get("connector_id") != value["connector_id"]
        or type(saved.get("was_running")) is not bool
    ):
        raise RecoveryError("connector_recovery_ownership_mismatch")
    fence = saved.get("fence")
    if saved.get("phase") == "begin_pending":
        operation = saved.get("operation_id")
        try:
            UUID(operation)
        except (ValueError, TypeError, AttributeError):
            raise RecoveryError("connector_recovery_ownership_mismatch") from None
        if fence is not None:
            raise RecoveryError("connector_recovery_ownership_mismatch")
        # Begin may or may not have committed. Same-operation retry reconciles
        # either outcome without inventing an operation or clearing another one.
        fence = bridge.begin_snapshot(value["connector_id"], operation)
        validate_snapshot_fence(fence, value["connector_id"], operation)
        saved.update(phase="fenced", fence=fence)
        atomic_json(journal, saved)
    else:
        if saved.get("phase") not in (None, "fenced", "release_pending"):
            raise RecoveryError("connector_recovery_ownership_mismatch")
        validate_snapshot_fence(fence, value["connector_id"], saved.get("operation_id"))
    if saved["was_running"]:
        if not runtime.running(value):
            runtime.start(value)
        if not runtime.running(value):
            raise RecoveryError("connector_restart_failed")
    elif runtime.running(value):
        raise RecoveryError("connector_original_state_changed")
    saved["phase"] = "release_pending"
    atomic_json(journal, saved)
    release = bridge.restore_snapshot if saved.get("intent") == "restore_handoff" else bridge.end_snapshot
    if not release(fence):
        raise RecoveryError("connector_maintenance_release_failed")
    journal.unlink()


def handoff_restored_connector(registry, payload, manifest, runtime, bridge):
    """Caller authenticates archive and holds all host locks; no instance creation."""
    verify_connector_snapshot(payload, manifest)
    value = load_registry(registry)
    portable = manifest["registry"]
    fence = manifest.get("snapshot_fence")
    was_running = manifest.get("was_running")
    if (
        type(was_running) is not bool
        or any(
            portable.get(key) != value[key]
            for key in ("connector_id", "image", "image_id", "origin")
        )
        or manifest["image_id"] != value["image_id"]
        or not hmac.compare_digest(
            Path(value["admin_secret_file"]).read_bytes(),
            (payload / "admin-secret").read_bytes(),
        )
    ):
        raise RecoveryError("connector_recovery_ownership_mismatch")
    validate_snapshot_fence(fence, value["connector_id"])
    saved = {
        "version": 1,
        "intent": "restore_handoff",
        "connector_id": value["connector_id"],
        "operation_id": fence["operation_id"],
        "was_running": was_running,
        "phase": "fenced",
        "fence": fence,
    }
    journal = registry.parent / "snapshot-state.json"
    if journal.exists() or journal.is_symlink():
        existing = json.loads(protected_file(journal).read_text())
        validate_snapshot_journal(existing)
        if any(
            existing.get(key) != saved[key]
            for key in ("connector_id", "operation_id", "was_running", "fence", "intent")
        ) or existing.get("phase") not in ("fenced", "release_pending"):
            raise RecoveryError("connector_recovery_ownership_mismatch")
    # Inspect the actual relocated instance (labels/image/sole bind) before any
    # journal mutation or RPC. Gate expiry alone is never a recovery decision.
    running = runtime.running(value)
    if not was_running and running:
        raise RecoveryError("connector_original_state_changed")
    if not journal.exists():
        atomic_json(journal, saved)
    recover_interrupted_snapshot(registry, runtime, bridge)


def validate_snapshot_fence(fence, connector_id, operation_id=None):
    if (
        not isinstance(fence, dict)
        or set(fence) != {"connector_id", "operation_id", "revision", "expires_at"}
        or fence["connector_id"] != connector_id
        or (operation_id is not None and fence["operation_id"] != operation_id)
        or type(fence["revision"]) is not int
        or fence["revision"] < 1
    ):
        raise RecoveryError("connector_recovery_ownership_mismatch")
    try:
        UUID(fence["operation_id"])
        if datetime.fromisoformat(fence["expires_at"]).tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise RecoveryError("connector_recovery_ownership_mismatch") from None


class DockerRuntime:
    def call(self, args, *, timeout=15, input=None):
        try:
            result = subprocess.run(
                ["docker", *args],
                capture_output=True,
                timeout=timeout,
                check=True,
                input=input,
            )
            return result.stdout
        except subprocess.CalledProcessError as error:
            if (
                args[0] == "inspect"
                and error.returncode == 1
                and docker_inspect_absent(error.stderr or b"", args[1])
            ):
                raise ContainerAbsent("connector_container_absent") from None
            raise RecoveryError("connector_runtime_failed") from None
        except (OSError, subprocess.SubprocessError):
            raise RecoveryError("connector_runtime_failed") from None

    def running(self, value):
        details = json.loads(
            self.call(["inspect", value["container_name"]], timeout=2)
        )[0]
        labels = details["Config"].get("Labels", {})
        if (
            labels.get("erp.cloud.connector") != value["connector_id"]
            or labels.get("erp.cloud.dedicated") != "true"
            or details["Image"] != value["image_id"]
        ):
            raise RecoveryError("connector_ownership_mismatch")
        mounts = details.get("Mounts", [])
        if (
            len(mounts) != 1
            or mounts[0].get("Type") != "bind"
            or Path(mounts[0]["Source"]).resolve() != Path(value["data_dir"]).resolve()
            or mounts[0].get("Destination") != "/opt/openlist/data"
        ):
            raise RecoveryError("connector_mount_invalid")
        return details["State"]["Running"] is True

    def stop(self, value, timeout):
        self.call(
            ["stop", "--time", str(timeout), value["container_name"]],
            timeout=timeout + 2,
        )
        if self.running(value):
            raise RecoveryError("connector_stop_failed")

    def start(self, value):
        self.call(["start", value["container_name"]], timeout=5)


class SnapshotBridge:
    def __init__(self, project):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
            raise RecoveryError("connector_project_invalid")
        self.project = project

    def call(self, action, **payload):
        raw = json.dumps({"action": action, **payload}).encode()
        if len(raw) > 65536:
            raise RecoveryError("connector_snapshot_bridge_failed")
        try:
            with tempfile.TemporaryFile() as output:
                result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        self.project + "-api-1",
                        "python",
                        "-m",
                        "app.operations.cloud_auth_bridge",
                    ],
                    input=raw,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                if result.returncode or output.tell() > 65536:
                    raise ValueError()
                output.seek(0)
                value = json.loads(output.read(65537))
                if value.get("ok") is not True:
                    raise ValueError()
                return value
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            raise RecoveryError("connector_snapshot_bridge_failed") from None

    def begin_snapshot(self, connector_id, operation_id):
        UUID(connector_id)
        UUID(operation_id)
        fence = self.call(
            "begin_snapshot", connector_id=connector_id, operation_id=operation_id
        )["fence"]
        validate_snapshot_fence(fence, connector_id, operation_id)
        return fence

    def end_snapshot(self, fence):
        return self.call("end_snapshot", fence=fence).get("released") is True

    def restore_snapshot(self, fence):
        return self.call("restore_snapshot", fence=fence).get("released") is True


def verify_connector_database(payload, manifest):
    database = payload / "data/data.db"
    if not database.is_file() or database.is_symlink():
        raise RecoveryError("connector_database_missing")
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RecoveryError("connector_database_corrupt")
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {"x_storages", "x_users"}.issubset(tables):
                raise RecoveryError("connector_schema_invalid")
            for ref in manifest["registry"].get("objects", []):
                if not db.execute(
                    "SELECT id FROM x_storages WHERE id=? AND mount_path=?",
                    (ref["storage_id"], ref["mount_path"]),
                ).fetchone():
                    raise RecoveryError("connector_mount_reference_missing")
                if not db.execute(
                    "SELECT id FROM x_users WHERE id=? AND pwd_hash<>''",
                    (ref["webdav_user_id"],),
                ).fetchone():
                    raise RecoveryError("connector_credential_reference_missing")
            if not db.execute(
                "SELECT id FROM x_users WHERE role=2 AND pwd_hash<>''"
            ).fetchone():
                raise RecoveryError("connector_admin_credential_missing")
    except sqlite3.Error:
        raise RecoveryError("connector_database_invalid") from None


def verify_isolated_connector(
    payload, manifest, docker=None, *, verify_database=verify_connector_database
):
    """No live credential use: validate copied DB and boot with network none."""
    verify_connector_snapshot(payload, manifest)
    verify_database(payload, manifest)
    docker = docker or DockerRuntime().call
    name = "erp-connector-verify-" + uuid4().hex
    ownership = uuid4().hex
    attempted = False
    with tempfile.TemporaryDirectory(
        prefix=".connector-verify-", dir=payload.parent
    ) as temporary:
        work = Path(temporary) / "data"
        shutil.copytree(payload / "data", work)
        user = container_bind_user(work, manifest["image_id"], docker)
        try:
            # Logs disabled: the connector's own bootstrap must never log secrets.
            attempted = True  # run can create the container before returning failure.
            docker(
                [
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--log-driver",
                    "none",
                    "--label",
                    "erp.connector.verify=" + ownership,
                    "--user",
                    user,
                    "--security-opt",
                    "no-new-privileges:true",
                    "--cap-drop",
                    "ALL",
                    "--mount",
                    "type=bind,src=" + str(work) + ",dst=/opt/openlist/data",
                    manifest["image_id"],
                ],
                timeout=30,
            )
            details = json.loads(docker(["inspect", name]))[0]
            if (
                details["HostConfig"].get("NetworkMode") != "none"
                or details["HostConfig"].get("PortBindings")
                or not details["State"].get("Running")
            ):
                raise RecoveryError("connector_restore_isolation_failed")
            verify_admin_credential(
                docker, name, (payload / "admin-secret").read_text()
            )
            credentials = payload / "candidate-credentials.json"
            if credentials.exists():
                for credential in json.loads(credentials.read_text()):
                    if (
                        credential["connector_id"]
                        != manifest["registry"]["connector_id"]
                    ):
                        raise RecoveryError("connector_credential_ownership_invalid")
                    verify_admin_credential(
                        docker,
                        name,
                        credential["password"],
                        username=credential["username"],
                    )
        finally:
            if attempted:
                remove_owned_container(
                    docker,
                    name,
                    {"erp.connector.verify": ownership},
                    manifest["image_id"],
                    work,
                )


def remove_owned_container(docker, name, labels, image_id, data):
    """Resolve uncertain run results; never delete on a partial identity match."""
    try:
        details = json.loads(docker(["inspect", name]))[0]
    except ContainerAbsent:
        return False
    actual_labels = details.get("Config", {}).get("Labels") or {}
    mounts = details.get("Mounts", [])
    if (
        details.get("Name") != "/" + name
        or details.get("Image") != image_id
        or any(actual_labels.get(key) != value for key, value in labels.items())
        or len(mounts) != 1
        or mounts[0].get("Type") != "bind"
        or Path(mounts[0].get("Source", "")).resolve() != data.resolve()
        or mounts[0].get("Destination") != "/opt/openlist/data"
    ):
        raise RecoveryError("connector_cleanup_ownership_mismatch")
    docker(["rm", "--force", "--volumes", name], timeout=30)
    return True


def container_bind_user(data, image, docker=None):
    """Docker VM file sharing may map host uid to a different owner; retain 0700."""
    docker = docker or DockerRuntime().call
    value = (
        docker(
            [
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--mount",
                "type=bind,src=" + str(data) + ",dst=/opt/openlist/data,readonly",
                "--entrypoint",
                "/bin/stat",
                image,
                "-c",
                "%u:%g",
                "/opt/openlist/data",
            ],
            timeout=15,
        )
        .decode()
        .strip()
    )
    if not re.fullmatch(r"\d{1,10}:\d{1,10}", value):
        raise RecoveryError("connector_bind_owner_invalid")
    return value


def connector_local_request(docker, name, path, body, *, authorization=""):
    """Send secret-bearing HTTP entirely on stdin inside an isolated namespace."""
    if path not in (
        "/api/auth/login",
        "/api/admin/storage/create",
        "/api/admin/user/create",
    ):
        raise RecoveryError("connector_local_request_invalid")
    encoded = json.dumps(body, separators=(",", ":")).encode()
    headers = (
        "POST "
        + path
        + " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(encoded))
        + "\r\n"
        + ("Authorization: " + authorization + "\r\n" if authorization else "")
        + "\r\n"
    ).encode()
    raw = docker(
        ["exec", "-i", name, "nc", "-w", "5", "127.0.0.1", "5244"],
        input=headers + encoded,
        timeout=6,
    )
    if len(raw) > 65536:
        raise RecoveryError("connector_local_response_invalid")

    class Socket:
        def makefile(self, *args):
            return io.BytesIO(raw)

    try:
        response = http.client.HTTPResponse(Socket())
        response.begin()
        if response.status != 200:
            raise ValueError()
        return json.loads(response.read(65537))
    except (OSError, ValueError, http.client.HTTPException):
        raise RecoveryError("connector_local_response_invalid") from None


def verify_admin_credential(docker, name, password, *, username="admin"):
    deadline = time.monotonic() + 10
    while True:
        try:
            result = connector_local_request(
                docker,
                name,
                "/api/auth/login",
                {"username": username, "password": password},
            )
            if result.get("code") == 200 and isinstance(
                result.get("data", {}).get("token"), str
            ):
                return
            raise RecoveryError("connector_admin_credential_unrecoverable")
        except RecoveryError:
            if time.monotonic() >= deadline:
                raise RecoveryError(
                    "connector_admin_credential_unrecoverable"
                ) from None
            time.sleep(0.1)
