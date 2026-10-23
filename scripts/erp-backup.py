#!/usr/bin/env python3
"""Encrypted complete ERP backups and restore drills; host Python standard library only.

Production containers are read only to this program. Restoration always creates
a fresh, portless Docker database, never a production database or volume.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import select
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloud_auth.recovery import (
    DockerRuntime,
    RecoveryError,
    SnapshotBridge,
    cold_snapshot,
    handoff_restored_connector,
    load_registry,
    recover_interrupted_snapshot,
    verify_isolated_connector,
)

ID_PATTERN = re.compile(r"backup-\d{8}T\d{6}Z-[a-f0-9]{8}$")
EXCLUDED = {
    ".git",
    "node_modules",
    ".venv",
    ".venv312",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "runtime",
    "uploads",
    "data",
    "backups",
    "secrets",
    ".superpowers",
    "test-results",
    "playwright-report",
    "coverage",
}
CHUNK = 1024 * 1024
REPORT_STAGE = None
GIB = 1024**3
# Backup filesystem peak after encrypted-output cleanup: during encryption or
# verification, at most three full-size representations coexist. The fixed
# reserve covers source/config/TLS metadata and compression/encryption overhead.
BACKUP_COPY_FACTOR = 3
BACKUP_WORKING_RESERVE = GIB
# A restore may temporarily need the live database footprint plus comparable
# WAL/index-building workspace in Docker's storage, with an independent reserve.
RESTORE_DATABASE_FACTOR = 2
# Loading a saved image archive may retain both imported layers and temporary
# unpacking material in Docker storage. Budget the entire archive, not just the
# API/PostgreSQL images whose absence triggers loading it.
RESTORE_IMAGE_FACTOR = 2
DOCKER_WORKING_RESERVE = GIB
HELPER_ERRORS = {
    "backup_container_check_failed",
    "recovery_payload_invalid",
    "field_key_canary_failed",
    "lookup_key_canary_failed",
    "restore_row_counts_mismatch",
    "restore_alembic_revision_mismatch",
    "restore_schema_columns_mismatch",
    "restore_schema_constraints_mismatch",
    "restore_schema_indexes_mismatch",
    "restore_schema_sequences_mismatch",
    "restore_sequence_evidence_invalid",
    "restore_sequence_state_mismatch",
    "restore_sequence_next_identifier_unsafe",
    "restored_identity_decryption_failed",
    "restored_phone_decryption_failed",
    "restored_email_decryption_failed",
    "restored_mail_username_decryption_failed",
    "restored_mail_password_decryption_failed",
    "restored_mail_sender_decryption_failed",
    "restored_mail_reply_to_decryption_failed",
    "snapshot_controller_closed",
    "restore_cloud_connector_references_mismatch",
    "restored_cloud_candidate_decryption_failed",
}


class BackupError(RuntimeError):
    def __init__(self, code, *, return_code=None):
        super().__init__(code)
        self.return_code = return_code


def stage(name):
    if REPORT_STAGE:
        REPORT_STAGE(name)


def command_error_code(args):
    if args[0] == "openssl":
        return (
            "archive_decryption_failed" if "-d" in args else "archive_encryption_failed"
        )
    if args[0] != "docker":
        return "command_failed"
    if args[1] == "cp":
        return "container_files_copy_failed"
    if args[1:3] == ["image", "save"]:
        return "runtime_image_archive_failed"
    if args[1:3] == ["image", "load"]:
        return "runtime_image_load_failed"
    if args[1:3] == ["image", "inspect"] or args[1] == "inspect":
        return "runtime_inspection_failed"
    if args[1] == "rm":
        return "restore_cleanup_failed"
    if args[1] == "exec" and "pg_dump" in args:
        return "database_dump_failed"
    if args[1] == "exec" and "psql" in args:
        return "database_metadata_failed"
    if args[1] == "run":
        return (
            "restore_database_start_failed"
            if "--detach" in args
            else "restore_verification_failed"
        )
    return "container_read_failed"


def sanitized_helper_error(output):
    if not isinstance(output, bytes):
        return None
    for line in output.decode("utf-8", errors="replace").splitlines():
        if line.startswith("ERP_BACKUP_ERROR:"):
            code = line.removeprefix("ERP_BACKUP_ERROR:")
            if code in HELPER_ERRORS:
                return code
    return None


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def atomic_json(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    try:
        with open(temporary, "x", encoding="utf-8") as stream:
            os.chmod(temporary, mode)
            json.dump(value, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def run(args, *, input=None, stdin=None, stdout=None, timeout=7200, error_code=None):
    try:
        result = subprocess.run(
            args,
            input=input,
            stdin=stdin,
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=timeout,
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        # Subprocess output can contain credentials or sensitive database values.
        code = (
            sanitized_helper_error(exc.stderr) or error_code or command_error_code(args)
        )
        raise BackupError(code, return_code=exc.returncode) from None
    except (subprocess.TimeoutExpired, OSError):
        raise BackupError(error_code or command_error_code(args)) from None


def digest(path, key=None):
    result = (
        hashlib.sha256() if key is None else hmac.new(key, digestmod=hashlib.sha256)
    )
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            result.update(chunk)
    return result.hexdigest()


def key_bytes(path, backup_dir):
    resolved = path.resolve()
    if resolved.is_relative_to(backup_dir.resolve()) or path.is_symlink():
        raise BackupError("encryption_key_location_invalid")
    try:
        details = path.stat()
        if not stat.S_ISREG(details.st_mode) or details.st_mode & 0o077:
            raise BackupError("encryption_key_permissions_invalid")
        value = path.read_bytes().strip()
        if len(value) < 64:
            raise BackupError("encryption_key_invalid")
        return hashlib.sha256(b"erp-backup-authentication-v1\0" + value).digest()
    except OSError:
        raise BackupError("encryption_key_unavailable") from None


def initialize_key(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        raise BackupError("encryption_key_already_exists")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(secrets.token_hex(48) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def active_release(app_dir):
    marker = app_dir / ".active-release"
    release = (
        Path(marker.read_text().strip()) if marker.is_file() else app_dir / "current"
    )
    release = release.resolve(strict=True)
    if (
        not release.is_relative_to(app_dir.resolve())
        or not (release / ".env").is_file()
    ):
        raise BackupError("active_release_invalid")
    return release


@contextlib.contextmanager
def locked(path, *, shared=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(
                stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
            )
        except BlockingIOError:
            raise BackupError("backup_or_deployment_busy") from None
        yield


def safe_extract(archive, destination):
    """No links, special files, duplicate files, or path traversal in recovery archives."""
    with tarfile.open(archive, "r:gz") as source:
        seen = set()
        for member in source:
            relative = Path(member.name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or member.name in seen
                or not (member.isdir() or member.isfile())
            ):
                raise BackupError("archive_members_invalid")
            seen.add(member.name)
            target = destination / relative
            if not target.resolve().is_relative_to(destination.resolve()):
                raise BackupError("archive_members_invalid")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with (
                    source.extractfile(member) as incoming,
                    target.open("xb") as outgoing,
                ):
                    shutil.copyfileobj(incoming, outgoing, CHUNK)
                os.chmod(target, 0o600)


def file_inventory(directory):
    inventory = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise BackupError("backup_special_file_rejected")
        if path.is_file():
            inventory[str(path.relative_to(directory))] = {
                "size": path.stat().st_size,
                "sha256": digest(path),
            }
    return inventory


def media_check(payload, manifest):
    for item in manifest["database"]["media"]:
        for field in ("storage_key", "preview_storage_key"):
            relative = Path(item[field])
            if relative.is_absolute() or ".." in relative.parts:
                raise BackupError("media_reference_invalid")
            path = payload / "evaluation-media" / relative
            if not path.is_file() or path.is_symlink():
                raise BackupError("media_file_missing")
            if field == "storage_key" and (
                path.stat().st_size != item["size_bytes"]
                or digest(path) != item["sha256"]
            ):
                raise BackupError("media_hash_mismatch")


def inspect(container):
    return json.loads(run(["docker", "inspect", container], timeout=30))[0]


def copy_recovery_files(release, payload, project):
    stage("copy_recovery_configuration")
    recovery = payload / "recovery"
    recovery.mkdir(exist_ok=True)
    shutil.copyfile(release / ".env", recovery / ".env")
    shutil.copytree(release / "secrets", recovery / "secrets")
    for path in release.glob("compose*.yaml"):
        shutil.copyfile(path, recovery / path.name)
    if (release / ".release-metadata").is_file():
        shutil.copyfile(release / ".release-metadata", recovery / ".release-metadata")

    # Preserve source needed to rebuild the release; runtime/dependencies stay excluded.
    def source_filter(member):
        parts = Path(member.name).parts
        if any(part in EXCLUDED or part.startswith(".env") for part in parts):
            return None
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            return None
        return member

    with tarfile.open(recovery / "source.tar.gz", "w:gz", dereference=False) as archive:
        for child in sorted(release.iterdir()):
            if child.name not in EXCLUDED and not child.name.startswith(".env"):
                archive.add(child, arcname=child.name, filter=source_filter)
    run(
        [
            "docker",
            "cp",
            f"{project}-api-1:/run/secrets",
            str(recovery / "mounted-api-secrets"),
        ],
        error_code="mounted_api_secrets_copy_failed",
    )
    run(
        [
            "docker",
            "cp",
            f"{project}-db-1:/run/secrets",
            str(recovery / "mounted-db-secrets"),
        ],
        error_code="mounted_database_secrets_copy_failed",
    )
    run(
        [
            "docker",
            "cp",
            f"{project}-caddy-1:/etc/caddy/certs",
            str(recovery / "tls-certs"),
        ],
        error_code="tls_certificate_copy_failed",
    )


def capture_connector_snapshot(args, payload, export_database):
    registry = args.app_dir / "cloud-connector/registry.json"
    if not registry.exists() and not registry.is_symlink():
        metadata = export_database()
        credentials = metadata.pop("cloud_connector_credentials", [])
        if credentials or any(
            ref["state"] in ("configured", "verified", "active")
            for ref in metadata.get("cloud_connector_references", [])
        ):
            raise BackupError("connector_registry_missing")
        return None, metadata
    if args.key_file.resolve().is_relative_to(registry.parent.resolve()):
        raise BackupError("backup_key_location_invalid")
    (payload / "recovery").mkdir(exist_ok=True, mode=0o700)
    runtime, bridge = DockerRuntime(), SnapshotBridge(args.project)
    try:
        recover_interrupted_snapshot(registry, runtime, bridge)
        return cold_snapshot(
            registry,
            payload / "recovery/cloud-connector",
            runtime,
            bridge,
            export_database,
        )
    except (RecoveryError, OSError, ValueError, KeyError):
        raise BackupError("connector_snapshot_failed") from None


def read_snapshot_metadata(stream, *, timeout):
    deadline = time.monotonic() + timeout
    raw = bytearray()
    while b"\n" not in raw:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
            raise BackupError("snapshot_timeout")
        chunk = os.read(stream.fileno(), 65536)
        if not chunk or len(raw) + len(chunk) > 64 * 1024 * 1024:
            raise BackupError("snapshot_invalid")
        raw.extend(chunk)
    metadata = json.loads(raw.split(b"\n", 1)[0])
    if not re.fullmatch(r"[A-Fa-f0-9-]+", metadata["snapshot"]):
        raise BackupError("snapshot_invalid")
    return metadata


def snapshot_payload(args, release, payload):
    stage("capture_database_snapshot")
    api = inspect(args.project + "-api-1")
    db = inspect(args.project + "-db-1")
    runtime_images = {"api": api["Image"], "db": db["Image"]}
    for service in ("scheduler", "web", "proxy", "caddy"):
        runtime_images[service] = inspect(args.project + "-" + service + "-1")["Image"]
    helper = Path(__file__).with_name("erp_backup_container.py").read_text()
    exporter = None
    registry = args.app_dir / "cloud-connector/registry.json"

    def export_database():
        nonlocal exporter
        exporter = subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                args.project + "-api-1",
                "python",
                "-c",
                helper,
                "snapshot",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        # Managed connectors have a bounded 30-second total pause, not 10 minutes.
        timeout = 12 if registry.exists() else 600
        return read_snapshot_metadata(exporter.stdout, timeout=timeout)

    try:
        connector_manifest, metadata = capture_connector_snapshot(
            args, payload, export_database
        )
        if connector_manifest is not None:
            runtime_images["cloud_connector"] = connector_manifest["image_id"]
            atomic_json(
                payload / "recovery/cloud-connector/manifest.json", connector_manifest
            )
        with (payload / "database.dump").open("wb") as dump:
            run(
                [
                    "docker",
                    "exec",
                    args.project + "-db-1",
                    "pg_dump",
                    "-U",
                    "erp",
                    "-d",
                    "erp",
                    "--format=custom",
                    "--no-owner",
                    "--snapshot=" + metadata["snapshot"],
                ],
                stdout=dump,
            )
        exporter.stdin.write(b"release\n")
        exporter.stdin.flush()
        exporter.stdin.close()
        if exporter.wait(timeout=30) != 0:
            raise BackupError("snapshot_failed")
    except (ValueError, KeyError, BrokenPipeError, subprocess.TimeoutExpired):
        raise BackupError("snapshot_failed") from None
    finally:
        if exporter is not None and exporter.poll() is None:
            exporter.kill()
            exporter.wait(timeout=30)
        if exporter is not None and exporter.stdin and not exporter.stdin.closed:
            exporter.stdin.close()
        if exporter is not None:
            exporter.stdout.close()
    copy_recovery_files(release, payload, args.project)
    stage("save_runtime_images")
    with (payload / "recovery/images.tar").open("wb") as image_archive:
        run(
            ["docker", "image", "save", *sorted(set(runtime_images.values()))],
            stdout=image_archive,
        )
    runtime_config = metadata.pop("runtime_config")
    atomic_json(payload / "recovery/runtime-config.json", runtime_config)
    for name, setting in (
        ("evaluation-media", "evaluation_media_root"),
        ("evaluation-quarantine", "evaluation_media_quarantine_root"),
    ):
        stage("copy_" + name.replace("-", "_"))
        path = runtime_config[setting]
        if not path.startswith("/") or ":" in path:
            raise BackupError("media_root_invalid")
        destination = payload / name
        destination.mkdir()
        run(["docker", "cp", args.project + "-api-1:" + path + "/.", str(destination)])
    return {
        "format_version": 1,
        "created_at": now(),
        "release": release.name,
        "database": metadata,
        "api_image": api["Image"],
        "postgres_image": db["Image"],
        "postgres_image_tag": db["Config"]["Image"],
        "postgres_major": int(
            run(
                [
                    "docker",
                    "exec",
                    args.project + "-db-1",
                    "psql",
                    "-U",
                    "erp",
                    "-d",
                    "erp",
                    "-Atc",
                    "SHOW server_version_num",
                ]
            ).strip()
        )
        // 10000,
        "runtime_images": runtime_images,
        **(
            {"cloud_connector": connector_manifest}
            if connector_manifest is not None
            else {}
        ),
        "database_snapshot_consistent": True,
        "media_consistency": "snapshot references verified; quarantine captured after database dump",
    }


def parse_sequence_state(dumped):
    """Read pg_restore's selected SEQUENCE SET SQL; never execute dump SQL here."""
    pattern = re.compile(
        r"^SELECT pg_catalog\.setval\('((?:[^']|'')*)', (-?[0-9]+), (true|false)\);$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(dumped))
    remainder = re.sub(r"^--[^\n]*", "", pattern.sub("", dumped), flags=re.MULTILINE)
    if re.search(r"\bsetval\s*\(", remainder):
        raise BackupError("restore_sequence_evidence_invalid")
    settings = re.findall(
        r"^SET standard_conforming_strings = (on|off);$", remainder, re.MULTILINE
    )
    if len(set(settings)) > 1:
        raise BackupError("restore_sequence_evidence_invalid")
    evidence = []
    for match in matches:
        name = match[1].replace("''", "'")
        if settings and settings[0] == "off":
            # pg_dump doubles every literal backslash in nonstandard strings.
            # Decode those pairs, not arbitrary Python/SQL escape sequences;
            # unmatched escapes are outside pg_dump's emitted literal contract.
            if "\\" in name.replace("\\\\", ""):
                raise BackupError("restore_sequence_evidence_invalid")
            name = name.replace("\\\\", "\\")
        evidence.append([name, int(match[2]), match[3] == "true"])
    if len({row[0] for row in evidence}) != len(evidence):
        raise BackupError("restore_sequence_evidence_invalid")
    return evidence


def dump_sequence_state(payload, container):
    """Extract immutable dump evidence using only the owned disposable container.

    Sequence positions are NOT MVCC: a source sample before/after pg_dump is not
    an equality oracle. Selecting archive TOC entries avoids reading table data
    into memory and works for existing v1 archives without new manifest fields.
    """
    with (payload / "database.dump").open("rb") as dump:
        toc = run(
            ["docker", "exec", "-i", container, "pg_restore", "--list"], stdin=dump
        )
    entries = re.findall(rb"^(\d+); \d+ \d+ SEQUENCE SET public ", toc, re.MULTILINE)
    if not entries:
        return []
    with tempfile.TemporaryDirectory(
        prefix=".sequence-evidence-", dir=payload.parent
    ) as work:
        selection = Path(work) / "sequences.list"
        selection.write_bytes(b"".join(entry + b";\n" for entry in entries))
        run(["docker", "cp", str(selection), container + ":/tmp/erp-sequences.list"])
        with (payload / "database.dump").open("rb") as dump:
            dumped = run(
                [
                    "docker",
                    "exec",
                    "-i",
                    container,
                    "pg_restore",
                    "--use-list=/tmp/erp-sequences.list",
                    "--file=-",
                    "--no-owner",
                    "--no-privileges",
                ],
                stdin=dump,
            )
    try:
        evidence = parse_sequence_state(dumped.decode("utf-8"))
    except UnicodeDecodeError:
        raise BackupError("restore_sequence_evidence_invalid") from None
    if len(evidence) != len(entries):
        raise BackupError("restore_sequence_evidence_invalid")
    return evidence


def verify_payload(payload, manifest):
    stage("verify_recovery_files")
    expected = manifest["files"]
    actual = file_inventory(payload)
    actual.pop("manifest.json", None)
    if actual != expected:
        raise BackupError("recovery_file_checksum_mismatch")
    connector_tree = payload / "recovery/cloud-connector"
    if "cloud_connector" in manifest:
        try:
            if (
                json.loads((connector_tree / "manifest.json").read_text())
                != manifest["cloud_connector"]
            ):
                raise RecoveryError("connector_manifest_mismatch")
            verify_isolated_connector(connector_tree, manifest["cloud_connector"])
        except (RecoveryError, OSError, ValueError, KeyError):
            raise BackupError("connector_restore_failed") from None
    elif connector_tree.exists():
        raise BackupError("connector_manifest_missing")
    for required in (
        "database.dump",
        "recovery/runtime-config.json",
        "recovery/.env",
        "recovery/source.tar.gz",
        "recovery/images.tar",
        "recovery/secrets/field_encryption_master_key",
    ):
        if required not in actual:
            raise BackupError("recovery_file_missing")
    media_check(payload, manifest)
    config = json.loads((payload / "recovery/runtime-config.json").read_text())
    # Compare the exact mounted secrets to the settings used at snapshot time.
    for name in (
        "field_encryption_master_key",
        "field_lookup_key",
        "session_token_pepper",
        "registration_verification_pepper",
        "registration_session_token_pepper",
        "evaluation_tool_service_token",
        "evaluation_tool_web_service_token",
        "evaluation_tool_worker_service_token",
    ):
        secret_file = payload / "recovery/mounted-api-secrets" / name
        if (
            not secret_file.is_file()
            or secret_file.read_text().rstrip("\r\n") != config[name]
        ):
            raise BackupError("recovery_secret_mismatch")
    helper = Path(__file__).with_name("erp_backup_container.py").read_text()
    owned_name = "erp-backup-verify-" + secrets.token_hex(8)
    ownership = secrets.token_hex(16)
    owned = False
    try:
        stage("start_isolated_database")
        # --network none + no published ports. Postgres gets a disposable anonymous
        # volume from its image, removed with this container; no production mounts.
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                owned_name,
                "--network",
                "none",
                "--label",
                "erp.backup.verify=" + ownership,
                "--memory",
                "2g",
                "--cpus",
                "1",
                "--security-opt",
                "no-new-privileges:true",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e",
                "POSTGRES_USER=erp_verify",
                "-e",
                "POSTGRES_DB=erp_verify",
                manifest["postgres_image"],
            ],
            timeout=120,
        )
        owned = True
        for _ in range(60):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    owned_name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "erp_verify",
                    "-d",
                    "erp_verify",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise BackupError("restore_database_unavailable")
        with (payload / "database.dump").open("rb") as dump:
            stage("restore_isolated_database")
            try:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        owned_name,
                        "pg_restore",
                        "-U",
                        "erp_verify",
                        "-d",
                        "erp_verify",
                        "--no-owner",
                        "--no-privileges",
                        "--exit-on-error",
                    ],
                    stdin=dump,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=7200,
                )
            except subprocess.CalledProcessError as exc:
                raise BackupError(
                    "restore_database_failed", return_code=exc.returncode
                ) from None
            except (subprocess.SubprocessError, OSError):
                raise BackupError("restore_database_failed") from None
        stage("verify_restored_database")
        sequence_state = dump_sequence_state(payload, owned_name)
        verification = json.loads(
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--interactive",
                    "--network",
                    "container:" + owned_name,
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=64m",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--entrypoint",
                    "python",
                    manifest["api_image"],
                    "-c",
                    helper,
                    "verify",
                ],
                input=json.dumps(
                    {
                        "manifest": manifest["database"],
                        "runtime_config": config,
                        "sequence_state": sequence_state,
                    }
                ).encode(),
            )
        )
        return {
            "encrypted": True,
            "verified": True,
            "media_files": sum(name.startswith("evaluation-media/") for name in actual),
            **verification,
        }
    finally:
        if owned:
            details = inspect(owned_name)
            if (
                details["Config"].get("Labels", {}).get("erp.backup.verify")
                != ownership
            ):
                raise BackupError("restore_cleanup_ownership_mismatch")
            run(["docker", "rm", "--force", "--volumes", owned_name], timeout=60)


def crypt(source, destination, key_file, *, decrypt=False):
    command = [
        "openssl",
        "enc",
        "-aes-256-cbc",
        "-pbkdf2",
        "-iter",
        "200000",
        "-md",
        "sha256",
        "-pass",
        "file:" + str(key_file),
        "-in",
        str(source),
        "-out",
        str(destination),
    ]
    if decrypt:
        command.append("-d")
    run(command)


def validate_encrypted(directory, authentication_key):
    meta = json.loads((directory / "complete.json").read_text())
    archive = directory / "archive.tar.gz.enc"
    signed = dict(meta)
    signature = signed.pop("metadata_hmac_sha256", "")
    expected_signature = hmac.new(
        authentication_key,
        json.dumps(signed, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    if (
        meta.get("format_version") != 1
        or not ID_PATTERN.fullmatch(meta["backup_id"])
        or (
            ID_PATTERN.fullmatch(directory.name) and directory.name != meta["backup_id"]
        )
        or not hmac.compare_digest(signature, expected_signature)
        or digest(archive) != meta["sha256"]
        or not hmac.compare_digest(
            digest(archive, authentication_key), meta["hmac_sha256"]
        )
    ):
        raise BackupError("archive_authentication_failed")
    return meta


def seal_metadata(meta, authentication_key):
    meta.pop("metadata_hmac_sha256", None)
    meta["metadata_hmac_sha256"] = hmac.new(
        authentication_key,
        json.dumps(meta, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    return meta


def verify_archive(directory, workspace, args, authentication_key):
    stage("authenticate_encrypted_archive")
    meta = validate_encrypted(directory, authentication_key)
    decrypted = workspace / "restore.tar.gz"
    crypt(directory / "archive.tar.gz.enc", decrypted, args.key_file, decrypt=True)
    payload = workspace / "restore"
    payload.mkdir()
    stage("unpack_recovery_archive")
    safe_extract(decrypted, payload)
    manifest = json.loads((payload / "manifest.json").read_text())
    if manifest["backup_id"] != meta["backup_id"]:
        raise BackupError("archive_identity_mismatch")
    # A fresh recovery host can load the exact backed-up runtime without a
    # registry, package index, or an old image still existing on the failed NAS.
    try:
        run(
            [
                "docker",
                "image",
                "inspect",
                manifest["api_image"],
                manifest["postgres_image"],
                *(
                    [manifest["cloud_connector"]["image_id"]]
                    if "cloud_connector" in manifest
                    else []
                ),
            ],
            timeout=30,
        )
    except BackupError:
        run(
            ["docker", "image", "load", "--input", str(payload / "recovery/images.tar")]
        )
    if getattr(args, "api_image", None):
        manifest["api_image"] = args.api_image
    if getattr(args, "postgres_image", None):
        version = run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "postgres",
                args.postgres_image,
                "--version",
            ],
            timeout=60,
        ).decode()
        match = re.search(r"PostgreSQL\) (\d+)", version)
        if not match or int(match.group(1)) != manifest["postgres_major"]:
            raise BackupError("postgres_major_version_mismatch")
        manifest["postgres_image"] = args.postgres_image
    return verify_payload(payload, manifest)


def extract_recovery(args, *, quiet=False):
    """Explicit disaster recovery export to a new directory, never an in-place restore."""
    target = args.recovery_dir
    if target is None or target.exists() or target.is_symlink():
        raise BackupError("recovery_target_must_be_new_directory")
    if target.resolve().is_relative_to(args.backup_dir.resolve()):
        raise BackupError("recovery_target_must_be_outside_backups")
    key = key_bytes(args.key_file, args.backup_dir)
    source = selected_backup(args.backup_dir, getattr(args, "backup_id", None))
    meta = validate_encrypted(source, key)
    target.mkdir(mode=0o700)
    try:
        with tempfile.TemporaryDirectory(prefix=".extract-", dir=target) as work:
            plain = Path(work) / "archive.tar.gz"
            crypt(source / "archive.tar.gz.enc", plain, args.key_file, decrypt=True)
            safe_extract(plain, target)
        manifest = json.loads((target / "manifest.json").read_text())
        inventory = file_inventory(target)
        inventory.pop("manifest.json", None)
        if manifest["backup_id"] != meta["backup_id"] or inventory != manifest["files"]:
            raise BackupError("recovery_file_checksum_mismatch")
        media_check(target, manifest)
    except Exception:
        shutil.rmtree(target)
        raise
    if not quiet:
        print(
            "Recovery files exported to a new private directory; no live database was changed."
        )


def recover_connector(args, *, runtime=None, bridge=None):
    """Explicit operator handoff from authenticated original backup to restored ERP."""
    app = args.app_dir.resolve(strict=True)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", args.project):
        raise BackupError("connector_project_invalid")
    with (
        locked(app / ".cloud-backup.lock"),
        locked(app / ".backup.lock"),
        locked(app / ".deploy.lock", shared=True),
        locked(app / ".cloud-connector.lock"),
        tempfile.TemporaryDirectory(prefix=".connector-handoff-", dir=app) as work,
    ):
        extraction = argparse.Namespace(**vars(args))
        extraction.recovery_dir = Path(work) / "authenticated"
        extract_recovery(extraction, quiet=True)
        manifest = json.loads((extraction.recovery_dir / "manifest.json").read_text())
        if "cloud_connector" not in manifest:
            raise BackupError("connector_recovery_payload_missing")
        try:
            handoff_restored_connector(
                app / "cloud-connector/registry.json",
                extraction.recovery_dir / "recovery/cloud-connector",
                manifest["cloud_connector"],
                runtime or DockerRuntime(),
                bridge or SnapshotBridge(args.project),
            )
        except (RecoveryError, OSError, ValueError, KeyError, TypeError):
            raise BackupError("connector_handoff_failed") from None
    print(
        "Restored connector identity verified and exact snapshot fence released; no provider was enabled."
    )


def retained_backups(directories):
    """Union of latest 14 days, 8 ISO weeks, 6 months; unknown files never deleted."""
    keep = set()
    buckets = ({}, {}, {})
    for directory in sorted(directories, reverse=True):
        value = dt.datetime.strptime(directory.name[7:23], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=dt.timezone.utc
        )
        keys = (
            value.strftime("%Y-%m-%d"),
            value.strftime("%G-%V"),
            value.strftime("%Y-%m"),
        )
        for index, key in enumerate(keys):
            buckets[index].setdefault(key, directory)
    for bucket, limit in zip(buckets, (14, 8, 6)):
        keep.update(list(bucket.values())[:limit])
    keep.update(sorted(directories, reverse=True)[:2])
    return keep


def completed_backups(directory):
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and ID_PATTERN.fullmatch(path.name)
        and (path / "complete.json").is_file()
    )


def selected_backup(directory, requested=None):
    choices = completed_backups(directory)
    if requested is not None:
        if not ID_PATTERN.fullmatch(requested) or directory / requested not in choices:
            raise BackupError("requested_backup_unavailable")
        return directory / requested
    if not choices:
        raise BackupError("no_complete_backup")
    return choices[-1]


def apply_retention(backup_dir, authentication_key):
    directories = completed_backups(backup_dir)
    valid = []
    for directory in directories:
        # Corrupt/unrecognized snapshots cannot occupy retention slots and evict
        # the last older, restorable snapshot. Keep them untouched for diagnosis.
        try:
            validate_encrypted(directory, authentication_key)
        except (BackupError, ValueError, OSError, KeyError):
            continue
        valid.append(directory)
    keep = retained_backups(valid)
    for directory in valid:
        if directory not in keep:
            shutil.rmtree(directory)


def secondary_copy(source, destination, key, primary_root):
    if not destination.is_dir() or destination.resolve().is_relative_to(
        primary_root.resolve()
    ):
        raise BackupError("secondary_destination_unavailable")
    # Refuse a missing mount silently falling back to the primary filesystem.
    if destination.stat().st_dev == primary_root.stat().st_dev:
        raise BackupError("secondary_destination_not_independent")
    target = destination / source.name
    if target.exists():
        validate_encrypted(target, key)
        return
    temporary = destination / (".partial-" + source.name)
    if temporary.exists():
        raise BackupError("secondary_copy_busy")
    try:
        shutil.copytree(source, temporary)
        validate_encrypted(temporary, key)
        os.rename(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    apply_retention(destination, key)


def filesystem_device(path):
    return Path(path).stat().st_dev


def docker_storage_root():
    value = os.fsdecode(
        run(["docker", "info", "--format", "{{.DockerRootDir}}"])
    ).strip()
    root = Path(value)
    if not root.is_absolute() or not root.is_dir():
        raise BackupError("insufficient_disk_space")
    return root


def require_filesystem_space(backup_dir, backup_required, docker_required):
    docker_root = docker_storage_root()
    if filesystem_device(backup_dir) == filesystem_device(docker_root):
        if shutil.disk_usage(backup_dir).free < backup_required + docker_required:
            raise BackupError("insufficient_disk_space")
        return
    if (
        shutil.disk_usage(backup_dir).free < backup_required
        or shutil.disk_usage(docker_root).free < docker_required
    ):
        raise BackupError("insufficient_disk_space")


def require_peak_space(backup_dir, backup_bytes, database_bytes):
    """Reserve simultaneous backup and disposable-restore peaks per filesystem."""
    require_filesystem_space(
        backup_dir,
        BACKUP_COPY_FACTOR * backup_bytes + BACKUP_WORKING_RESERVE,
        RESTORE_DATABASE_FACTOR * database_bytes + DOCKER_WORKING_RESERVE,
    )


def require_verification_space(
    backup_dir, restore_bytes, database_bytes, image_bytes=None
):
    """Reserve decrypt/extract, image import/staging and disposable database."""
    if image_bytes is None:
        # Legacy signed metadata lacks an image estimate. The full restore
        # payload bounds the saved image archive without trusting local images.
        image_bytes = restore_bytes
    require_filesystem_space(
        backup_dir,
        2 * restore_bytes + BACKUP_WORKING_RESERVE,
        RESTORE_DATABASE_FACTOR * database_bytes
        + RESTORE_IMAGE_FACTOR * image_bytes
        + DOCKER_WORKING_RESERVE,
    )


def disk_preflight(args):
    size = int(
        run(
            [
                "docker",
                "exec",
                args.project + "-db-1",
                "psql",
                "-U",
                "erp",
                "-d",
                "erp",
                "-Atc",
                "SELECT pg_database_size(current_database())",
            ]
        ).strip()
    )
    media = int(
        run(
            [
                "docker",
                "exec",
                args.project + "-api-1",
                "python",
                "-c",
                (
                    "from app.core.config import get_settings; from pathlib import Path; "
                    "s=get_settings(); print(sum(p.stat().st_size "
                    "for d in (s.evaluation_media_root,s.evaluation_media_quarantine_root) "
                    "for p in Path(d).rglob('*') if p.is_file()))"
                ),
            ]
        ).strip()
    )
    image_ids = {
        inspect(args.project + "-" + service + "-1")["Image"]
        for service in ("api", "db", "scheduler", "web", "proxy", "caddy")
    }
    registry = args.app_dir / "cloud-connector/registry.json"
    if registry.exists() or registry.is_symlink():
        try:
            connector = load_registry(registry)
            image_ids.add(connector["image_id"])
            media += sum(
                path.stat().st_size
                for path in Path(connector["data_dir"]).rglob("*")
                if path.is_file()
            )
            media += Path(connector["admin_secret_file"]).stat().st_size
        except (RecoveryError, OSError, ValueError, KeyError):
            raise BackupError("connector_preflight_failed") from None
    image_bytes = sum(
        image["Size"]
        for image in json.loads(run(["docker", "image", "inspect", *sorted(image_ids)]))
    )
    require_peak_space(args.backup_dir, size + media + image_bytes, size)
    return size


def perform(args):
    global REPORT_STAGE
    args.app_dir = args.app_dir.resolve()
    args.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.backup_dir, 0o700)
    previous = {}
    try:
        previous = json.loads(args.status_file.read_text())
    except (OSError, ValueError):
        pass
    with (
        locked(args.app_dir / ".cloud-backup.lock"),
        locked(args.app_dir / ".backup.lock"),
        locked(args.app_dir / ".deploy.lock", shared=True),
        locked(args.app_dir / ".cloud-connector.lock"),
    ):
        status = {
            "schema_version": 1,
            "status": "running",
            "started_at": now(),
            "completed_at": previous.get("completed_at"),
            "error_code": None,
            "last_success_at": previous.get("last_success_at"),
            "verification_at": previous.get("verification_at"),
            "last_verified_backup_id": previous.get("last_verified_backup_id"),
            "backup_id": previous.get("backup_id"),
            "size_bytes": previous.get("size_bytes", 0),
            "recovery": previous.get(
                "recovery", {"encrypted": True, "verified": False}
            ),
            "secondary_copy": previous.get(
                "secondary_copy",
                {
                    "configured": bool(args.secondary_dir),
                    "status": "pending" if args.secondary_dir else "not_configured",
                    "last_success_at": None,
                },
            ),
        }
        if not args.secondary_dir:
            status["secondary_copy"] = {
                "configured": False,
                "status": "not_configured",
                "last_success_at": None,
            }
        atomic_json(args.status_file, status, 0o644)

        def report_stage(name):
            status["stage"] = name
            atomic_json(args.status_file, status, 0o644)
            print("ERP backup stage: " + name, flush=True)

        REPORT_STAGE = report_stage
        try:
            stage("preflight")
            key = key_bytes(args.key_file, args.backup_dir)
            with tempfile.TemporaryDirectory(
                prefix=".work-", dir=args.backup_dir
            ) as temporary:
                workspace = Path(temporary)
                if args.command == "backup":
                    release = active_release(args.app_dir)
                    database_size = disk_preflight(args)
                    identity = (
                        "backup-"
                        + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        + "-"
                        + secrets.token_hex(4)
                    )
                    payload = workspace / "payload"
                    payload.mkdir()
                    manifest = snapshot_payload(args, release, payload)
                    manifest["backup_id"] = identity
                    media_check(payload, manifest)
                    manifest["files"] = file_inventory(payload)
                    atomic_json(payload / "manifest.json", manifest)
                    plain = workspace / "archive.tar.gz"
                    stage("compress_backup_archive")
                    with tarfile.open(plain, "w:gz", dereference=True) as archive:
                        for child in sorted(payload.iterdir()):
                            archive.add(child, arcname=child.name)
                    candidate = workspace / identity
                    candidate.mkdir()
                    encrypted = candidate / "archive.tar.gz.enc"
                    stage("encrypt_backup_archive")
                    crypt(plain, encrypted, args.key_file)
                    with encrypted.open("rb") as stream:
                        os.fsync(stream.fileno())
                    meta = {
                        "format_version": 1,
                        "backup_id": identity,
                        "created_at": manifest["created_at"],
                        "sha256": digest(encrypted),
                        "hmac_sha256": digest(encrypted, key),
                        "size_bytes": encrypted.stat().st_size,
                        "alembic_revision": manifest["database"]["alembic_revision"],
                        "release": manifest["release"],
                        "restore_size_bytes": sum(
                            item["size"] for item in manifest["files"].values()
                        ),
                        "database_size_bytes": database_size,
                        "image_size_bytes": manifest["files"]["recovery/images.tar"][
                            "size"
                        ],
                    }
                    atomic_json(candidate / "complete.json", seal_metadata(meta, key))
                    # The ciphertext and authenticated metadata are now durable
                    # inside the protected workspace. Remove both plaintext source
                    # representations before decrypting the archive for verification.
                    shutil.rmtree(payload)
                    plain.unlink()
                    # Verify the encrypted output, not only the unencrypted source.
                    recovery = verify_archive(candidate, workspace, args, key)
                    meta["verified_at"] = now()
                    atomic_json(candidate / "complete.json", seal_metadata(meta, key))
                    final = args.backup_dir / identity
                    os.rename(candidate, final)
                    descriptor = os.open(args.backup_dir, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    status.update(
                        {
                            "backup_id": identity,
                            "last_success_at": now(),
                            "size_bytes": meta["size_bytes"],
                        }
                    )
                else:
                    final = selected_backup(
                        args.backup_dir, getattr(args, "backup_id", None)
                    )
                    meta = validate_encrypted(final, key)
                    restore_size = int(meta["restore_size_bytes"])
                    # Older archives predate the database estimate. Their whole
                    # payload is a deliberately conservative upper bound.
                    database_size = int(meta.get("database_size_bytes") or restore_size)
                    # validate_encrypted authenticated this estimate before use.
                    # Reserve imports even if images happen to exist right now.
                    image_size = int(meta.get("image_size_bytes") or restore_size)
                    require_verification_space(
                        args.backup_dir, restore_size, database_size, image_size
                    )
                    recovery = verify_archive(final, workspace, args, key)
                status.update(
                    {
                        "verification_at": now(),
                        "last_verified_backup_id": final.name,
                        "recovery": recovery,
                    }
                )
                if args.secondary_dir:
                    status["secondary_copy"]["configured"] = True
                    try:
                        secondary_copy(final, args.secondary_dir, key, args.backup_dir)
                    except BackupError:
                        status["secondary_copy"]["status"] = "failed"
                        raise
                    status["secondary_copy"].update(
                        {"status": "ok", "last_success_at": now()}
                    )
                if args.command == "backup":
                    apply_retention(args.backup_dir, key)
            status.update({"status": "ok", "completed_at": now()})
            status["stage"] = "complete"
            atomic_json(args.status_file, status, 0o644)
            print("ERP encrypted backup and isolated restore verification completed.")
        except Exception as exc:  # noqa: BLE001 — sanitize credentials in unexpected failures
            code = (
                str(exc) if isinstance(exc, BackupError) else "backup_unexpected_error"
            )
            status.update(
                {
                    "status": "failed",
                    "completed_at": now(),
                    "error_code": code,
                    "command_return_code": getattr(exc, "return_code", None),
                }
            )
            atomic_json(args.status_file, status, 0o644)
            raise BackupError(code) from None
        finally:
            REPORT_STAGE = None


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("init-key", "backup", "verify", "extract", "recover-connector"),
    )
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--app-dir", type=Path)
    parser.add_argument("--project", default="esports-erp-staging")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--secondary-dir", type=Path)
    parser.add_argument("--recovery-dir", type=Path)
    parser.add_argument("--api-image")
    parser.add_argument("--postgres-image")
    parser.add_argument("--backup-id")
    args = parser.parse_args()
    try:
        if args.command == "init-key":
            initialize_key(args.key_file)
            print("Independent backup encryption key created with private permissions.")
        elif args.command == "extract":
            if not args.backup_dir or not args.recovery_dir:
                parser.error("--backup-dir and --recovery-dir are required")
            extract_recovery(args)
        elif args.command == "recover-connector":
            if not args.backup_dir or not args.app_dir:
                parser.error("--backup-dir and --app-dir are required")
            recover_connector(args)
        else:
            if not all((args.app_dir, args.backup_dir, args.status_file)):
                parser.error("--app-dir, --backup-dir and --status-file are required")
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", args.project):
                parser.error("invalid compose project name")
            perform(args)
    except BackupError as exc:
        print("ERP backup failed: " + str(exc), file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 — never print secret-bearing subprocess/OS exception details
        print("ERP backup failed: backup_preflight_failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
