"""Connector recovery rejects unsafe input and proves a cold capture ordering."""

import importlib.util
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[4]


def snapshot_fence(connector, operation):
    return {
        "connector_id": connector,
        "operation_id": operation,
        "revision": 1,
        "expires_at": "2099-01-01T00:00:00Z",
    }


def module():
    path = ROOT / "scripts/cloud_auth/recovery.py"
    assert path.is_file(), "connector recovery implementation is missing"
    spec = importlib.util.spec_from_file_location("connector_recovery", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def registry(tmp_path):
    root = tmp_path / "cloud-connector"
    root.mkdir(mode=0o700)
    data = root / "data"
    data.mkdir(mode=0o700)
    (data / "data.db").write_bytes(b"cold sqlite fixture")
    secret = root / "admin-secret"
    secret.write_text("protected-admin-password")
    secret.chmod(0o600)
    path = root / "registry.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "managed": True,
                "connector_id": str(uuid4()),
                "container_name": "erp-cloud-connector",
                "image": "openlistteam/openlist:v4.2.6",
                "image_id": "sha256:" + "a" * 64,
                "origin": "http://127.0.0.1:5244",
                "data_dir": str(data),
                "admin_secret_file": str(secret),
                "objects": [],
            }
        )
    )
    path.chmod(0o600)
    return path


def test_snapshot_contains_cold_data_and_protected_secret_not_master_key(registry, tmp_path):
    recovery = module()
    (registry.parent / "backup.key").write_text("offline-recovery-master")
    dest = tmp_path / "snapshot"
    manifest = recovery.snapshot_connector(registry, dest)
    assert (dest / "data/data.db").read_bytes() == b"cold sqlite fixture"
    assert (dest / "admin-secret").read_text() == "protected-admin-password"
    assert not (dest / "backup.key").exists()
    assert "protected-admin-password" not in json.dumps(manifest)
    recovery.verify_connector_snapshot(dest, manifest)
    (dest / "data/data.db").write_bytes(b"tamper")
    with pytest.raises(recovery.RecoveryError, match="checksum"):
        recovery.verify_connector_snapshot(dest, manifest)


def recovery_source(mode="openlist_builtin_import", provider="baidu", version=2):
    return {"provider": provider, "protocol_version": version, "mode": mode,
            "auth_config_id": ("own-app:" if mode == "own_app" else "openlist-builtin-import-v1:") + provider,
            "auth_config_revision": 3 if mode == "own_app" else 1,
            "app_revision": 3 if mode == "own_app" else None}


def test_snapshot_retains_original_mode_version_and_frozen_configuration(registry, tmp_path):
    recovery = module()
    connector = json.loads(registry.read_text())["connector_id"]
    source = recovery_source()
    reference = {"candidate_id": str(uuid4()), "connector_id": connector,
                 "operation_id": str(uuid4()), "storage_id": "1", "webdav_user_id": "2",
                 "mount_path": "/owned", "state": "active", **source}
    credential = {"candidate_id": reference["candidate_id"], "connector_id": connector,
                  "username": "private-candidate", "password": "encrypted-archive-only", **source}
    destination = tmp_path / "snapshot"
    manifest = recovery.snapshot_connector(registry, destination,
                                          references=[reference], credentials=[credential])
    assert manifest["version"] == 2
    saved = manifest["registry"]["objects"][0]
    assert {key: saved[key] for key in source} == source
    assert saved["candidate_id"] == reference["candidate_id"]
    assert "encrypted-archive-only" not in json.dumps(manifest)
    assert json.loads((destination / "candidate-credentials.json").read_text()) == [credential]
    recovery.verify_connector_snapshot(destination, manifest)


@pytest.mark.parametrize("bad", [{"protocol_version": 3}, {"mode": "own_app"},
                                  {"auth_config_revision": 0}, {"provider": "115"}])
def test_snapshot_rejects_unknown_or_mixed_sources(registry, tmp_path, bad):
    recovery = module()
    connector = json.loads(registry.read_text())["connector_id"]
    reference = {"candidate_id": str(uuid4()), "connector_id": connector,
                 "operation_id": str(uuid4()), "storage_id": "1", "webdav_user_id": "2",
                 "mount_path": "/owned", "state": "active", **recovery_source(), **bad}
    with pytest.raises(recovery.RecoveryError, match="source"):
        recovery.snapshot_connector(registry, tmp_path / "snapshot", references=[reference])


def test_unknown_reference_shape_is_not_reinterpreted_as_legacy(registry, tmp_path):
    recovery = module()
    destination = tmp_path / "snapshot"
    manifest = recovery.snapshot_connector(registry, destination)
    manifest["registry"]["objects"] = [{"operation_id": str(uuid4()), "storage_id": "1",
        "webdav_user_id": "2", "mount_path": "/owned", "future_authorization": "unknown"}]
    with pytest.raises(recovery.RecoveryError, match="source"):
        recovery.verify_connector_snapshot(destination, manifest)


def test_restored_handoff_persists_restore_intent_and_retry_never_uses_normal_release(registry, tmp_path):
    recovery = module()
    payload = tmp_path / "authenticated"
    manifest = recovery.snapshot_connector(registry, payload)
    fence = snapshot_fence(manifest["registry"]["connector_id"], str(uuid4()))
    manifest.update(snapshot_fence=fence, was_running=False)
    journal = registry.parent / "snapshot-state.json"
    calls = []

    class Runtime:
        def running(self, value):
            return False

    class Bridge:
        def end_snapshot(self, value):
            pytest.fail("restoration must not take the normal same-database release path")

        def restore_snapshot(self, value):
            assert value == fence
            saved = json.loads(journal.read_text())
            assert saved["intent"] == "restore_handoff" and saved["phase"] == "release_pending"
            calls.append("restore")
            if len(calls) == 1:
                raise RuntimeError("synthetic-lost-restore-ack")
            return True

    with pytest.raises(RuntimeError, match="synthetic-lost-restore-ack"):
        recovery.handoff_restored_connector(registry, payload, manifest, Runtime(), Bridge())
    assert journal.exists()
    recovery.recover_interrupted_snapshot(registry, Runtime(), Bridge())
    assert calls == ["restore", "restore"] and not journal.exists()


@pytest.mark.parametrize("version", [0, 2, 99, True])
def test_unknown_snapshot_journal_format_never_restarts_or_releases(registry, version):
    recovery = module()
    connector = json.loads(registry.read_text())["connector_id"]
    operation = str(uuid4())
    recovery.atomic_json(registry.parent / "snapshot-state.json", {
        "version": version, "connector_id": connector, "operation_id": operation,
        "was_running": True, "phase": "fenced", "fence": snapshot_fence(connector, operation),
    })

    class Runtime:
        def running(self, value):
            pytest.fail("unknown format must not change runtime")

    class Bridge:
        def end_snapshot(self, fence):
            pytest.fail("unknown format must not release a maintenance lock")

    with pytest.raises(recovery.RecoveryError, match="ownership"):
        recovery.recover_interrupted_snapshot(registry, Runtime(), Bridge())


@pytest.mark.parametrize("foreign_gate", [False, True])
def test_restored_handoff_reconstructs_exact_journal_and_preserves_foreign_gate(
    registry, tmp_path, foreign_gate
):
    recovery = module()
    payload = tmp_path / "authenticated"
    manifest = recovery.snapshot_connector(registry, payload)
    identifier = manifest["registry"]["connector_id"]
    fence = snapshot_fence(identifier, str(uuid4()))
    manifest.update(snapshot_fence=fence, was_running=True)
    fresh = tmp_path / "fresh/cloud-connector"
    fresh.mkdir(parents=True, mode=0o700)
    shutil.copytree(payload / "data", fresh / "data")
    (fresh / "data").chmod(0o700)
    shutil.copyfile(payload / "admin-secret", fresh / "admin-secret")
    (fresh / "admin-secret").chmod(0o600)
    relocated = json.loads(registry.read_text())
    relocated.update(
        container_name="fresh-cloud-connector",
        data_dir=str(fresh / "data"),
        admin_secret_file=str(fresh / "admin-secret"),
    )
    target = fresh / "registry.json"
    recovery.atomic_json(target, relocated)
    events = []

    class Runtime:
        running_now = False

        def running(self, value):
            assert value["container_name"] == "fresh-cloud-connector"
            assert value["data_dir"] == str(fresh / "data")
            events.append("inspect")
            return self.running_now

        def start(self, value):
            self.running_now = True
            events.append("restart")

    class Bridge:
        def restore_snapshot(self, value):
            assert value == fence
            saved = json.loads((fresh / "snapshot-state.json").read_text())
            assert saved["operation_id"] == fence["operation_id"]
            events.append("end")
            return not foreign_gate

    if foreign_gate:
        with pytest.raises(recovery.RecoveryError, match="maintenance_release"):
            recovery.handoff_restored_connector(target, payload, manifest, Runtime(), Bridge())
        assert json.loads((fresh / "snapshot-state.json").read_text())["fence"] == fence
    else:
        recovery.handoff_restored_connector(target, payload, manifest, Runtime(), Bridge())
        assert not (fresh / "snapshot-state.json").exists()
    assert events.index("restart") < events.index("end")


@pytest.mark.parametrize(
    "mismatch",
    [
        "connector_id",
        "image_id",
        "secret",
        "foreign_journal",
        "missing_fence",
        "bad_running",
        "corrupt_payload",
        "runtime",
    ],
)
def test_restored_handoff_rejects_untrusted_identity_before_release(registry, tmp_path, mismatch):
    recovery = module()
    payload = tmp_path / "authenticated"
    manifest = recovery.snapshot_connector(registry, payload)
    fence = snapshot_fence(manifest["registry"]["connector_id"], str(uuid4()))
    manifest.update(snapshot_fence=fence, was_running=True)
    if mismatch == "connector_id":
        manifest["registry"]["connector_id"] = str(uuid4())
    elif mismatch == "image_id":
        manifest["image_id"] = "sha256:" + "b" * 64
    elif mismatch == "secret":
        (registry.parent / "admin-secret").write_text("different protected secret")
    elif mismatch == "foreign_journal":
        recovery.atomic_json(
            registry.parent / "snapshot-state.json",
            {"fence": snapshot_fence(fence["connector_id"], str(uuid4()))},
        )
    elif mismatch == "missing_fence":
        manifest.pop("snapshot_fence")
    elif mismatch == "bad_running":
        manifest["was_running"] = "true"
    elif mismatch == "corrupt_payload":
        (payload / "data/data.db").write_bytes(b"tampered")

    class Runtime:
        def running(self, value):
            if mismatch == "runtime":
                raise recovery.RecoveryError("connector_ownership_mismatch")
            return True

    class Bridge:
        def end_snapshot(self, value):
            pytest.fail("untrusted restored handoff must not release any gate")

    with pytest.raises(recovery.RecoveryError):
        recovery.handoff_restored_connector(registry, payload, manifest, Runtime(), Bridge())


@pytest.mark.parametrize("escape", ["symlink", "outside"])
def test_snapshot_rejects_links_and_outside_paths(registry, tmp_path, escape):
    recovery = module()
    if escape == "symlink":
        (registry.parent / "data/link").symlink_to(tmp_path)
    else:
        config = json.loads(registry.read_text())
        config["data_dir"] = str(tmp_path)
        registry.write_text(json.dumps(config))
    with pytest.raises(recovery.RecoveryError):
        recovery.snapshot_connector(registry, tmp_path / "snapshot")


def test_capture_restart_and_gate_release_even_on_snapshot_error(registry, tmp_path):
    recovery = module()
    events = []

    class Runtime:
        def running(self, value):
            return True

        def stop(self, value, timeout):
            events.append("stop")

        def start(self, value):
            events.append("start")

    class Bridge:
        def begin_snapshot(self, connector, operation):
            events.append("gate")
            return snapshot_fence(connector, operation)

        def end_snapshot(self, fence):
            events.append("ungate")
            return True

    def export():
        events.append("database")
        raise RuntimeError("snapshot export failed")

    with pytest.raises(RuntimeError):
        recovery.cold_snapshot(registry, tmp_path / "snapshot", Runtime(), Bridge(), export)
    assert events == ["gate", "stop", "database", "start", "ungate"]


def test_capture_exports_database_while_stopped_then_restarts(registry, tmp_path):
    recovery = module()
    events = []

    class Runtime:
        def running(self, value):
            return True

        def stop(self, value, timeout):
            events.append("stop")

        def start(self, value):
            events.append("start")

    class Bridge:
        def begin_snapshot(self, connector, operation):
            events.append("gate")
            return snapshot_fence(connector, operation)

        def end_snapshot(self, fence):
            events.append("ungate")
            return True

    result = recovery.cold_snapshot(
        registry, tmp_path / "snapshot", Runtime(), Bridge(), lambda: events.append("database")
    )
    assert events == ["gate", "stop", "database", "start", "ungate"]
    assert result[0]["image_id"] == "sha256:" + "a" * 64


def test_restart_failure_keeps_maintenance_journal_and_does_not_clear_gate(registry, tmp_path):
    recovery = module()
    events = []

    class Runtime:
        def running(self, value):
            return True

        def stop(self, value, timeout):
            pass

        def start(self, value):
            raise recovery.RecoveryError("connector_restart_failed")

    class Bridge:
        def begin_snapshot(self, connector, operation):
            return snapshot_fence(connector, operation)

        def end_snapshot(self, fence):
            events.append("ungate")
            return True

    with pytest.raises(recovery.RecoveryError, match="restart"):
        recovery.cold_snapshot(registry, tmp_path / "snapshot", Runtime(), Bridge(), dict)
    assert events == []
    assert (registry.parent / "snapshot-state.json").is_file()


@pytest.mark.parametrize("committed", [False, True])
def test_lost_begin_reply_reconciles_durable_same_operation(registry, tmp_path, committed):
    recovery = module()
    journal = registry.parent / "snapshot-state.json"
    operations, gate = [], []

    class Runtime:
        def running(self, value):
            return True

        def start(self, value):
            pytest.fail("original runtime is still running")

    class Bridge:
        def begin_snapshot(self, connector, operation):
            assert journal.is_file(), "begin intent must be durable before RPC"
            assert json.loads(journal.read_text())["operation_id"] == operation
            operations.append(operation)
            if len(operations) == 1:
                if committed:
                    gate.append(operation)
                raise recovery.RecoveryError("lost_begin_reply")
            assert not gate or gate == [operation]
            gate[:] = [operation]
            return snapshot_fence(connector, operation)

        def end_snapshot(self, fence):
            assert gate == [fence["operation_id"]]
            gate.clear()
            return True

    bridge = Bridge()
    with pytest.raises(recovery.RecoveryError, match="lost_begin"):
        recovery.cold_snapshot(registry, tmp_path / "snapshot", Runtime(), bridge, dict)
    recovery.recover_interrupted_snapshot(registry, Runtime(), bridge)
    assert operations[0] == operations[1]
    assert not gate and not journal.exists()


@pytest.mark.parametrize("lost_ack", [True, False])
def test_completed_end_replay_recovers_journal_and_allows_next_snapshot(
    registry, tmp_path, monkeypatch, lost_ack
):
    recovery = module()
    gate, ended = [], []

    class Runtime:
        def running(self, value):
            return False

    class Bridge:
        def begin_snapshot(self, connector, operation):
            assert not gate
            fence = snapshot_fence(connector, operation)
            gate.append(fence)
            return fence

        def end_snapshot(self, fence):
            if gate and gate != [fence]:
                return False
            gate.clear()
            ended.append(fence)
            if lost_ack and len(ended) == 1:
                raise recovery.RecoveryError("lost_end_reply")
            return True

    bridge = Bridge()
    if lost_ack:
        with pytest.raises(recovery.RecoveryError, match="lost_end"):
            recovery.cold_snapshot(registry, tmp_path / "first", Runtime(), bridge, dict)
    else:
        unlink = Path.unlink

        def crash_after_end(path, *args, **kwargs):
            if path.name == "snapshot-state.json":
                raise recovery.RecoveryError("crash_before_unlink")
            return unlink(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", crash_after_end)
            with pytest.raises(recovery.RecoveryError, match="crash_before_unlink"):
                recovery.cold_snapshot(registry, tmp_path / "first", Runtime(), bridge, dict)
    recovery.recover_interrupted_snapshot(registry, Runtime(), bridge)
    assert not (registry.parent / "snapshot-state.json").exists()
    recovery.cold_snapshot(registry, tmp_path / "next", Runtime(), bridge, dict)
    assert not gate


def test_failed_fence_journal_write_keeps_begin_intent_for_reconciliation(
    registry, tmp_path, monkeypatch
):
    recovery = module()
    journal = registry.parent / "snapshot-state.json"
    operations = []
    atomic = recovery.atomic_json
    failed = []

    def write(path, value):
        if value.get("phase") == "fenced" and not failed:
            failed.append(True)
            raise OSError("fence persistence failed")
        return atomic(path, value)

    monkeypatch.setattr(recovery, "atomic_json", write)

    class Runtime:
        def running(self, value):
            return False

    class Bridge:
        def begin_snapshot(self, connector, operation):
            operations.append(operation)
            return snapshot_fence(connector, operation)

        def end_snapshot(self, fence):
            return True

    with pytest.raises(OSError):
        recovery.cold_snapshot(registry, tmp_path / "snapshot", Runtime(), Bridge(), dict)
    assert json.loads(journal.read_text())["phase"] == "begin_pending"
    recovery.recover_interrupted_snapshot(registry, Runtime(), Bridge())
    assert operations[0] == operations[1] and not journal.exists()


def test_release_replay_preserves_foreign_live_gate_and_owned_journal(registry):
    recovery = module()
    connector = json.loads(registry.read_text())["connector_id"]
    owned = snapshot_fence(connector, str(uuid4()))
    foreign = snapshot_fence(connector, str(uuid4()))
    journal = registry.parent / "snapshot-state.json"
    recovery.atomic_json(
        journal,
        {
            "version": 1,
            "connector_id": connector,
            "operation_id": owned["operation_id"],
            "was_running": False,
            "phase": "release_pending",
            "fence": owned,
        },
    )

    class Runtime:
        def running(self, value):
            return False

    class Bridge:
        def end_snapshot(self, fence):
            assert fence == owned and fence != foreign
            return False  # Authoritative backend refuses a different live gate.

    with pytest.raises(recovery.RecoveryError, match="maintenance_release"):
        recovery.recover_interrupted_snapshot(registry, Runtime(), Bridge())
    assert journal.exists() and json.loads(journal.read_text())["fence"] == owned
    assert foreign["operation_id"] != owned["operation_id"]


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
@pytest.mark.parametrize(
    "identity", ["owned", "foreign_label", "foreign_mount", "foreign_image", "absent"]
)
def test_uncertain_restore_run_cleans_only_full_owned_identity(
    registry, tmp_path, failure, identity
):
    recovery = module()
    payload = tmp_path / "snapshot"
    manifest = recovery.snapshot_connector(registry, payload)
    created = []
    removed = []
    bind = []

    def docker(args, **kwargs):
        if "/bin/stat" in args:
            return b"501:20"
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            label = args[args.index("--label") + 1].split("=", 1)[1]
            directory = args[args.index("--mount") + 1].split("src=", 1)[1].split(",dst=", 1)[0]
            created.append(
                {
                    "Name": "/" + name,
                    "Image": manifest["image_id"]
                    if identity != "foreign_image"
                    else "sha256:" + "b" * 64,
                    "Config": {
                        "Labels": {
                            "erp.connector.verify": label
                            if identity != "foreign_label"
                            else "foreign"
                        }
                    },
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": directory
                            if identity != "foreign_mount"
                            else str(tmp_path / "foreign"),
                            "Destination": "/opt/openlist/data",
                        }
                    ],
                }
            )
            bind.append(Path(directory))
            raise recovery.RecoveryError("run_" + failure)
        if args[0] == "inspect":
            if identity == "absent":
                raise recovery.ContainerAbsent("connector_container_absent")
            return json.dumps(created).encode()
        if args[0] == "rm":
            removed.extend(created)
            created.clear()
            return b""
        pytest.fail("unexpected Docker boundary")

    with pytest.raises(recovery.RecoveryError):
        recovery.verify_isolated_connector(
            payload, manifest, docker, verify_database=lambda *_: None
        )
    assert bool(removed) is (identity == "owned")
    assert not bind[0].exists()


def test_recovery_requires_live_running_check_before_clearing_saved_gate(registry):
    recovery = module()
    events = []
    journal = registry.parent / "snapshot-state.json"
    journal.write_text(
        json.dumps(
            {
                "connector_id": json.loads(registry.read_text())["connector_id"],
                "was_running": True,
                "fence": {
                    "connector_id": json.loads(registry.read_text())["connector_id"],
                    "operation_id": str(uuid4()),
                    "revision": 1,
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            }
        )
    )
    journal.chmod(0o600)

    class Runtime:
        def running(self, value):
            events.append("running")
            return len(events) > 1

        def start(self, value):
            events.append("start")

    class Bridge:
        def end_snapshot(self, fence):
            events.append("ungate")
            return True

    recovery.recover_interrupted_snapshot(registry, Runtime(), Bridge())
    assert events == ["running", "start", "running", "ungate"]
    assert not journal.exists()


def test_recovery_rejects_fence_for_different_instance_before_runtime_or_rpc(registry):
    recovery = module()
    journal = registry.parent / "snapshot-state.json"
    journal.write_text(
        json.dumps(
            {
                "connector_id": json.loads(registry.read_text())["connector_id"],
                "was_running": False,
                "fence": {
                    "connector_id": str(uuid4()),
                    "operation_id": str(uuid4()),
                    "revision": 1,
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            }
        )
    )
    journal.chmod(0o600)

    class Runtime:
        def running(self, value):
            pytest.fail("foreign fence must be rejected before container operations")

    class Bridge:
        def end_snapshot(self, fence):
            pytest.fail("foreign fence must not clear another maintenance operation")

    with pytest.raises(recovery.RecoveryError, match="ownership"):
        recovery.recover_interrupted_snapshot(registry, Runtime(), Bridge())


def test_isolated_recovery_starts_portless_network_none_and_cleans_ownership(registry, tmp_path):
    recovery = module()
    dest = tmp_path / "snapshot"
    manifest = recovery.snapshot_connector(registry, dest)
    commands = []

    def docker(args, **kwargs):
        commands.append(args)
        if "/bin/stat" in args:
            return b"501:20\n"
        if "nc" in args:
            body = b'{"code":200,"data":{"token":"restored-test-token"}}'
            return (
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )
        if args[0] == "inspect":
            launched = next(c for c in commands if "--detach" in c)
            return json.dumps(
                [
                    {
                        "Name": "/" + launched[launched.index("--name") + 1],
                        "Image": manifest["image_id"],
                        "Mounts": [
                            {
                                "Type": "bind",
                                "Source": launched[launched.index("--mount") + 1]
                                .split("src=", 1)[1]
                                .split(",dst=", 1)[0],
                                "Destination": "/opt/openlist/data",
                            }
                        ],
                        "Config": {
                            "Labels": {
                                "erp.connector.verify": launched[
                                    launched.index("--label") + 1
                                ].split("=", 1)[1]
                            }
                        },
                        "HostConfig": {"NetworkMode": "none", "PortBindings": {}},
                        "State": {"Running": True},
                    }
                ]
            ).encode()
        return b""

    # The real archive verifier delegates only the Docker boundary, not checksums.
    recovery.verify_isolated_connector(dest, manifest, docker, verify_database=lambda p, m: None)
    command = next(c for c in commands if "--detach" in c)
    assert command[command.index("--network") + 1] == "none"
    assert "--publish" not in command and "-p" not in command
    assert commands[-1][0:3] == ["rm", "--force", "--volumes"]


@pytest.mark.skipif(
    os.environ.get("ERP_CLOUD_CONNECTOR_RECOVERY_TEST") != "1",
    reason="explicit isolated connector recovery drill",
)
def test_real_connector_cold_snapshot_restores_offline(tmp_path):
    recovery = module()
    runtime = recovery.DockerRuntime()
    identifier = str(uuid4())
    name = "erp-connector-source-" + uuid4().hex
    root = tmp_path / "cloud-connector"
    root.mkdir(mode=0o700)
    data = root / "data"
    data.mkdir(mode=0o700)
    secret = root / "admin-secret"
    secret.write_text(secrets.token_urlsafe(48))
    secret.chmod(0o600)
    env_file = root / "bootstrap-env"
    env_file.write_text(
        "OPENLIST_ADMIN_PASSWORD=" + secret.read_text() + "\nLOG_ENABLE=false\nUMASK=077\n"
    )
    env_file.chmod(0o600)
    created = False
    try:
        user = recovery.container_bind_user(data, "openlistteam/openlist:v4.2.6")
        runtime.call(
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
                "erp.cloud.connector=" + identifier,
                "--label",
                "erp.cloud.dedicated=true",
                "--user",
                user,
                "--env-file",
                str(env_file),
                "--mount",
                "type=bind,src=" + str(data) + ",dst=/opt/openlist/data",
                "openlistteam/openlist:v4.2.6",
            ],
            timeout=30,
        )
        created = True
        details = json.loads(runtime.call(["inspect", name]))[0]

        def api(path, body, token=""):
            return recovery.connector_local_request(
                runtime.call, name, path, body, authorization=token
            )

        deadline = time.monotonic() + 20
        while True:
            try:
                login = api(
                    "/api/auth/login", {"username": "admin", "password": secret.read_text()}
                )
                token = login["data"]["token"]
                break
            except (recovery.RecoveryError, KeyError, TypeError):
                if time.monotonic() > deadline:
                    pytest.fail("isolated connector bootstrap/login failed")
                time.sleep(0.1)
        # Local-only storage; no provider tokens or external network.
        assert (
            api(
                "/api/admin/storage/create",
                {
                    "mount_path": "/restore-fixture",
                    "driver": "Local",
                    "addition": json.dumps(
                        {"root_folder_path": "/opt/openlist/data", "mkdir_perm": "700"}
                    ),
                    "order": 0,
                },
                token,
            )["code"]
            == 200
        )
        assert (
            api(
                "/api/admin/user/create",
                {
                    "username": "backup-fixture",
                    "password": "isolated-only",
                    "base_path": "/restore-fixture",
                    "role": 0,
                    "permission": 0,
                    "disabled": False,
                },
                token,
            )["code"]
            == 200
        )
        runtime.call(["stop", "--time", "5", name], timeout=10)
        import sqlite3

        with sqlite3.connect(data / "data.db") as db:
            storage = db.execute(
                "SELECT id FROM x_storages WHERE mount_path='/restore-fixture'"
            ).fetchone()[0]
            user = db.execute("SELECT id FROM x_users WHERE username='backup-fixture'").fetchone()[
                0
            ]
        registry = root / "registry.json"
        registry.write_text(
            json.dumps(
                {
                    "version": 1,
                    "managed": True,
                    "connector_id": identifier,
                    "container_name": name,
                    "image": "openlistteam/openlist:v4.2.6",
                    "image_id": details["Image"],
                    "origin": "http://127.0.0.1:5244",
                    "data_dir": str(data),
                    "admin_secret_file": str(secret),
                    "objects": [
                        {
                            "operation_id": str(uuid4()),
                            "storage_id": str(storage),
                            "webdav_user_id": str(user),
                            "mount_path": "/restore-fixture",
                        }
                    ],
                }
            )
        )
        registry.chmod(0o600)
        dest = tmp_path / "restored-connector"
        value = recovery.load_registry(registry)
        runtime.start(value)
        assert runtime.running(value)

        class Gate:
            released = False

            def begin_snapshot(self, connector_id, operation_id):
                assert runtime.running(value)
                return {
                    "connector_id": connector_id,
                    "operation_id": operation_id,
                    "revision": 1,
                    "expires_at": "2099-01-01T00:00:00Z",
                }

            def end_snapshot(self, fence):
                assert runtime.running(value)
                self.released = True
                return True

        gate = Gate()

        def export():
            assert not runtime.running(value)
            return {
                "cloud_connector_references": [
                    {
                        "candidate_id": str(uuid4()),
                        "connector_id": identifier,
                        "operation_id": str(uuid4()),
                        "storage_id": str(storage),
                        "webdav_user_id": str(user),
                        "mount_path": "/restore-fixture",
                        "state": "active",
                    }
                ],
                "cloud_connector_credentials": [
                    {
                        "candidate_id": str(uuid4()),
                        "connector_id": identifier,
                        "username": "backup-fixture",
                        "password": "isolated-only",
                    }
                ],
            }

        manifest, metadata = recovery.cold_snapshot(registry, dest, runtime, gate, export)
        assert gate.released and runtime.running(value)
        assert "cloud_connector_credentials" not in metadata
        assert "isolated-only" not in json.dumps(manifest)
        recovery.verify_isolated_connector(dest, manifest)
        assert recovery.inventory(dest) == manifest["files"]
        (dest / "admin-secret").write_text("not-the-restored-admin-password")
        manifest["files"] = recovery.inventory(dest)
        with pytest.raises(recovery.RecoveryError, match="credential"):
            recovery.verify_isolated_connector(dest, manifest)
    finally:
        if created:
            runtime.call(["rm", "--force", "--volumes", name], timeout=30)
        env_file.unlink(missing_ok=True)
        for owned_directory in (root, tmp_path / "restored-connector"):
            if owned_directory.exists():
                shutil.rmtree(owned_directory)
