"""Portable authorization installation, using real wrappers and isolated cron."""

import importlib.util
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

_fixture_spec = importlib.util.spec_from_file_location(
    "cloud_install_test_fixture", Path(__file__).with_name("test_erp_cloud_install.py")
)
_fixture_module = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_fixture_module)


@pytest.fixture
def installed(tmp_path):
    return _fixture_module.installed.__wrapped__(tmp_path)


def test_documented_clean_recovery_kit_extract_bootstrap(tmp_path):
    root = Path(__file__).resolve().parents[4]
    kit = tmp_path / "clean-host"
    kit.mkdir()
    instructions = (root / "docs/operations/encrypted-backup-recovery.md").read_text()
    listing = instructions.split("<!-- ERP_RECOVERY_KIT_BEGIN -->", 1)[1].split(
        "<!-- ERP_RECOVERY_KIT_END -->", 1
    )[0]
    files = re.findall(r"`((?:scripts|apps)/[^`]+)`", listing)
    assert set(files) == {
        "scripts/erp-backup.py", "scripts/erp-backup-install.py",
        "scripts/erp_backup_container.py", "scripts/cloud_auth/__init__.py",
        "scripts/cloud_auth/recovery.py", "scripts/web_assets.py",
        "apps/web/scripts/web-assets.schema.json",
    }
    for relative in files:
        target = kit / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    # No inherited PYTHONPATH or repo scripts path; execute the shipped CLI.
    result = subprocess.run(
        [sys.executable, "-I", str(kit / "scripts/erp-backup.py"), "extract", "--help"],
        cwd=kit,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--recovery-dir" in result.stdout


def test_pending_install_adds_auth_without_installing_connector(installed, monkeypatch):
    app, release, record, cron, env, args = installed
    root = Path(__file__).resolve().parents[4]
    shutil.copyfile(
        root / "scripts/erp-cloud-authorize.py", release / "scripts/erp-cloud-authorize.py"
    )
    sys.path.insert(0, str(root / "scripts"))
    subprocess.run(args, env=env, check=True, capture_output=True)
    assert cron.read_text().count("* * * * * /usr/bin/python3 ") == 1
    assert not (app / "cloud-connector/registry.json").exists()
    wrapper = app / "backup-tools/run-cloud-auth.py"
    assert wrapper.stat().st_mode & 0o777 == 0o700

    def launch(command, **kwargs):
        # Load shipped source at the exact generated entrypoint, execute real CLI
        # parsing and the actual pending run-loop; only child-process/time bounds
        # are substituted. No invented runner file or provider/backend calls.
        spec = importlib.util.spec_from_file_location("real_authorization_cli", command[1])
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        times = iter([0, 0, 60])
        runner.time = SimpleNamespace(monotonic=lambda: next(times, 60), sleep=lambda _: None)
        monkeypatch.setattr(sys, "argv", command[1:])
        return runner.main()

    monkeypatch.setattr(subprocess, "call", launch)
    # The real wrapper narrows PATH in its child process. runpy shares this
    # test process, so register the original value for automatic restoration.
    monkeypatch.setenv("PATH", os.environ["PATH"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(wrapper), run_name="__main__")
    assert stopped.value.code == 0
    assert not record.exists()
    assert (
        json.loads((app / "backup-tools/config.json").read_text())["connector_management"]
        == "pending"
    )


def test_generated_auth_wrapper_drains_late_operation_after_sixty_seconds(installed, monkeypatch):
    app, release, _, _, env, args = installed
    root = Path(__file__).resolve().parents[4]
    shutil.copyfile(
        root / "scripts/erp-cloud-authorize.py", release / "scripts/erp-cloud-authorize.py"
    )
    sys.path.insert(0, str(root / "scripts"))
    connector = app / "cloud-connector"
    (connector / "data").mkdir(parents=True, mode=0o700)
    (connector / "admin-secret").write_text("private-fixture-password")
    (connector / "admin-secret").chmod(0o600)
    (connector / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "managed": True,
                "connector_id": str(uuid4()),
                "container_name": "erp-cloud-connector",
                "image": "openlistteam/openlist:v4.2.6",
                "image_id": "sha256:" + "a" * 64,
                "origin": "http://127.0.0.1:5244",
                "data_dir": str(connector / "data"),
                "admin_secret_file": str(connector / "admin-secret"),
                "objects": [],
            }
        )
    )
    (connector / "registry.json").chmod(0o600)
    subprocess.run(args, env=env, check=True, capture_output=True)
    events = []

    def launch(command, **kwargs):
        spec = importlib.util.spec_from_file_location("draining_authorization_cli", command[1])
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        elapsed = [0]
        runner.time = SimpleNamespace(monotonic=lambda: elapsed[0], sleep=lambda _: None)

        def call(config, action, **payload):
            events.append(action)
            if action == "heartbeat":
                elapsed[0] = 54
            if action == "claim":
                return {"work": {"action": "poll", "fence": {}}}
            if action == "complete":
                assert payload["result"]["state"] == "pending"
            return {"ok": True}

        def provider_operation(config, work, call):
            assert elapsed[0] == 54
            elapsed[0] = 72
            # Model subprocess.call's watchdog at the real child-process boundary.
            if kwargs.get("timeout", float("inf")) < elapsed[0]:
                raise SystemExit("child killed before checkpoint")
            events.append("operation_finished")
            return {"action": "poll", "state": "pending"}

        runner.perform = provider_operation
        actual_run = runner.run
        runner.run = lambda config: actual_run(config, call=call)
        monkeypatch.setattr(sys, "argv", command[1:])
        return runner.main()

    monkeypatch.setattr(subprocess, "call", launch)
    monkeypatch.setenv("PATH", os.environ["PATH"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(app / "backup-tools/run-cloud-auth.py"), run_name="__main__")
    assert stopped.value.code == 0
    assert events.count("claim") == 1
    assert events.count("operation_finished") == events.count("complete") == 1
    assert events.index("operation_finished") < events.index("complete")


def test_bad_cron_does_not_replace_existing_configuration(installed):
    app, _, _, cron, env, args = installed
    runtime = app / "backup-tools"
    runtime.mkdir()
    config = runtime / "config.json"
    config.write_text('{"connector_url":"http://127.0.0.1:6000"}')
    cron.write_text("# BEGIN ESPORTS ERP CLOUD AUTH\nunterminated\n")
    old = config.read_bytes()
    result = subprocess.run(args, env=env, capture_output=True, check=False)
    assert result.returncode != 0
    assert config.read_bytes() == old


def test_cron_write_failure_preserves_old_config_and_wrappers(installed):
    app, _, _, cron, env, args = installed
    subprocess.run(args, env=env, check=True, capture_output=True)
    runtime = app / "backup-tools"
    before = {p.name: p.read_bytes() for p in runtime.iterdir() if p.is_file()}
    old_cron = cron.read_bytes()
    fake = Path(env["PATH"].split(":")[0]) / "crontab"
    fake.write_text(fake.read_text().replace("p.write_text(sys.stdin.read())", "sys.exit(9)"))
    result = subprocess.run(
        [*args, "--connector-url", "http://127.0.0.1:6000"],
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert {p.name: p.read_bytes() for p in runtime.iterdir() if p.is_file()} == before
    assert cron.read_bytes() == old_cron


def test_connector_installer_pending_does_not_claim_existing_external_instance(tmp_path):
    root = Path(__file__).resolve().parents[4]
    path = root / "scripts/erp-cloud-connector-install.py"
    assert path.exists(), "portable connector installer missing"
    app = tmp_path / "app"
    app.mkdir()
    result = subprocess.run(
        [sys.executable, str(path), "--app-dir", str(app), "--mode", "pending"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "pending" in result.stdout
    assert not (app / "cloud-connector").exists()


def test_create_failure_preserves_unowned_container_and_existing_data(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "connector_install", root / "scripts/erp-cloud-connector-install.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = tmp_path / "app"
    (app / "cloud-connector/data").mkdir(parents=True)
    marker = app / "cloud-connector/data/existing.db"
    marker.write_text("do not overwrite")
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b"[{}]", stderr=b"")

    monkeypatch.setattr(mod.subprocess, "run", command)
    with pytest.raises(mod.RecoveryError):
        mod.install(app, mode="create", connector_id=str(uuid4()))
    assert marker.read_text() == "do not overwrite"
    assert not any("rm" in args or "run" in args for args in calls)


@pytest.mark.parametrize(
    "message,absent",
    [
        ("error: no such object: erp-cloud-connector", True),
        ("Error response from daemon: No such container: erp-cloud-connector", True),
        ("error: no such object: another-container", False),
        ("permission denied: no such object: erp-cloud-connector", False),
        ("Cannot connect to Docker daemon", False),
    ],
)
def test_create_absence_accepts_only_named_daemon_response(tmp_path, monkeypatch, message, absent):
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "connector_install", root / "scripts/erp-cloud-connector-install.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = tmp_path / "app"
    app.mkdir()
    calls = []

    def docker(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=b'[{"Config":{"Labels":{}}}]', stderr=b""
            )
        return subprocess.CompletedProcess(command, 1, stdout=b"[]", stderr=message.encode())

    monkeypatch.setattr(mod.subprocess, "run", docker)
    expected = "image_version" if absent else "existing_container"
    with pytest.raises(mod.RecoveryError, match=expected):
        mod.install(app, mode="create", project="erp", connector_id=str(uuid4()))
    assert any(command[1:3] == ["image", "inspect"] for command in calls) is absent
    assert not (app / "cloud-connector").exists()


def test_managed_immutable_image_requires_fixed_version(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "connector_install", root / "scripts/erp-cloud-connector-install.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = tmp_path / "app"
    data = app / "cloud-connector/data"
    data.mkdir(parents=True, mode=0o700)
    secret = data.parent / "admin-secret"
    secret.write_text("fixture-only-password")
    secret.chmod(0o600)
    identifier, image = str(uuid4()), "sha256:" + "c" * 64
    details = {
        "Image": image,
        "Config": {
            "Image": image,
            "Labels": {"erp.cloud.connector": identifier, "erp.cloud.dedicated": "true"},
        },
        "Mounts": [{"Type": "bind", "Source": str(data), "Destination": "/opt/openlist/data"}],
        "HostConfig": {"PortBindings": {"5244/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5244"}]}},
    }

    def docker(command, **kwargs):
        value = (
            [{"Config": {"Labels": {"org.opencontainers.image.version": "v7.0.0"}}}]
            if command[1:3] == ["image", "inspect"]
            else [details]
        )
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(value).encode(), stderr=b""
        )

    monkeypatch.setattr(mod.subprocess, "run", docker)
    with pytest.raises(mod.RecoveryError, match="version"):
        mod.install(
            app, mode="managed", project="erp", connector_id=identifier, admin_secret_file=secret
        )
    assert not (data.parent / "registry.json").exists()


def test_explicit_create_registers_only_own_instance_without_secret_in_argv(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "connector_install", root / "scripts/erp-cloud-connector-install.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = tmp_path / "app"
    app.mkdir()
    identifier = str(uuid4())
    commands = []
    created = False

    def docker(command, **kwargs):
        nonlocal created
        commands.append(command)
        if "/bin/stat" in command:
            return subprocess.CompletedProcess(command, 0, stdout=b"501:20\n", stderr=b"")
        if "nc" in command:
            body = b'{"code":200,"data":{"token":"fixture"}}'
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body,
                stderr=b"",
            )
        if command[1:3] == ["image", "inspect"]:
            out = [
                {
                    "Id": "sha256:" + "b" * 64,
                    "Config": {"Labels": {"org.opencontainers.image.version": "v4.2.6"}},
                }
            ]
        elif command[1] == "run":
            created = True
            out = []
        elif command[1] == "inspect" and not created:
            return subprocess.CompletedProcess(
                command, 1, stdout=b"[]", stderr=("error: no such object: " + command[-1]).encode()
            )
        elif command[1] == "inspect":
            out = [
                {
                    "Image": "sha256:" + "b" * 64,
                    "State": {"Running": True},
                    "Config": {
                        "Image": "openlistteam/openlist:v4.2.6",
                        "Labels": {
                            "erp.cloud.connector": identifier,
                            "erp.cloud.dedicated": "true",
                        },
                    },
                    "HostConfig": {
                        "PortBindings": {"5244/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5244"}]}
                    },
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": str(app / "cloud-connector/data"),
                            "Destination": "/opt/openlist/data",
                        }
                    ],
                }
            ]
        else:
            out = []
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(out).encode(), stderr=b"")

    monkeypatch.setattr(mod.subprocess, "run", docker)
    result = mod.install(app, mode="create", project="erp", connector_id=identifier)
    registry = json.loads((app / "cloud-connector/registry.json").read_text())
    secret = (app / "cloud-connector/admin-secret").read_text()
    assert result["state"] == "managed"
    assert registry["connector_id"] == identifier
    assert len(secret) >= 32
    assert secret not in json.dumps(commands)
    assert (app / "cloud-connector/data").stat().st_mode & 0o777 == 0o700
    assert (app / "cloud-connector/admin-secret").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
@pytest.mark.parametrize(
    "identity", ["owned", "foreign_label", "foreign_mount", "foreign_image", "absent"]
)
def test_uncertain_create_cleans_only_exact_attempt_and_private_env(
    tmp_path, monkeypatch, failure, identity
):
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "connector_install", root / "scripts/erp-cloud-connector-install.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = tmp_path / "app"
    app.mkdir()
    identifier = str(uuid4())
    created, removed = [], []
    attempted = False
    image_id = "sha256:" + "c" * 64

    def docker(command, **kwargs):
        nonlocal attempted
        if "/bin/stat" in command:
            return subprocess.CompletedProcess(command, 0, stdout=b"501:20", stderr=b"")
        if command[1:3] == ["image", "inspect"]:
            output = [
                {
                    "Id": image_id,
                    "Config": {"Labels": {"org.opencontainers.image.version": "v4.2.6"}},
                }
            ]
        elif command[1] == "run":
            attempted = True
            labels = {
                command[index + 1].split("=", 1)[0]: command[index + 1].split("=", 1)[1]
                for index, value in enumerate(command)
                if value == "--label"
            }
            if identity == "foreign_label":
                labels["erp.cloud.connector"] = str(uuid4())
            source = (
                str(app / "cloud-connector/data")
                if identity != "foreign_mount"
                else str(tmp_path / "foreign")
            )
            created.append(
                {
                    "Name": "/erp-cloud-connector",
                    "Image": image_id if identity != "foreign_image" else "sha256:" + "d" * 64,
                    "Config": {"Labels": labels},
                    "Mounts": [
                        {"Type": "bind", "Source": source, "Destination": "/opt/openlist/data"}
                    ],
                }
            )
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 30)
            raise subprocess.CalledProcessError(1, command, stderr=b"startup failed")
        elif command[1] == "inspect":
            if not attempted or identity == "absent":
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(
                        1, command, stderr=("error: no such object: " + command[-1]).encode()
                    )
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout=b"[]",
                    stderr=("error: no such object: " + command[-1]).encode(),
                )
            output = created
        elif command[1] == "rm":
            removed.extend(created)
            created.clear()
            output = []
        else:
            pytest.fail("unexpected Docker boundary")
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(output).encode(), stderr=b""
        )

    monkeypatch.setattr(mod.subprocess, "run", docker)
    with pytest.raises((mod.RecoveryError, subprocess.SubprocessError)):
        mod.install(app, mode="create", project="erp", connector_id=identifier)
    assert bool(removed) is (identity == "owned")
    assert not (app / "cloud-connector/.bootstrap-env").exists()
    assert not (app / "cloud-connector/registry.json").exists()
