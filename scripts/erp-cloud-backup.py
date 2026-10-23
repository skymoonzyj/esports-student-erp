#!/usr/bin/env python3
"""ERP-owned encrypted backup replication; no provider credentials in argv/logs."""

import argparse
import datetime as dt
import hashlib
import hmac
import importlib.util
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from uuid import UUID

from cloud_auth.recovery import DockerRuntime, RecoveryError, load_registry
from erp_cloud_transport import (
    CloudError,
    Transport,
    safe_path,  # noqa: F401 -- retain legacy runner export
    target_prefix,
)

spec = importlib.util.spec_from_file_location(
    "erp_backup", Path(__file__).with_name("erp-backup.py")
)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def test_connection(transport, remote_path):
    path = target_prefix(remote_path) + "/.erp-probe-" + secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="erp-cloud-probe-") as name:
        root = Path(name)
        source = root / "probe"
        source.write_bytes(secrets.token_bytes(64))
        source.chmod(0o600)
        try:
            transport.put(path, source)
            transport.get(path, root / "read", 64)
            if (root / "read").read_bytes() != source.read_bytes():
                raise CloudError("cloud_remote_corrupt")
        finally:
            transport.delete(path)


def local_pair(directory, key, *, full=True):
    if directory.is_symlink() or not directory.is_dir():
        raise CloudError("cloud_local_invalid")
    for name in ("archive.tar.gz.enc", "complete.json"):
        file = directory / name
        if file.is_symlink() or not file.is_file():
            raise CloudError("cloud_local_invalid")
    if (directory / "complete.json").stat().st_size > 65536:
        raise CloudError("cloud_local_invalid")
    try:
        meta = json.loads((directory / "complete.json").read_text())
        signed = dict(meta)
        signature = signed.pop("metadata_hmac_sha256", "")
        expected = hmac.new(
            key,
            json.dumps(signed, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        verified = dt.datetime.fromisoformat(meta.get("verified_at", ""))
        if (
            not hmac.compare_digest(signature, expected)
            or meta.get("format_version") != 1
            or meta.get("backup_id") != directory.name
            or verified.tzinfo is None
            or type(meta.get("size_bytes")) is not int
            or meta["size_bytes"] <= 0
            or meta["size_bytes"] != (directory / "archive.tar.gz.enc").stat().st_size
        ):
            raise ValueError()
        return backup.validate_encrypted(directory, key) if full else meta
    except (OSError, ValueError, KeyError, TypeError, backup.BackupError):
        raise CloudError("cloud_local_invalid") from None


def latest(root, key):
    for directory in sorted(root.glob("backup-*"), reverse=True):
        if not backup.ID_PATTERN.fullmatch(directory.name):
            continue
        try:
            local_pair(directory, key, full=False)
            return directory.name
        except CloudError:
            continue
    return None


def copy_backup(transport, remote_path, directory, key):
    metadata = local_pair(directory, key)
    size = (directory / "archive.tar.gz.enc").stat().st_size
    if shutil.disk_usage(directory.parent).free < size + 64 * 1024 * 1024:
        raise CloudError("cloud_disk_space")
    remote = target_prefix(remote_path) + "/" + directory.name
    with tempfile.TemporaryDirectory(
        prefix=".cloud-readback-", dir=directory.parent
    ) as name:
        temporary = Path(name)
        marker = temporary / "complete.json"
        exists = transport.get(remote + "/complete.json", marker, 65536, missing=True)
        if exists:
            transport.get(
                remote + "/archive.tar.gz.enc", temporary / "archive.tar.gz.enc", size
            )
            try:
                if json.loads(marker.read_text()) != metadata:
                    raise ValueError()
                verified = backup.validate_encrypted(temporary, key)
                if verified != metadata:
                    raise ValueError()
            except (OSError, ValueError, KeyError, TypeError, backup.BackupError):
                raise CloudError("cloud_remote_corrupt") from None
            return metadata["backup_id"]
        transport.mkdir(remote)
        transport.put(remote + "/archive.tar.gz.enc", directory / "archive.tar.gz.enc")
        transport.get(
            remote + "/archive.tar.gz.enc", temporary / "archive.tar.gz.enc", size
        )
        if (
            backup.digest(temporary / "archive.tar.gz.enc") != metadata["sha256"]
            or backup.digest(temporary / "archive.tar.gz.enc", key)
            != metadata["hmac_sha256"]
        ):
            raise CloudError("cloud_remote_corrupt")
        transport.put(remote + "/complete.json", directory / "complete.json")
        transport.get(remote + "/complete.json", marker, 65536)
        try:
            if json.loads(marker.read_text()) != metadata:
                raise ValueError()
            if backup.validate_encrypted(temporary, key) != metadata:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, backup.BackupError):
            raise CloudError("cloud_remote_corrupt") from None
    return metadata["backup_id"]


def bridge(config, action, **payload):
    project = config["project"]
    if not isinstance(project, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", project
    ):
        raise CloudError("cloud_backup_failed")
    try:
        # A private file prevents unbounded child output accumulation in RAM.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    project + "-api-1",
                    "python",
                    "-m",
                    "app.operations.cloud_bridge",
                ],
                input=json.dumps({"action": action, **payload}).encode(),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            if result.returncode or output.tell() > 65536:
                raise TypeError()
            output.seek(0)
            value = json.loads(output.read(65537))
            if not isinstance(value, dict):
                raise TypeError()
            return value
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise CloudError("cloud_backup_failed") from None


def copy_origin(config, job):
    """Managed credentials belong only to the exact protected host registration.

    Claims contain an identity, never a configurable endpoint. The retained
    legacy connector URL remains solely for manual targets.
    """
    managed = job.get("managed_auth", False)
    if not isinstance(managed, bool):
        raise CloudError("cloud_connector_failed")
    if not managed:
        return config.get("connector_url", "http://127.0.0.1:5244")
    try:
        connector_id = UUID(job["connector_id"])
        registry = load_registry(
            Path(config["app_dir"]) / "cloud-connector/registry.json"
        )
        if UUID(
            registry["connector_id"]
        ) != connector_id or not DockerRuntime().running(registry):
            raise CloudError("cloud_connector_failed")
        return registry["origin"]
    except (RecoveryError, OSError, ValueError, TypeError, KeyError):
        raise CloudError("cloud_connector_failed") from None


def run(config, call=bridge):
    app_dir, root = Path(config["app_dir"]), Path(config["backup_dir"])
    with (
        backup.locked(app_dir / ".cloud-backup.lock"),
        backup.locked(app_dir / ".backup.lock", shared=True),
        backup.locked(app_dir / ".deploy.lock", shared=True),
    ):
        key = backup.key_bytes(Path(config["key_file"]), root)
        current = latest(root, key)
        call(config, "sync", backup_id=current)
        for _ in range(6):
            job = call(config, "claim").get("job")
            if job is None:
                break
            success, code, identity = False, None, None
            try:
                last_heartbeat = time.monotonic()

                def heartbeat(job=job):
                    nonlocal last_heartbeat
                    if time.monotonic() - last_heartbeat >= 60:
                        call(
                            config,
                            "heartbeat",
                            job_id=job["id"],
                            revision=job["revision"],
                        )
                        last_heartbeat = time.monotonic()

                with (
                    backup.locked(app_dir / ".cloud-connector.lock")
                    if job.get("managed_auth") is True
                    else nullcontext()
                ):
                    transport = Transport(
                        copy_origin(config, job),
                        job["username"],
                        job["password"],
                        progress=heartbeat,
                        managed_auth=job.get("managed_auth", False),
                    )
                    if job["kind"] == "test":
                        test_connection(transport, job["remote_path"])
                    elif job["kind"] == "copy":
                        identity = job.get("backup_id") or current
                        if not identity:
                            raise CloudError("cloud_no_local_backup")
                        if not backup.ID_PATTERN.fullmatch(identity):
                            raise CloudError("cloud_local_invalid")
                        identity = copy_backup(
                            transport, job["remote_path"], root / identity, key
                        )
                    else:
                        raise CloudError("cloud_backup_failed")
                success = True
            except CloudError as error:
                code = str(error)
            except (OSError, ValueError, TypeError, KeyError, backup.BackupError):
                code = "cloud_backup_failed"
            call(
                config,
                "finish",
                job_id=job["id"],
                revision=job["revision"],
                success=success,
                error_code=code,
                backup_id=identity if success and job["kind"] == "copy" else None,
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    try:
        run(json.loads(args.config.read_text()))
    except (CloudError, OSError, ValueError, TypeError, KeyError, backup.BackupError):
        print("ERP_CLOUD_ERROR:cloud_backup_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
