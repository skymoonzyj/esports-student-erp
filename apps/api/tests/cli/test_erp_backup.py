from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import io
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.security.encryption import EnvelopeCipher

ROOT = Path(__file__).resolve().parents[4]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = load_script("erp-backup.py")
installer = load_script("erp-backup-install.py")
container_helper = load_script("erp_backup_container.py")
LEGACY_CANDIDATE_COLUMNS = (
    "id", "session_id", "provider", "operation_id", "connector_id", "storage_id",
    "webdav_user_id", "mount_path", "account_fingerprint_ciphertext", "username_ciphertext",
    "password_ciphertext", "config_revision", "selected_directory", "probe_operation_id",
    "probe_sha256", "probe_verified_at", "state", "error_code", "created_at", "updated_at",
)
NEW_SOURCE_COLUMNS = ("protocol_version", "mode", "auth_config_id", "auth_config_revision", "app_revision")


@pytest.fixture
def args(tmp_path):
    app = tmp_path / "app"
    release = app / "releases" / "release-a"
    release.mkdir(parents=True)
    (release / ".env").write_text("private-config")
    (app / ".active-release").write_text(str(release))
    key = tmp_path / "keys" / "backup.key"
    backup.initialize_key(key)
    return argparse.Namespace(
        command="backup",
        app_dir=app,
        backup_dir=app / "backups",
        key_file=key,
        status_file=app / "operations/status.json",
        secondary_dir=None,
        project="erp-fixture",
    )


def test_encryption_roundtrip_and_wrong_key_rejected(args, tmp_path):
    plain = tmp_path / "plain"
    plain.write_bytes(b"private database and recovery secrets" * 20)
    encrypted = tmp_path / "encrypted"
    restored = tmp_path / "restored"
    backup.crypt(plain, encrypted, args.key_file)
    assert plain.read_bytes() not in encrypted.read_bytes()
    backup.crypt(encrypted, restored, args.key_file, decrypt=True)
    assert restored.read_bytes() == plain.read_bytes()
    wrong = tmp_path / "wrong.key"
    backup.initialize_key(wrong)
    with pytest.raises(backup.BackupError):
        backup.crypt(encrypted, restored, wrong, decrypt=True)


def test_legacy_backup_without_connector_still_exports_database(args, tmp_path):
    calls = []
    payload = tmp_path / "payload"
    payload.mkdir()
    result = backup.capture_connector_snapshot(
        args, payload, lambda: calls.append("export") or {"snapshot": "abc"}
    )
    assert result == (None, {"snapshot": "abc"})
    assert calls == ["export"]
    assert not (payload / "recovery/cloud-connector").exists()


def test_snapshot_reference_metadata_is_optional_for_legacy_schema():
    class Connection:
        def execute(self, query, *parameters):
            assert "to_regclass" in query
            return SimpleNamespace(fetchone=lambda: (None,))

    assert container_helper.cloud_connector_references(Connection()) == []


def test_snapshot_reference_metadata_contains_ids_not_credentials():
    class Connection:
        def execute(self, query, *parameters):
            if "to_regclass" in query:
                return SimpleNamespace(fetchone=lambda: ("cloud_auth_candidates",))
            if "information_schema.columns" in query:
                return SimpleNamespace(fetchall=lambda: [(key,) for key in LEGACY_CANDIDATE_COLUMNS])
            return SimpleNamespace(
                fetchall=lambda: [
                    ("candidate-1", "connector-1", "operation-1", "7", "8", "/mount", "active")
                ]
            )

    assert container_helper.cloud_connector_references(Connection()) == [
        {
            "candidate_id": "candidate-1",
            "connector_id": "connector-1",
            "operation_id": "operation-1",
            "storage_id": "7",
            "webdav_user_id": "8",
            "mount_path": "/mount",
            "state": "active",
        }
    ]


def test_missing_managed_registry_rejects_active_database_connector_reference(args, tmp_path):
    with pytest.raises(backup.BackupError, match="connector_registry_missing"):
        backup.capture_connector_snapshot(
            args,
            tmp_path,
            lambda: {
                "cloud_connector_references": [{"state": "active"}],
                "cloud_connector_credentials": [{"password": "must-not-enter-manifest"}],
            },
        )


def test_exporter_partial_line_cannot_hold_connector_pause_indefinitely():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b'{"snapshot":')
        with (
            os.fdopen(read_fd, "rb") as stream,
            pytest.raises(backup.BackupError, match="snapshot_timeout"),
        ):
            backup.read_snapshot_metadata(stream, timeout=0.02)
    finally:
        os.close(write_fd)


def test_snapshot_candidate_credential_recovery_keeps_original_field_binding():
    identifier, connector = uuid4(), uuid4()
    cipher = EnvelopeCipher(bytes(range(32)))
    username = cipher.encrypt(
        json.dumps("restore-user"), field_name=f"cloud_auth:baidu:{identifier}:username"
    )
    password = cipher.encrypt(
        json.dumps("isolated-only"), field_name=f"cloud_auth:baidu:{identifier}:password"
    )

    class Connection:
        def execute(self, query):
            if "to_regclass" in query:
                return SimpleNamespace(fetchone=lambda: ("cloud_auth_candidates",))
            if "information_schema.columns" in query:
                return SimpleNamespace(fetchall=lambda: [(key,) for key in LEGACY_CANDIDATE_COLUMNS])
            return SimpleNamespace(
                fetchall=lambda: [(identifier, connector, "baidu", username, password)]
            )

    assert container_helper.cloud_connector_credentials(Connection(), cipher) == [
        {
            "candidate_id": str(identifier),
            "connector_id": str(connector),
            "username": "restore-user",
            "password": "isolated-only",
        }
    ]
    password = cipher.encrypt(json.dumps("isolated-only"), field_name="wrong-record-field")
    with pytest.raises(container_helper.CheckFailure, match="decryption_failed"):
        container_helper.cloud_connector_credentials(Connection(), cipher)


@pytest.mark.parametrize("mode,version", [("own_app", 1), ("own_app", 2), ("openlist_builtin_import", 2)])
def test_current_candidate_export_retains_source_and_decrypts_actual_protocol(mode, version):
    identifier, connector, operation = uuid4(), uuid4(), uuid4()
    cipher = EnvelopeCipher(bytes(range(32)))
    source = {"provider": "baidu", "protocol_version": version, "mode": mode,
              "auth_config_id": ("own-app:" if mode == "own_app" else "openlist-builtin-import-v1:") + "baidu",
              "auth_config_revision": 3 if mode == "own_app" else 1,
              "app_revision": 3 if mode == "own_app" else None}
    aad_prefix = f"cloud_auth:baidu:{identifier}:" if version == 1 else f"cloud_auth:v2:baidu:{mode}:{identifier}:"
    username = cipher.encrypt(json.dumps("restore-user"), field_name=aad_prefix + "username")
    password = cipher.encrypt(json.dumps("isolated-only"), field_name=aad_prefix + "password")

    class Connection:
        def execute(self, query):
            if "to_regclass" in query:
                return SimpleNamespace(fetchone=lambda: ("cloud_auth_candidates",))
            if "information_schema.columns" in query:
                return SimpleNamespace(fetchall=lambda: [(key,) for key in (*LEGACY_CANDIDATE_COLUMNS, *NEW_SOURCE_COLUMNS)])
            if "username_ciphertext" in query:
                row = (identifier, connector, "baidu", username, password,
                       version, mode, source["auth_config_id"], source["auth_config_revision"], source["app_revision"])
            else:
                row = (identifier, connector, operation, "7", "8", "/mount", "active", "baidu",
                       version, mode, source["auth_config_id"], source["auth_config_revision"], source["app_revision"])
            return SimpleNamespace(fetchall=lambda: [row])

    assert container_helper.cloud_connector_references(Connection()) == [{
        "candidate_id": str(identifier), "connector_id": str(connector), "operation_id": str(operation),
        "storage_id": "7", "webdav_user_id": "8", "mount_path": "/mount", "state": "active", **source,
    }]
    assert container_helper.cloud_connector_credentials(Connection(), cipher) == [{
        "candidate_id": str(identifier), "connector_id": str(connector),
        "username": "restore-user", "password": "isolated-only", **source,
    }]
    password = cipher.encrypt(json.dumps("isolated-only"), field_name=f"cloud_auth:baidu:{identifier}:password" if version == 2 else "wrong-record")
    with pytest.raises(container_helper.CheckFailure, match="decryption_failed"):
        container_helper.cloud_connector_credentials(Connection(), cipher)


@pytest.mark.parametrize("columns", [
    (*LEGACY_CANDIDATE_COLUMNS, "mode"),
    (*LEGACY_CANDIDATE_COLUMNS, *NEW_SOURCE_COLUMNS, "future_source"),
    ("id", "provider"),
])
def test_exporter_rejects_partial_unknown_candidate_schema(columns):
    class Connection:
        def execute(self, query):
            if "to_regclass" in query:
                return SimpleNamespace(fetchone=lambda: ("cloud_auth_candidates",))
            assert "information_schema.columns" in query
            return SimpleNamespace(fetchall=lambda: [(key,) for key in columns])

    with pytest.raises(container_helper.CheckFailure, match="decryption_failed"):
        container_helper.cloud_connector_references(Connection())


def test_subprocess_error_reports_only_fixed_stage_and_return_code(monkeypatch):
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(7, command, stderr=b"private DSN and password")

    monkeypatch.setattr(backup.subprocess, "run", fail)
    with pytest.raises(backup.BackupError, match="runtime_image_archive_failed") as caught:
        backup.run(["docker", "image", "save", "private-image-argument"])
    assert caught.value.return_code == 7
    assert "private" not in str(caught.value)


def test_only_allowlisted_helper_errors_are_exposed(monkeypatch):
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            command,
            stderr=b"private-data\nERP_BACKUP_ERROR:restore_schema_constraints_mismatch\n",
        )

    monkeypatch.setattr(backup.subprocess, "run", fail)
    with pytest.raises(backup.BackupError, match="restore_schema_constraints_mismatch"):
        backup.run(["docker", "run", "--entrypoint", "python"])
    assert backup.sanitized_helper_error(b"ERP_BACKUP_ERROR:private-password") is None


def test_key_permissions_location_and_no_overwrite(args):
    assert stat.S_IMODE(args.key_file.stat().st_mode) == 0o600
    value = args.key_file.read_bytes()
    with pytest.raises(backup.BackupError, match="already_exists"):
        backup.initialize_key(args.key_file)
    assert args.key_file.read_bytes() == value
    with pytest.raises(backup.BackupError, match="location"):
        backup.key_bytes(args.key_file, args.key_file.parent)
    args.key_file.chmod(0o644)
    with pytest.raises(backup.BackupError, match="permissions"):
        backup.key_bytes(args.key_file, args.backup_dir)


@pytest.mark.parametrize(
    "name,kind",
    [("../escape", "file"), ("/absolute", "file"), ("link", "symlink"), ("hard", "hardlink")],
)
def test_archive_rejects_traversal_and_links(tmp_path, name, kind):
    source = tmp_path / "bad.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        if kind != "file":
            member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            member.linkname = "../../outside"
            archive.addfile(member)
        else:
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(backup.BackupError, match="members"):
        backup.safe_extract(source, target)
    assert not (tmp_path / "escape").exists()


def test_ciphertext_sha_and_keyed_authentication_both_checked(args):
    directory = args.backup_dir / "backup-20260910T190000Z-1234abcd"
    directory.mkdir(parents=True)
    encrypted = directory / "archive.tar.gz.enc"
    encrypted.write_bytes(b"encrypted fixture")
    key = backup.key_bytes(args.key_file, args.backup_dir)
    meta = {
        "format_version": 1,
        "backup_id": directory.name,
        "sha256": backup.digest(encrypted),
        "hmac_sha256": backup.digest(encrypted, key),
    }
    backup.atomic_json(directory / "complete.json", backup.seal_metadata(meta, key))
    assert backup.validate_encrypted(directory, key)["backup_id"] == directory.name
    encrypted.write_bytes(b"tampered fixture")
    # Replacing the unkeyed checksum alone cannot authenticate a modified archive.
    meta["sha256"] = backup.digest(encrypted)
    backup.atomic_json(directory / "complete.json", meta)
    with pytest.raises(backup.BackupError, match="authentication"):
        backup.validate_encrypted(directory, key)


def test_restored_connector_handoff_authenticates_original_archive_before_runtime(args):
    directory = args.backup_dir / "backup-20260910T190000Z-1234abcd"
    directory.mkdir(parents=True)
    encrypted = directory / "archive.tar.gz.enc"
    encrypted.write_bytes(b"unauthenticated plaintext pretending to be restored manifest")
    backup.atomic_json(
        directory / "complete.json", {"format_version": 1, "backup_id": directory.name}
    )

    class Runtime:
        def running(self, value):
            pytest.fail("unverified archive must not inspect or mutate a runtime")

    class Bridge:
        def end_snapshot(self, value):
            pytest.fail("unverified archive must not release a gate")

    with pytest.raises(backup.BackupError, match="authentication"):
        backup.recover_connector(args, runtime=Runtime(), bridge=Bridge())


def test_retention_keeps_daily_weekly_monthly_and_latest_two(tmp_path):
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    directories = [
        tmp_path
        / ("backup-" + (start + dt.timedelta(days=index)).strftime("%Y%m%dT%H%M%SZ") + "-1234abcd")
        for index in range(240)
    ]
    kept = backup.retained_backups(directories)
    assert set(directories[-14:]).issubset(kept)
    assert directories[-1] in kept
    assert 14 < len(kept) <= 14 + 8 + 6
    assert directories[0] not in kept
    assert len({path.name[7:13] for path in kept}) == 6


def test_corrupt_recent_snapshots_do_not_evict_last_valid_old_backup(tmp_path, monkeypatch):
    old = tmp_path / "backup-20250101T190000Z-1234abcd"
    old.mkdir()
    (old / "complete.json").write_text("valid fixture")
    for month in range(1, 10):
        bad = tmp_path / f"backup-2026{month:02d}01T190000Z-1234abcd"
        bad.mkdir()
        (bad / "complete.json").write_text("corrupt fixture")

    def validate(directory, key):
        if directory != old:
            raise backup.BackupError("archive_authentication_failed")

    monkeypatch.setattr(backup, "validate_encrypted", validate)
    backup.apply_retention(tmp_path, b"fixture")
    assert old.is_dir()
    assert len(list(tmp_path.iterdir())) == 10


def test_historical_backup_selection_rejects_paths_and_symlinks(tmp_path):
    older = tmp_path / "backup-20260909T190000Z-1234abcd"
    newer = tmp_path / "backup-20260910T190000Z-1234abcd"
    for path in (older, newer):
        path.mkdir()
        (path / "complete.json").write_text("fixture")
    assert backup.selected_backup(tmp_path) == newer
    assert backup.selected_backup(tmp_path, older.name) == older
    with pytest.raises(backup.BackupError, match="unavailable"):
        backup.selected_backup(tmp_path, "../" + older.name)
    link = tmp_path / "backup-20260911T190000Z-1234abcd"
    link.symlink_to(older, target_is_directory=True)
    with pytest.raises(backup.BackupError, match="unavailable"):
        backup.selected_backup(tmp_path, link.name)


def test_failure_preserves_good_backup_and_previous_success(args, monkeypatch):
    previous = {
        "status": "ok",
        "last_success_at": "2026-09-09T19:00:00+00:00",
        "verification_at": "2026-09-09T19:01:00+00:00",
        "backup_id": "good",
        "secondary_copy": {
            "configured": True,
            "status": "ok",
            "last_success_at": "2026-09-09T19:00:00+00:00",
        },
    }
    backup.atomic_json(args.status_file, previous)
    good = args.backup_dir / "backup-20260909T190000Z-1234abcd"
    good.mkdir(parents=True)
    (good / "complete.json").write_text("kept")

    def fail(_args):
        raise backup.BackupError("insufficient_disk_space")

    monkeypatch.setattr(backup, "disk_preflight", fail)
    with pytest.raises(backup.BackupError, match="insufficient_disk_space"):
        backup.perform(args)
    result = json.loads(args.status_file.read_text())
    assert result["last_success_at"] == previous["last_success_at"]
    assert result["verification_at"] == previous["verification_at"]
    assert result["status"] == "failed"
    assert result["secondary_copy"] == {
        "configured": False,
        "status": "not_configured",
        "last_success_at": None,
    }
    assert (good / "complete.json").read_text() == "kept"
    assert not list(args.backup_dir.glob(".work-*"))
    assert stat.S_IMODE(args.status_file.stat().st_mode) == 0o644


def test_disk_preflight_accepts_44_gib_after_bounding_backup_copies(args, tmp_path, monkeypatch):
    gib = 1024**3
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    args.backup_dir.mkdir()

    def fake_run(command, **_kwargs):
        if command[1:3] == ["image", "inspect"]:
            return json.dumps([{"Size": gib // 16}]).encode()
        if command[1:3] == ["info", "--format"]:
            return str(docker_root).encode()
        return str(10 * gib if "python" in command else gib // 16).encode()

    monkeypatch.setattr(backup, "run", fake_run)
    monkeypatch.setattr(backup, "inspect", lambda _name: {"Image": "fixture-image"})
    monkeypatch.setattr(
        backup.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=44 * gib),
    )
    backup.disk_preflight(args)


@pytest.mark.parametrize("shortfall", [0, 1])
def test_disk_preflight_exact_boundaries_on_distinct_filesystems(
    args, tmp_path, monkeypatch, shortfall
):
    gib = 1024**3
    database = 64 * 1024**2
    media = 10 * gib
    images = 64 * 1024**2
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    args.backup_dir.mkdir()
    backup_required = 3 * (database + media + images) + gib
    docker_required = 2 * database + gib

    def fake_run(command, **_kwargs):
        if command[1:3] == ["image", "inspect"]:
            return json.dumps([{"Size": images}]).encode()
        if command[1:3] == ["info", "--format"]:
            return str(docker_root).encode()
        return str(media if "python" in command else database).encode()

    monkeypatch.setattr(backup, "run", fake_run)
    monkeypatch.setattr(backup, "inspect", lambda _name: {"Image": "fixture-image"})
    monkeypatch.setattr(
        backup,
        "filesystem_device",
        lambda path: 1 if Path(path) == args.backup_dir else 2,
        raising=False,
    )

    def fake_usage(path):
        free = backup_required if Path(path) == args.backup_dir else docker_required
        return SimpleNamespace(free=free - shortfall)

    monkeypatch.setattr(backup.shutil, "disk_usage", fake_usage)
    if shortfall:
        with pytest.raises(backup.BackupError, match="insufficient_disk_space"):
            backup.disk_preflight(args)
    else:
        backup.disk_preflight(args)


def test_disk_preflight_combines_simultaneous_peaks_on_one_filesystem(args, tmp_path, monkeypatch):
    gib = 1024**3
    database, media, images = 17, 23, 31
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    args.backup_dir.mkdir()
    required = 3 * (database + media + images) + 2 * database + 2 * gib

    def fake_run(command, **_kwargs):
        if command[1:3] == ["image", "inspect"]:
            return json.dumps([{"Size": images}]).encode()
        if command[1:3] == ["info", "--format"]:
            return str(docker_root).encode()
        return str(media if "python" in command else database).encode()

    monkeypatch.setattr(backup, "run", fake_run)
    monkeypatch.setattr(backup, "inspect", lambda _name: {"Image": "fixture-image"})
    monkeypatch.setattr(backup, "filesystem_device", lambda _path: 7, raising=False)
    monkeypatch.setattr(
        backup.shutil, "disk_usage", lambda _path: SimpleNamespace(free=required - 1)
    )
    with pytest.raises(backup.BackupError, match="insufficient_disk_space"):
        backup.disk_preflight(args)
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _path: SimpleNamespace(free=required))
    backup.disk_preflight(args)


def test_archive_verification_checks_disposable_database_filesystem_boundary(
    args, tmp_path, monkeypatch
):
    gib = 1024**3
    restore_size = 10 * gib
    database_size = 64 * 1024**2
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    args.backup_dir.mkdir()
    monkeypatch.setattr(backup, "docker_storage_root", lambda: docker_root)
    monkeypatch.setattr(
        backup,
        "filesystem_device",
        lambda path: 1 if Path(path) == args.backup_dir else 2,
    )
    backup_required = 2 * restore_size + gib
    # Archives without an image estimate reserve the full payload for image
    # import and staging as well as the disposable database.
    docker_required = 2 * database_size + 2 * restore_size + gib

    def fake_usage(path):
        free = backup_required if Path(path) == args.backup_dir else docker_required - 1
        return SimpleNamespace(free=free)

    monkeypatch.setattr(backup.shutil, "disk_usage", fake_usage)
    with pytest.raises(backup.BackupError, match="insufficient_disk_space"):
        backup.require_verification_space(args.backup_dir, restore_size, database_size)

    monkeypatch.setattr(
        backup.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(
            free=backup_required if Path(path) == args.backup_dir else docker_required
        ),
    )
    backup.require_verification_space(args.backup_dir, restore_size, database_size)


@pytest.mark.parametrize("signed_image_size", [False, True])
@pytest.mark.parametrize("shared_filesystem", [False, True])
@pytest.mark.parametrize("capacity", ["database_only", "one_byte_short", "exact"])
def test_archive_only_verify_reserves_image_import_before_restoring(
    args, tmp_path, monkeypatch, signed_image_size, shared_filesystem, capacity
):
    # Regression: D=64 MiB and 4 GiB of saved images were admitted with only
    # 1.125 GiB Docker free, before verify_archive could load all saved images.
    gib = 1024**3
    args.command = "verify"
    directory = args.backup_dir / "backup-20260910T190000Z-1234abcd"
    directory.mkdir(parents=True)
    encrypted = directory / "archive.tar.gz.enc"
    encrypted.write_bytes(b"bounded authenticated archive fixture")
    key = backup.key_bytes(args.key_file, args.backup_dir)
    meta = {
        "format_version": 1,
        "backup_id": directory.name,
        "sha256": backup.digest(encrypted),
        "hmac_sha256": backup.digest(encrypted, key),
        "restore_size_bytes": 5 * gib,
        "database_size_bytes": 64 * 1024**2,
    }
    if signed_image_size:
        meta["image_size_bytes"] = 4 * gib
    backup.atomic_json(directory / "complete.json", backup.seal_metadata(meta, key))
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    monkeypatch.setattr(backup, "docker_storage_root", lambda: docker_root)
    monkeypatch.setattr(
        backup,
        "filesystem_device",
        lambda path: 1 if shared_filesystem or Path(path) == args.backup_dir else 2,
    )
    # Hand-derived: backup 11 GiB, Docker 9.125 GiB (signed 4 GiB images)
    # or 11.125 GiB (legacy fallback to the entire 5 GiB restore payload).
    docker_required = (9 if signed_image_size else 11) * gib + gib // 8
    docker_free = {
        "database_only": gib + gib // 8,
        "one_byte_short": docker_required - 1,
        "exact": docker_required,
    }[capacity]
    monkeypatch.setattr(
        backup.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(
            free=11 * gib + docker_free
            if shared_filesystem
            else 11 * gib
            if Path(path) == args.backup_dir
            else docker_free
        ),
    )

    def bounded_verify(*_args):
        if capacity != "exact":
            pytest.fail("insufficient image import capacity reached archive restore")
        return {"verified": True}

    monkeypatch.setattr(backup, "verify_archive", bounded_verify)
    if capacity == "exact":
        backup.perform(args)
        assert json.loads(args.status_file.read_text())["status"] == "ok"
    else:
        with pytest.raises(backup.BackupError, match="insufficient_disk_space"):
            backup.perform(args)
        assert json.loads(args.status_file.read_text())["error_code"] == "insufficient_disk_space"
    assert backup.validate_encrypted(directory, key)["backup_id"] == directory.name
    assert not list(args.backup_dir.glob(".work-*"))


def test_backup_removes_plaintext_staging_before_authenticated_restore_verification(
    args, monkeypatch
):
    args.backup_dir.mkdir()
    monkeypatch.setattr(backup, "disk_preflight", lambda _args: 0)
    source_paths = {}

    def snapshot(_args, release, payload):
        source_paths["payload"] = payload
        (payload / "database.dump").write_bytes(b"tiny database dump")
        recovery = payload / "recovery"
        recovery.mkdir()
        (recovery / "images.tar").write_bytes(b"tiny image archive")
        return {
            "format_version": 1,
            "created_at": "2026-09-14T00:00:00+00:00",
            "release": release.name,
            "database": {"alembic_revision": "head", "media": []},
            "api_image": "fixture-api",
            "postgres_image": "postgres:16-alpine",
        }

    monkeypatch.setattr(backup, "snapshot_payload", snapshot)
    original_run = backup.run

    def bounded_run(command, **kwargs):
        if command[:3] == ["docker", "image", "inspect"]:
            return b"[]"
        return original_run(command, **kwargs)

    monkeypatch.setattr(backup, "run", bounded_run)

    def verify_payload(payload, manifest):
        assert not source_paths["payload"].exists()
        assert not source_paths["payload"].parent.joinpath("archive.tar.gz").exists()
        assert (payload / "database.dump").read_bytes() == b"tiny database dump"
        assert manifest["backup_id"].startswith("backup-")
        return {"encrypted": True, "verified": True, "media_files": 0}

    monkeypatch.setattr(backup, "verify_payload", verify_payload)
    backup.perform(args)
    final = backup.selected_backup(args.backup_dir)
    key = backup.key_bytes(args.key_file, args.backup_dir)
    meta = backup.validate_encrypted(final, key)
    assert meta["backup_id"] == final.name
    assert meta["image_size_bytes"] == 18
    assert json.loads(args.status_file.read_text())["recovery"]["web_assets_verification"] == {
        "status": "legacy_unavailable", "current_files": None,
        "retained_files": None, "manifest_sha256": None,
    }
    assert not list(args.backup_dir.glob(".work-*"))


def test_unexpected_failure_is_sanitized(args, monkeypatch, capsys):
    monkeypatch.setattr(backup, "disk_preflight", lambda args: None)

    def fail(*_args):
        raise ValueError("secret-database-password")

    monkeypatch.setattr(backup, "snapshot_payload", fail)
    with pytest.raises(backup.BackupError, match="backup_unexpected_error"):
        backup.perform(args)
    assert "secret-database-password" not in args.status_file.read_text()
    assert "secret-database-password" not in capsys.readouterr().out


def test_lock_overlap_refuses_second_attempt(tmp_path):
    lock = tmp_path / "lock"
    with backup.locked(lock), pytest.raises(backup.BackupError, match="busy"), backup.locked(lock):
        pass


def test_active_release_uses_marker_and_rejects_outside_app(args, tmp_path):
    assert backup.active_release(args.app_dir).name == "release-a"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".env").write_text("secret")
    (args.app_dir / ".active-release").write_text(str(outside))
    with pytest.raises(backup.BackupError, match="active_release_invalid"):
        backup.active_release(args.app_dir)


def test_secondary_copy_requires_existing_independent_device(args, tmp_path):
    args.backup_dir.mkdir()
    destination = tmp_path / "secondary"
    destination.mkdir()
    with pytest.raises(backup.BackupError, match="not_independent"):
        backup.secondary_copy(tmp_path / "source", destination, b"key", args.backup_dir)
    with pytest.raises(backup.BackupError, match="unavailable"):
        backup.secondary_copy(tmp_path / "source", tmp_path / "missing", b"key", args.backup_dir)


def test_cron_update_is_idempotent_and_preserves_other_jobs():
    existing = "MAILTO=\n17 * * * * existing-job\n"
    expected = installer.update_crontab(existing, "0 3 * * * backup")
    assert expected.startswith(existing)
    assert installer.update_crontab(expected, "0 3 * * * backup") == expected
    changed = installer.update_crontab(expected, "0 3 * * * updated")
    assert "0 3 * * * backup" not in changed
    assert changed.count(installer.BEGIN) == 1
    with pytest.raises(ValueError, match="unterminated"):
        installer.update_crontab(installer.BEGIN, "job")


def test_media_restore_detects_missing_and_corrupt_files(tmp_path):
    path = tmp_path / "evaluation-media/objects/aa/video.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"valid")
    preview = tmp_path / "evaluation-media/previews/thumb.jpg"
    preview.parent.mkdir()
    preview.write_bytes(b"preview")
    manifest = {
        "database": {
            "media": [
                {
                    "storage_key": "objects/aa/video.jpg",
                    "preview_storage_key": "previews/thumb.jpg",
                    "sha256": backup.digest(path),
                    "size_bytes": 5,
                }
            ]
        }
    }
    backup.media_check(tmp_path, manifest)
    path.write_bytes(b"wrong")
    with pytest.raises(backup.BackupError, match="hash_mismatch"):
        backup.media_check(tmp_path, manifest)
    path.unlink()
    with pytest.raises(backup.BackupError, match="missing"):
        backup.media_check(tmp_path, manifest)


def test_verification_never_mounts_production_or_exposes_ports(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "database.dump").write_bytes(b"fixture dump")
    recovery = payload / "recovery"
    recovery.mkdir()
    (recovery / ".env").write_text("config")
    (recovery / "source.tar.gz").write_bytes(b"source")
    (recovery / "images.tar").write_bytes(b"images")
    (recovery / "secrets").mkdir()
    (recovery / "secrets/field_encryption_master_key").write_text("fixture")
    mounted = recovery / "mounted-api-secrets"
    mounted.mkdir()
    config = {}
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
        config[name] = "private-" + name
        (mounted / name).write_text(config[name])
    (recovery / "runtime-config.json").write_text(json.dumps(config))
    manifest = {
        "files": backup.file_inventory(payload),
        "database": {"media": []},
        "api_image": "fixture-api",
        "postgres_image": "fixture-postgres",
    }
    commands = []
    label = {}

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["docker", "run", "--detach"]:
            label["erp.backup.verify"] = command[command.index("--label") + 1].split("=", 1)[1]
        if "--entrypoint" in command:
            return b'{"table_count": 2, "encrypted_samples": 1}'
        return b""

    monkeypatch.setattr(backup, "run", fake_run)
    monkeypatch.setattr(backup, "inspect", lambda name: {"Config": {"Labels": label}})
    monkeypatch.setattr(
        backup.subprocess, "run", lambda command, **kwargs: SimpleNamespace(returncode=0)
    )
    result = backup.verify_payload(payload, manifest)
    assert result["verified"]
    postgres_run = commands[0]
    assert postgres_run[postgres_run.index("--network") + 1] == "none"
    assert not {"-p", "--publish", "-v", "--mount", "--volumes-from"}.intersection(postgres_run)
    cleanup = commands[-1]
    assert cleanup[:4] == ["docker", "rm", "--force", "--volumes"]
    assert cleanup[-1].startswith("erp-backup-verify-")
