import hashlib
import hmac
import http.client
import importlib.util
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def registered_copy_connector(tmp_path, monkeypatch):
    from cloud_auth.recovery import DockerRuntime

    root = tmp_path / "cloud-connector"
    root.mkdir(mode=0o700)
    data = root / "data"
    data.mkdir(mode=0o700)
    secret = root / "admin-secret"
    secret.write_text("private-admin-password")
    secret.chmod(0o600)
    value = {
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
    path = root / "registry.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    details = [
        {
            "Config": {
                "Labels": {
                    "erp.cloud.connector": value["connector_id"],
                    "erp.cloud.dedicated": "true",
                }
            },
            "Image": value["image_id"],
            "State": {"Running": True},
            "Mounts": [{"Type": "bind", "Source": str(data), "Destination": "/opt/openlist/data"}],
        }
    ]

    def inspect(args, **kwargs):
        assert args == ["inspect", "erp-cloud-connector"]
        return json.dumps(details).encode()

    monkeypatch.setattr(DockerRuntime, "call", lambda self, args, **kwargs: inspect(args, **kwargs))
    return value, details, path


def load():
    spec = importlib.util.spec_from_file_location(
        "cloud_runner", ROOT / "scripts/erp-cloud-backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dav():
    files, calls = {}, []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_MKCOL(self):
            self.send_response(201)
            self.end_headers()

        def do_PUT(self):
            calls.append(("PUT", self.path))
            files[self.path] = self.rfile.read(int(self.headers["Content-Length"]))
            if files.get("defer_put"):
                time.sleep(0.2)
            self.send_response(201)
            self.end_headers()

        def do_GET(self):
            calls.append(("GET", self.path))
            if self.headers.get("Authorization") != files.get(
                "_expected_authorization", "Basic dXNlcjpzZWNyZXQ="
            ):
                self.send_response(401)
                self.end_headers()
                return
            if self.path.startswith("/dav/redirect"):
                self.send_response(302)
                self.send_header("Location", "/stolen")
                self.end_headers()
                return
            value = files.get(self.path)
            if files.get("corrupt") and self.path.endswith("archive.tar.gz.enc"):
                value = b"corrupted"
            self.send_response(200 if value is not None else 404)
            self.end_headers()
            if value is not None:
                self.wfile.write(value)

        def do_DELETE(self):
            calls.append(("DELETE", self.path))
            files.pop(self.path, None)
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", files, calls
    server.shutdown()
    thread.join()


@pytest.fixture
def managed_dav():
    yield from dav.__wrapped__()


def test_probe_roundtrip_deletes_only_own_file(dav):
    runner = load()
    url, files, calls = dav
    files["/dav/baidu/keep"] = b"keep"
    runner.test_connection(runner.Transport(url, "user", "secret"), "/baidu")
    assert files == {"/dav/baidu/keep": b"keep"}
    assert [method for method, _ in calls] == ["PUT", "GET", "DELETE"]


def test_managed_user_root_probe_never_double_slashes_or_touches_other_files(dav):
    runner = load()
    url, files, calls = dav
    files["/dav/keep"] = b"keep"
    runner.test_connection(runner.Transport(url, "user", "secret"), "/")
    assert files == {"/dav/keep": b"keep"}
    assert [method for method, _ in calls] == ["PUT", "GET", "DELETE"]
    assert all(path.startswith("/dav/.erp-probe-") for _, path in calls)


def test_redirect_does_not_forward_credentials(dav, tmp_path):
    runner = load()
    url, _, calls = dav
    with pytest.raises(runner.CloudError, match="cloud_redirect_rejected"):
        runner.Transport(url, "user", "secret").get("/redirect", tmp_path / "out", 32)
    assert calls == [("GET", "/dav/redirect")]


def test_managed_auth_401_is_distinguished_from_manual_connector_failure(dav, tmp_path):
    runner = load()
    url, _, _ = dav
    with pytest.raises(runner.CloudError, match="cloud_authorization_invalid"):
        runner.Transport(url, "user", "wrong", managed_auth=True).get(
            "/file", tmp_path / "managed", 32
        )
    with pytest.raises(runner.CloudError, match="cloud_connector_failed"):
        runner.Transport(url, "user", "wrong").get("/file", tmp_path / "manual", 32)


@pytest.mark.parametrize("first", ["manual", "managed"])
def test_mixed_jobs_route_managed_credentials_only_to_registered_connector(
    dav,
    managed_dav,
    tmp_path,
    monkeypatch,
    registered_copy_connector,
    first,
):
    runner = load()
    legacy_origin, _, calls = dav
    managed_origin, managed_files, managed_calls = managed_dav
    managed_files["_expected_authorization"] = "Basic ZGVkaWNhdGVkOm1hbmFnZWQtc2VjcmV0"
    registry, _, _ = registered_copy_connector
    root = tmp_path / "backups"
    root.mkdir()
    key = tmp_path / "key"
    key.write_bytes(b"0" * 96)
    key.chmod(0o600)
    connections = []
    original = http.client.HTTPConnection

    def connection(host, port=None, **kwargs):
        # Keep the fixed production endpoint offline: route only this isolated
        # test connection to the second ephemeral server, recording its requested origin.
        destination = urlsplit(managed_origin) if (host, port) == ("127.0.0.1", 5244) else None
        stream = original(
            destination.hostname if destination else host,
            destination.port if destination else port,
            **kwargs,
        )
        original_header = stream.putheader

        def header(name, *values):
            if name == "Authorization":
                connections.append((host, port, values[0]))
            original_header(name, *values)

        stream.putheader = header
        return stream

    monkeypatch.setattr(http.client, "HTTPConnection", connection)
    jobs = {
        "manual": {
            "id": "manual",
            "revision": 1,
            "kind": "test",
            "username": "user",
            "password": "secret",
            "remote_path": "/legacy",
            "managed_auth": False,
        },
        "managed": {
            "id": "managed",
            "revision": 1,
            "kind": "test",
            "username": "dedicated",
            "password": "managed-secret",
            "remote_path": "/",
            "managed_auth": True,
            "connector_id": registry["connector_id"],
            "connector_url": legacy_origin,
        },
    }
    sequence = iter([jobs[first], jobs["managed" if first == "manual" else "manual"], None])
    results = []

    def bridge(config, action, **payload):
        if action == "claim":
            return {"job": next(sequence)}
        if action == "finish":
            results.append(payload)
        return {"ok": True}

    runner.run(
        {
            "app_dir": str(tmp_path),
            "backup_dir": str(root),
            "key_file": str(key),
            "connector_url": legacy_origin,
        },
        bridge,
    )
    managed_auth = "Basic ZGVkaWNhdGVkOm1hbmFnZWQtc2VjcmV0"
    assert any(auth == managed_auth for _, _, auth in connections)
    assert all(
        (host, port) == ("127.0.0.1", 5244)
        for host, port, auth in connections
        if auth == managed_auth
    )
    assert any(r["job_id"] == "manual" and r["success"] for r in results)
    assert any(r["job_id"] == "managed" and r["success"] for r in results)
    assert calls and all(path.startswith("/dav/legacy/") for _, path in calls)
    assert managed_calls and all(path.startswith("/dav/.erp-probe-") for _, path in managed_calls)


@pytest.mark.parametrize(
    "failure",
    [
        "missing_id",
        "foreign_id",
        "stopped",
        "foreign_runtime",
        "unsafe_registry",
        "missing_registry",
        "coerced_mode",
    ],
)
def test_managed_copy_origin_never_falls_back_after_identity_failure(
    tmp_path,
    registered_copy_connector,
    failure,
):
    runner = load()
    registry, details, path = registered_copy_connector
    config = {"app_dir": str(tmp_path), "connector_url": "http://legacy.private:9876"}
    job = {
        "managed_auth": True,
        "connector_id": registry["connector_id"],
        "connector_url": "http://public.invalid",
        "username": "dedicated",
        "password": "managed-secret",
    }
    if failure == "missing_id":
        job.pop("connector_id")
    elif failure == "foreign_id":
        job["connector_id"] = str(uuid4())
    elif failure == "stopped":
        details[0]["State"]["Running"] = False
    elif failure == "foreign_runtime":
        details[0]["Config"]["Labels"]["erp.cloud.connector"] = str(uuid4())
    elif failure == "unsafe_registry":
        registry["origin"] = "http://public.invalid"
        path.write_text(json.dumps(registry))
    elif failure == "missing_registry":
        path.unlink()
    else:
        job["managed_auth"] = "true"
    with pytest.raises(runner.CloudError) as error:
        runner.copy_origin(config, job)
    assert str(error.value) == "cloud_connector_failed"
    assert "managed-secret" not in str(error.value)
    assert runner.copy_origin(config, {"managed_auth": False}) == "http://legacy.private:9876"


@pytest.mark.parametrize(
    "path", ["/../etc", "//host/x", "/baidu/%2fsecret", "/baidu?x", "/baidu\\x"]
)
def test_rejects_unsafe_paths(path):
    runner = load()
    with pytest.raises(runner.CloudError):
        runner.safe_path(path)


def pair(tmp_path, key=b"key"):
    root = tmp_path / "backup-20261002T010203Z-aabbccdd"
    root.mkdir()
    data = b"authenticated encrypted archive fixture"
    (root / "archive.tar.gz.enc").write_bytes(data)
    meta = {
        "format_version": 1,
        "backup_id": root.name,
        "verified_at": "2026-10-02T01:03:00+00:00",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "hmac_sha256": hmac.new(key, data, hashlib.sha256).hexdigest(),
    }
    meta["metadata_hmac_sha256"] = hmac.new(
        key,
        json.dumps(meta, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    (root / "complete.json").write_text(json.dumps(meta))
    return root, key


def test_copy_publishes_marker_last_and_reuses_verified_pair(dav, tmp_path):
    runner = load()
    url, _files, calls = dav
    directory, key = pair(tmp_path)
    transport = runner.Transport(url, "user", "secret")
    assert runner.copy_backup(transport, "/baidu", directory, key) == directory.name
    assert [method for method, _ in calls] == ["GET", "PUT", "GET", "PUT", "GET"]
    calls.clear()
    assert runner.copy_backup(transport, "/baidu", directory, key) == directory.name
    assert [method for method, _ in calls] == ["GET", "GET"]
    assert not list(tmp_path.glob(".cloud-readback-*"))


def test_managed_user_root_copy_preserves_encrypted_pair_and_readback(dav, tmp_path):
    runner = load()
    url, files, calls = dav
    directory, key = pair(tmp_path)
    assert (
        runner.copy_backup(runner.Transport(url, "user", "secret"), "/", directory, key)
        == directory.name
    )
    assert set(files) == {
        f"/dav/{directory.name}/archive.tar.gz.enc",
        f"/dav/{directory.name}/complete.json",
    }
    assert all("//" not in path for _, path in calls)


def test_corrupt_readback_never_publishes_complete(dav, tmp_path):
    runner = load()
    url, files, calls = dav
    directory, key = pair(tmp_path)
    files["corrupt"] = True
    with pytest.raises(runner.CloudError, match="cloud_remote_corrupt"):
        runner.copy_backup(runner.Transport(url, "user", "secret"), "/baidu", directory, key)
    assert not any(p.endswith("/complete.json") for method, p in calls if method == "PUT")


def test_malformed_remote_copy_fails_only_its_provider(dav, tmp_path):
    runner = load()
    url, files, _ = dav
    root = tmp_path / "backups"
    root.mkdir()
    key_file = tmp_path / "key"
    key_file.write_bytes(b"0" * 96)
    key_file.chmod(0o600)
    key = hashlib.sha256(b"erp-backup-authentication-v1\0" + b"0" * 96).digest()
    directory, _ = pair(root, key)
    files["/dav/baidu/" + directory.name + "/complete.json"] = b"[]"
    files["/dav/baidu/" + directory.name + "/archive.tar.gz.enc"] = (
        directory / "archive.tar.gz.enc"
    ).read_bytes()
    jobs = iter(
        [
            {
                "id": "badcopy",
                "revision": 1,
                "kind": "copy",
                "username": "user",
                "password": "secret",
                "remote_path": "/baidu",
                "backup_id": directory.name,
            },
            {
                "id": "nextprovider",
                "revision": 1,
                "kind": "test",
                "username": "user",
                "password": "secret",
                "remote_path": "/115",
            },
            None,
        ]
    )
    results = []

    def call(config, action, **payload):
        if action == "claim":
            return {"job": next(jobs)}
        if action == "finish":
            results.append(payload)
        return {"ok": True}

    runner.run(
        {
            "app_dir": str(tmp_path),
            "backup_dir": str(root),
            "key_file": str(key_file),
            "connector_url": url,
        },
        call,
    )
    assert [(r["job_id"], r["success"], r["error_code"]) for r in results] == [
        ("badcopy", False, "cloud_remote_corrupt"),
        ("nextprovider", True, None),
    ]


def test_symlink_archive_rejected_before_remote_io(dav, tmp_path):
    runner = load()
    url, _, calls = dav
    directory, key = pair(tmp_path)
    archive = directory / "archive.tar.gz.enc"
    archive.unlink()
    archive.symlink_to("/etc/passwd")
    with pytest.raises(runner.CloudError, match="cloud_local_invalid"):
        runner.copy_backup(runner.Transport(url, "user", "secret"), "/baidu", directory, key)
    assert calls == []


def test_probe_corruption_and_credential_error_are_sanitized(dav, tmp_path):
    runner = load()
    url, files, _ = dav
    files["/dav/baidu/existing"] = b"untouched"
    with pytest.raises(runner.CloudError) as error:
        runner.Transport(url, "sensitive-user", "sensitive-password").get(
            "/missing", tmp_path / "out", 32
        )
    assert str(error.value) == "cloud_connector_failed"


def test_bridge_fixed_argv_stdin_only(monkeypatch):
    runner = load()
    seen = {}

    def execute(argv, **kwargs):
        seen.update(argv=argv, payload=json.loads(kwargs["input"]))
        kwargs["stdout"].write(b'{"ok": true}')
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.bridge({"project": "erp"}, "finish", password="private") == {"ok": True}
    assert seen["argv"] == [
        "docker",
        "exec",
        "-i",
        "erp-api-1",
        "python",
        "-m",
        "app.operations.cloud_bridge",
    ]
    assert seen["payload"] == {"action": "finish", "password": "private"}


def test_project_shell_payload_rejected(monkeypatch):
    runner = load()
    with pytest.raises(runner.CloudError):
        runner.bridge({"project": "erp; echo secret"}, "claim")


def test_transfer_progress_refreshes_worker_heartbeat(dav, tmp_path):
    runner = load()
    url, files, _ = dav
    files["/dav/baidu/file"] = b"content"
    heartbeats = []
    transport = runner.Transport(url, "user", "secret", progress=lambda: heartbeats.append(True))
    transport.get("/baidu/file", tmp_path / "out", 32)
    assert heartbeats


def test_environment_proxy_is_ignored(dav, tmp_path, monkeypatch):
    runner = load()
    url, files, _ = dav
    files["/dav/baidu/file"] = b"content"
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    runner.Transport(url, "user", "secret").get("/baidu/file", tmp_path / "out", 32)
    assert (tmp_path / "out").read_bytes() == b"content"


def test_failed_target_does_not_stop_next_job(dav, tmp_path):
    runner = load()
    url, _, _ = dav
    root = tmp_path / "backups"
    root.mkdir()
    key = tmp_path / "key"
    key.write_bytes(b"0" * 96)
    key.chmod(0o600)
    jobs = iter(
        [
            {
                "id": "first",
                "revision": 1,
                "kind": "test",
                "username": "user",
                "password": "secret",
                "remote_path": "/../bad",
            },
            {
                "id": "second",
                "revision": 1,
                "kind": "test",
                "username": "user",
                "password": "secret",
                "remote_path": "/baidu",
            },
            None,
        ]
    )
    results = []

    def call(config, action, **payload):
        if action == "claim":
            return {"job": next(jobs)}
        if action == "finish":
            results.append(payload)
        return {"ok": True}

    runner.run(
        {
            "app_dir": str(tmp_path),
            "backup_dir": str(root),
            "key_file": str(key),
            "connector_url": url,
        },
        call,
    )
    assert [(r["job_id"], r["success"], r["error_code"]) for r in results] == [
        ("first", False, "cloud_invalid_path"),
        ("second", True, None),
    ]


def test_partial_upload_fails_without_marker(dav, tmp_path, monkeypatch):
    runner = load()
    url, files, _calls = dav
    directory, key = pair(tmp_path)
    transport = runner.Transport(url, "user", "secret")
    actual_put = transport.put

    def partial(path, source):
        if path.endswith("archive.tar.gz.enc"):
            files["/dav" + path] = b"partial"
            raise runner.CloudError("cloud_connector_failed")
        actual_put(path, source)

    monkeypatch.setattr(transport, "put", partial)
    with pytest.raises(runner.CloudError):
        runner.copy_backup(transport, "/baidu", directory, key)
    assert not any(path.endswith("complete.json") for path in files)


def test_signed_but_unverified_backup_not_selected_or_copied(tmp_path):
    runner = load()
    directory, key = pair(tmp_path)
    meta = json.loads((directory / "complete.json").read_text())
    del meta["verified_at"]
    meta.pop("metadata_hmac_sha256")
    meta["metadata_hmac_sha256"] = hmac.new(
        key,
        json.dumps(meta, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    (directory / "complete.json").write_text(json.dumps(meta))
    assert runner.latest(tmp_path, key) is None
    with pytest.raises(runner.CloudError, match="cloud_local_invalid"):
        runner.local_pair(directory, key)


def test_latest_authenticates_marker_without_reading_large_archive(tmp_path, monkeypatch):
    runner = load()
    directory, key = pair(tmp_path)

    def forbidden(*args):
        raise AssertionError("archive hashed during selection")

    monkeypatch.setattr(runner.backup, "digest", forbidden)
    assert runner.latest(tmp_path, key) == directory.name


def test_put_deferred_provider_response_keeps_heartbeat_alive(dav, tmp_path):
    runner = load()
    url, files, _ = dav
    files["defer_put"] = True
    source = tmp_path / "source"
    source.write_bytes(b"encrypted")
    beats = []
    transport = runner.Transport(
        url,
        "user",
        "secret",
        progress=lambda: beats.append(time.monotonic()),
        socket_timeout=0.05,
        heartbeat_interval=0.02,
    )
    transport.put("/baidu/file", source)
    assert len(beats) >= 3
    assert files["/dav/baidu/file"] == b"encrypted"
    before = len(beats)
    time.sleep(0.06)
    assert len(beats) == before


@pytest.mark.parametrize("marker", [b"[]", b"null", b'"string"', b"1"])
def test_malformed_remote_marker_sanitized(dav, tmp_path, marker):
    runner = load()
    url, files, _ = dav
    directory, key = pair(tmp_path)
    files["/dav/baidu/" + directory.name + "/complete.json"] = marker
    files["/dav/baidu/" + directory.name + "/archive.tar.gz.enc"] = (
        directory / "archive.tar.gz.enc"
    ).read_bytes()
    with pytest.raises(runner.CloudError, match="cloud_remote_corrupt"):
        runner.copy_backup(runner.Transport(url, "user", "secret"), "/baidu", directory, key)
