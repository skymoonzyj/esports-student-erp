import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def runner():
    path = ROOT / "scripts/erp-cloud-authorize.py"
    assert path.is_file(), "authorization runner has not been implemented"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("erp_cloud_authorize", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_connector_admin_exchanges_installed_password_for_memory_only_token(
    monkeypatch, tmp_path
):
    from uuid import uuid4

    module = runner()
    import cloud_auth.openlist_admin as admin_module

    root = tmp_path / "cloud-connector"
    root.mkdir(mode=0o700)
    data = root / "data"
    data.mkdir(mode=0o700)
    secret = root / "admin-secret"
    secret.write_text("installed-password")
    secret.chmod(0o600)
    connector_id = uuid4()
    registry = {
        "version": 1,
        "managed": True,
        "state": "managed",
        "connector_id": str(connector_id),
        "image": "openlistteam/openlist:v4.2.6",
        "image_id": "sha256:" + "a" * 64,
        "origin": "http://127.0.0.1:5244",
        "container_name": "erp-cloud-connector",
        "connector_version": "4.2.6",
        "data_dir": str(data),
        "admin_secret_file": str(secret),
    }
    path = root / "registry.json"
    path.write_text(json.dumps(registry))
    path.chmod(0o600)
    requests = []

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port) == ("127.0.0.1", 5244)

        def request(self, method, path, body=None, headers=None):
            headers = headers or {}
            if path == "/api/auth/login":
                assert json.loads(body) == {"username": "admin", "password": "installed-password"}
                assert "Authorization" not in headers
                self.response = {"code": 200, "data": {"token": "in-memory-token"}}
            else:
                assert headers.get("Authorization") == "in-memory-token"
                self.response = {"code": 200, "data": {"content": [], "total": 0}}
            requests.append((method, path))

        def getresponse(self):
            result = self.response

            class Response:
                status = 200

                def read(self, limit):
                    return json.dumps(result).encode()

            return Response()

        def close(self):
            pass

    monkeypatch.setattr(admin_module.http.client, "HTTPConnection", Connection)
    client = module.connector_admin(
        {"app_dir": str(tmp_path)},
        {
            "candidate": {
                "connector_id": str(connector_id),
                "id": str(uuid4()),
                "config_revision": 1,
            }
        },
        None,
    )
    assert client.api.call("GET", "/api/admin/user/list") == {"content": [], "total": 0}
    assert requests == [("POST", "/api/auth/login"), ("GET", "/api/admin/user/list")]
    assert secret.read_text() == "installed-password"
    assert "in-memory-token" not in path.read_text()


def test_bridge_uses_fixed_argv_and_stdin_not_secret_arguments(monkeypatch):
    module = runner()
    seen = []

    def process(argv, **kwargs):
        seen.append((argv, json.loads(kwargs["input"])))
        kwargs["stdout"].write(b'{"ok":true}')
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(module.subprocess, "run", process)
    assert module.bridge({"project": "erp"}, "claim") == {"ok": True}
    assert seen == [
        (
            [
                "docker",
                "exec",
                "-i",
                "erp-api-1",
                "python",
                "-m",
                "app.operations.cloud_auth_bridge",
            ],
            {"action": "claim"},
        )
    ]


def test_bridge_rejects_oversized_payload_before_spawning(monkeypatch):
    module = runner()

    def forbidden(*args, **kwargs):
        pytest.fail("must validate before external process")

    monkeypatch.setattr(module.subprocess, "run", forbidden)
    with pytest.raises(module.AuthRunnerError):
        module.bridge({"project": "erp"}, "complete", result={"token": "x" * 65536})


def test_zero_deadline_does_not_claim_any_work(tmp_path):
    module = runner()
    calls = []
    module.run(
        {"project": "erp", "app_dir": str(tmp_path)},
        deadline_seconds=0,
        call=lambda config, action, **payload: calls.append(action),
    )
    assert calls == []


@pytest.mark.parametrize(
    "change", ["release", "failed", "marker_missing", "state_missing", "current", "outside"]
)
def test_each_operation_revalidates_release_before_any_side_effect(tmp_path, monkeypatch, change):
    module = runner()
    from cloud_auth import recovery

    first = tmp_path / "releases/one"
    second = tmp_path / "releases/two"
    for release in (first, second):
        (release / "scripts").mkdir(parents=True)
        (release / "scripts/erp-cloud-authorize.py").touch()
    marker = tmp_path / ".active-release"
    state = tmp_path / ".deployment-state"
    current = tmp_path / "current"
    current.symlink_to(first)
    marker.write_text(str(first))
    state.write_text(f"status=complete\nrelease={first}\n")
    monkeypatch.setattr(module, "__file__", str(first / "scripts/erp-cloud-authorize.py"))
    events = []
    monkeypatch.setattr(module, "load_registry", lambda _: {"connector_id": "registered"})
    monkeypatch.setattr(
        recovery, "recover_interrupted_snapshot", lambda *args: events.append("recover")
    )
    clock = [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        clock[0] += seconds
        if change == "release":
            marker.write_text(str(second))
            state.write_text(f"status=complete\nrelease={second}\n")
            current.unlink()
            current.symlink_to(second)
        elif change == "failed":
            state.write_text(f"status=failed\nrelease={first}\n")
        elif change == "marker_missing":
            marker.unlink(missing_ok=True)
        elif change == "state_missing":
            state.unlink(missing_ok=True)
        elif change == "current":
            current.unlink()
            current.symlink_to(second)
        elif change == "outside":
            marker.write_text(str(tmp_path.parent))
            state.write_text(f"status=complete\nrelease={tmp_path.parent}\n")
            current.unlink()
            current.symlink_to(tmp_path.parent)

    monkeypatch.setattr(module.time, "sleep", sleep)

    def call(config, action, **payload):
        events.append(action)
        return {"work": None}

    module.run({"project": "erp", "app_dir": str(tmp_path)}, deadline_seconds=3, call=call)
    assert events == ["recover", "heartbeat", "claim"]


@pytest.mark.parametrize(
    "status,unchanged,allowed",
    [
        ("healthy", None, True),
        ("complete", None, True),
        ("backup_failed", None, True),
        ("attempt_failed", "true", True),
        ("attempt_failed", "false", False),
        ("deploying", None, False),
    ],
)
def test_runner_release_gate_matches_wrapper_eligibility(
    tmp_path, monkeypatch, status, unchanged, allowed
):
    module = runner()
    release = tmp_path / "releases/one"
    (release / "scripts").mkdir(parents=True)
    source = release / "scripts/erp-cloud-authorize.py"
    source.touch()
    monkeypatch.setattr(module, "__file__", str(source))
    (tmp_path / "current").symlink_to(release)
    (tmp_path / ".active-release").write_text(str(release))
    (tmp_path / ".deployment-state").write_text(
        f"status={status}\nrelease={release}\nactive_unchanged={unchanged}\n"
    )
    if allowed:
        module.validate_active_release(tmp_path)
    else:
        with pytest.raises(module.ReleaseMismatch):
            module.validate_active_release(tmp_path)


def test_missing_startup_markers_block_before_registry_or_rpc(tmp_path, monkeypatch):
    module = runner()

    def forbidden(*args, **kwargs):
        pytest.fail("unverified release must not access connector or private RPC")

    monkeypatch.setattr(module, "load_registry", forbidden)
    module.run({"project": "erp", "app_dir": str(tmp_path)}, call=forbidden)


def test_recovery_deletes_only_journaled_probe_before_reset(monkeypatch):
    module = runner()
    operation_id = "11111111-1111-4111-8111-111111111111"
    deleted = []

    class Dav:
        def __init__(self, *args):
            pass

        def delete(self, path):
            deleted.append(path)

    monkeypatch.setattr(module, "Transport", Dav, raising=False)
    record = type("Record", (), {"directory": "/备份", "username": "u", "password": "p"})()

    class Journal:
        def checkpoint(self, kind, identity, **payload):
            assert deleted == ["/erp-probe-11111111111141118111111111111111-" + "a" * 32 + ".bin"]
            return {"ok": True}

    proof = {
        "path": "/erp-probe-11111111111141118111111111111111-" + "a" * 32 + ".bin",
        "directory": "/备份",
        "finished": False,
    }
    assert hasattr(module, "recover_probe"), (
        "interrupted probe recovery must precede any new upload"
    )
    module.recover_probe(operation_id, proof, record, Journal())
    with pytest.raises(module.AuthRunnerError):
        module.recover_probe(operation_id, {**proof, "path": "/existing-backup"}, record, Journal())
    assert len(deleted) == 1


def test_runner_exclusion_is_nonblocking(tmp_path):
    module = runner()
    with module.locked(tmp_path / ".cloud-auth-runner.lock"), pytest.raises(module.AuthRunnerError):
        module.run({"app_dir": str(tmp_path), "project": "erp"}, deadline_seconds=1)


def test_execute_preserves_canonical_readback_mismatch_code(monkeypatch):
    module = runner()
    from erp_cloud_transport import CloudError

    def mismatch(*args):
        raise CloudError("cloud_readback_failed")

    monkeypatch.setattr(module, "perform", mismatch)
    responses = []
    module.execute(
        {},
        {"action": "probe", "fence": {}},
        lambda config, action, **payload: responses.append((action, payload)),
    )
    assert responses == [
        (
            "complete",
            {"fence": {}, "result": {"action": "probe", "error_code": "cloud_remote_corrupt"}},
        )
    ]


def test_apply_receipt_requires_actual_connector_ownership_recheck(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from tests.cli.test_cloud_connector_candidates import API, Journal

    module = runner()
    from cloud_auth.openlist_admin import OpenListAdmin, RegisteredConnector

    journal = Journal()
    api = API(journal)
    operation = uuid4()
    candidate = uuid4()
    admin = OpenListAdmin(
        RegisteredConnector(uuid4(), "http://127.0.0.1:5244", "4.2.6", True),
        api,
        journal,
        lambda provider, tokens: SimpleNamespace(provider=provider, account_id="12345"),
        candidate_context=lambda op: (candidate, 1),
    )
    access = admin.prepare(
        operation,
        "baidu",
        {"client_id": "own", "client_secret": "owned"},
        SimpleNamespace(access_token="a", refresh_token="r"),
    )
    admin.select_directory(access.ref, "/备份")
    monkeypatch.setattr(module, "connector_admin", lambda config, work, journal: admin)
    monkeypatch.setattr(module, "RemoteJournal", lambda *args: journal)
    work = {
        "id": str(uuid4()),
        "action": "apply",
        "fence": {"protocol_version": 1, "mode": "own_app"},
        "candidate": {"operation_id": str(operation), "directory": "/备份"},
    }
    api.objects["user"][0]["base_path"] = "/foreign"
    with pytest.raises(RuntimeError):
        module.perform({}, work, lambda *args, **kwargs: None)
    api.objects["user"][0]["base_path"] = access.ref.mount_path + "/备份"
    assert module.perform({}, work, lambda *args, **kwargs: None)["host_drain_receipt"]


@pytest.mark.parametrize("missing", ["storage", "user", "meta", "all"])
def test_unbind_known_absence_checks_survivors_without_recreating_or_remote_deletes(
    monkeypatch, tmp_path, missing
):
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from tests.cli.test_cloud_connector_candidates import API, Journal

    module = runner()
    from cloud_auth.openlist_admin import OpenListAdmin, RegisteredConnector

    journal = Journal()
    api = API(journal)
    operation = uuid4()
    candidate = uuid4()
    admin = OpenListAdmin(
        RegisteredConnector(uuid4(), "http://127.0.0.1:5244", "4.2.6", True),
        api,
        journal,
        lambda provider, tokens: SimpleNamespace(provider=provider, account_id="12345"),
        candidate_context=lambda op: (candidate, 1),
    )
    access = admin.prepare(
        operation,
        "baidu",
        {"client_id": "own", "client_secret": "owned"},
        SimpleNamespace(access_token="a", refresh_token="r"),
    )
    admin.select_directory(access.ref, "/备份")
    for resource in list(api.objects) if missing == "all" else [missing]:
        api.objects[resource] = []
    before = len(api.events)
    monkeypatch.setattr(module, "connector_admin", lambda config, work, journal: admin)
    monkeypatch.setattr(module, "RemoteJournal", lambda *args: journal)
    work = {
        "id": str(uuid4()),
        "action": "apply",
        "fence": {"protocol_version": 1, "mode": "own_app"},
        "candidate": {"operation_id": str(operation), "directory": "/备份"},
    }
    with module.operation_locks(tmp_path):
        with pytest.raises(RuntimeError):
            module.perform({}, work, lambda *args, **kwargs: None)
        work["action"] = "unbind"
        assert module.perform({}, work, lambda *args, **kwargs: None)["host_drain_receipt"]
        record = journal.read(operation)
        journal.save(replace(record, creation_unknown=True))
        with pytest.raises(RuntimeError):
            module.perform({}, work, lambda *args, **kwargs: None)
        journal.save(record)
        if api.objects["user"]:
            api.objects["user"][0]["base_path"] = "/foreign"
            with pytest.raises(RuntimeError):
                module.perform({}, work, lambda *args, **kwargs: None)
            api.objects["user"][0]["base_path"] = record.mount_path + record.directory
        admin.cleanup_operation(operation)
    assert all(not values for values in api.objects.values())
    assert all(
        method == "GET" or "/api/admin/" in path and "/delete?" in path
        for method, path, _ in api.events[before:]
    )
