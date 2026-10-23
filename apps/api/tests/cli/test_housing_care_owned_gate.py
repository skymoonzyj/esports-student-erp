from __future__ import annotations

import copy
import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
IMAGE = "sha256:" + "a" * 64
CONTAINER = "b" * 64
NETWORK = "c" * 64
VOLUME = "d" * 64


def gate():
    path = ROOT / "scripts/housing-care-owned-gate.py"
    assert path.is_file(), "owned housing care full-check gate is missing"
    spec = importlib.util.spec_from_file_location("care_owned_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_shell_entry_invokes_owned_helper_once_without_python_or_node_hooks(tmp_path):
    interpreter = tmp_path / "python-wrapper"
    observed_env, observed_args = tmp_path / "environment", tmp_path / "arguments"
    interpreter.write_text(
        "#!/bin/sh\n"
        f"/usr/bin/env > {shlex.quote(str(observed_env))}\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(observed_args))}\n"
    )
    interpreter.chmod(0o700)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run-housing-care-gate.sh")],
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "E2E_PYTHON_BIN": str(interpreter),
            "NODE_OPTIONS": "secret",
            "PYTHONPATH": "/untrusted",
            "DOCKER_HOST": "unix:///local.sock",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert observed_args.read_text().splitlines() == [
        str(ROOT / "scripts/housing-care-owned-gate.py"),
        "--python",
        str(interpreter),
    ]
    child = dict(line.split("=", 1) for line in observed_env.read_text().splitlines())
    assert "PYTHONPATH" not in child and "NODE_OPTIONS" not in child
    assert child["DOCKER_HOST"] == "unix:///local.sock"


def test_launcher_timeout_stops_only_its_new_process_group(tmp_path):
    module = gate()
    run_launcher = getattr(module, "run_launcher", None)
    assert callable(run_launcher), "owned child process-group supervision is missing"
    pid_file = tmp_path / "owned-child.pid"
    with pytest.raises(subprocess.TimeoutExpired):
        run_launcher(
            [
                "/bin/sh",
                "-c",
                f"printf '%s\\n' \"$$\" > {shlex.quote(str(pid_file))}; exec sleep 30",
            ],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout=0.25,
        )
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_launcher_timeout_kills_term_ignoring_descendant_after_leader_exits(tmp_path):
    module = gate()
    child_record = tmp_path / "descendant.json"
    program = (
        "import json,os,signal,time\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f" with open({str(child_record)!r},'w') as stream:\n"
        "  json.dump([os.getpid(),os.getpgrp()],stream)\n"
        " time.sleep(30)\n"
        "else:\n"
        " time.sleep(30)\n"
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            module.run_launcher(
                [sys.executable, "-c", program],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout=1,
            )
        descendant, group = json.loads(child_record.read_text())
        assert descendant != group  # The launcher leader, not the child, owns PGID.
        with pytest.raises(ProcessLookupError):
            os.kill(group, 0)  # The leader has exited, independently of the child.
        deadline = time.monotonic() + 2
        while True:
            state = subprocess.run(
                ["ps", "-o", "state=", "-p", str(descendant)],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            # An orphan zombie has already terminated; init may reap it later.
            if not state or state.startswith("Z"):
                break
            assert time.monotonic() < deadline, "TERM-ignoring descendant survived owned-group cleanup"
            time.sleep(0.05)
    finally:
        if child_record.exists():
            descendant, group = json.loads(child_record.read_text())
            try:
                if os.getpgid(descendant) == group:
                    os.kill(descendant, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("rows", ["123 S\n", "124 Z\n", "malformed\n", ""])
def test_darwin_permission_error_never_hides_live_or_unproven_group(monkeypatch, tmp_path, rows):
    module = gate()
    waited = []

    def wait(*, timeout):
        waited.append(timeout)
        if len(waited) == 1:
            raise subprocess.TimeoutExpired(["owned"], timeout)
        return -15

    def killpg(group, sig):
        assert group == 123
        if sig == signal.SIGKILL:
            raise PermissionError("synthetic boundary refusal")

    def inspect_group(command, **kwargs):
        assert command == ["ps", "-o", "pgid=,stat=", "-g", "123"]
        return subprocess.CompletedProcess(command, 0, rows, "")

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(module, "os", SimpleNamespace(killpg=killpg))
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=lambda seconds: None))
    monkeypatch.setattr(
        module,
        "subprocess",
        SimpleNamespace(
            Popen=lambda *args, **kwargs: SimpleNamespace(pid=123, wait=wait),
            CompletedProcess=subprocess.CompletedProcess,
            run=inspect_group,
        ),
    )
    with pytest.raises(PermissionError):
        module.run_launcher(["owned"], env={}, cwd=tmp_path, timeout=0.25)
    assert waited == [0.25, 3]  # Even refused KILL must reap the reserved leader.


class DockerBoundary:
    """External engine boundary only; the actual gate/ledger/launcher dispatch run."""

    def __init__(self, *, child_status=0, drift=None):
        self.calls = []
        self.owner = None
        self.child_status = child_status
        self.drift = drift
        self.started = False
        self.container_removed = False
        self.network_removed = False
        self.child_environments = []

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append(command)
        output, status = "", 0
        if command[:3] == ["docker", "context", "inspect"]:
            output = "unix:///synthetic/local.sock\n"
        elif command[:3] == ["docker", "image", "inspect"]:
            output = IMAGE + "\n"
        elif command[:3] == ["docker", "network", "create"]:
            self.owner = command[command.index("--label") + 1].split("=", 1)[1]
            assert "--internal" not in command
            output = NETWORK + "\n"
        elif command[:3] == ["docker", "network", "inspect"]:
            members = {} if self.container_removed or not self.started else {CONTAINER: {}}
            output = json.dumps(
                [
                    {
                        "Id": NETWORK,
                        "Internal": False,
                        "Driver": "bridge",
                        "Labels": {"erp.assisted.restore.owner": self.owner},
                        "Containers": members,
                    }
                ]
            )
        elif command[:2] == ["docker", "create"]:
            assert "--pull=never" in command
            assert command[command.index("--publish") + 1] == "127.0.0.1::5432"
            assert command[command.index("--network") + 1] == NETWORK
            assert command[-1] == IMAGE
            assert not any(
                value in command for value in ("-v", "--volume", "--mount", "--volumes-from")
            )
            output = CONTAINER + "\n"
        elif command[:2] == ["docker", "inspect"]:
            detail = {
                "Id": CONTAINER,
                "Image": IMAGE,
                "Config": {"Labels": {"erp.assisted.restore.owner": self.owner}},
                "State": {"Running": self.started},
                "HostConfig": {
                    "NetworkMode": NETWORK,
                    "Binds": None,
                    "PortBindings": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]},
                    "PublishAllPorts": False,
                },
                "NetworkSettings": {
                    "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "43261"}]}
                    if self.started
                    else {},
                    "Networks": {"owned": {"NetworkID": NETWORK if self.started else ""}},
                },
                "Mounts": [{"Type": "volume", "Name": VOLUME}],
            }
            if self.started:
                if self.drift == "label":
                    detail["Config"]["Labels"] = {}
                elif self.drift == "volume":
                    detail["Mounts"][0]["Name"] = "unrelated-shared-volume"
                elif self.drift == "binding":
                    detail["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"
                elif self.drift == "network":
                    detail["HostConfig"]["NetworkMode"] = "bridge"
                elif self.drift == "attached_network":
                    detail["NetworkSettings"]["Networks"]["other"] = {"NetworkID": "e" * 64}
                elif self.drift == "unpublished":
                    # Observed on the first real internal-network run: the
                    # requested loopback binding existed, but no port published.
                    detail["NetworkSettings"]["Ports"] = {"5432/tcp": None}
            output = json.dumps([detail])
        elif command[:2] == ["docker", "start"]:
            assert command[2] == CONTAINER
            self.started = True
        elif command[:2] == ["docker", "exec"]:
            assert command[2] == CONTAINER
            assert "pg_isready" in command
        elif command[:2] == ["docker", "rm"]:
            assert command == ["docker", "rm", "--force", "--volumes", CONTAINER]
            self.container_removed = True
        elif command[:3] == ["docker", "network", "rm"]:
            assert command[3] == NETWORK and self.container_removed
            self.network_removed = True
        elif command == ["bash", str(ROOT / "scripts/run-housing-care-e2e.sh")]:
            env = copy.deepcopy(kwargs["env"])
            self.child_environments.append(env)
            ledger = Path(env["E2E_HOUSING_CARE_RUNTIME_LEDGER"])
            owned = json.loads(ledger.read_text())
            assert ledger.stat().st_mode & 0o777 == 0o600
            assert ledger.parent.stat().st_mode & 0o777 == 0o700
            assert owned["container_id"] == CONTAINER and owned["volume"] == VOLUME
            assert owned["task_label"] == "housing-care-20261023"
            assert env["TEST_DATABASE_URL"] == owned["test_database_url"]
            assert "127.0.0.1:43261/erp_housing_care_test" in env["TEST_DATABASE_URL"]
            status = self.child_status
        else:
            pytest.fail("unexpected external boundary command: " + command[0])
        encoded = output if kwargs.get("text") else output.encode()
        if kwargs.get("check") and status:
            raise subprocess.CalledProcessError(status, command, output=encoded, stderr=b"")
        return subprocess.CompletedProcess(
            command, status, encoded, "" if kwargs.get("text") else b""
        )


@pytest.mark.parametrize("child_status", [0, 87])
def test_gate_runs_existing_launcher_once_on_own_db_and_bubbles_failure_after_owned_cleanup(
    tmp_path, child_status
):
    boundary = DockerBoundary(child_status=child_status)
    module = gate()
    result = module.run_gate(
        ROOT,
        sys.executable,
        IMAGE,
        {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "DOCKER_HOST": "unix:///synthetic/local.sock",
            "TEST_DATABASE_URL": "postgresql://production:secret@remote/erp",
            "DATABASE_URL": "secret",
            "E2E_HOUSING_CARE_RUNTIME_LEDGER": "/untrusted/ledger",
            "E2E_API_PORT": "5432",
            "FIELD_ENCRYPTION_MASTER_KEY": "production-secret",
            "NODE_OPTIONS": "production-secret",
        },
        process=boundary,
        launcher=boundary,
        scratch_parent=tmp_path,
    )
    assert result["exit_code"] == child_status
    assert result["owned_containers_removed"] == 1
    assert result["owned_containers_remaining"] == 0
    assert result["private_scratch_removed"] is True
    assert boundary.network_removed
    assert len(boundary.child_environments) == 1
    child = boundary.child_environments[0]
    assert child["APP_ENV"] == "test"
    assert child["E2E_PYTHON_BIN"] == sys.executable
    assert child["E2E_API_PORT"] == "18144" and child["E2E_WEB_PORT"] == "15144"
    assert "DATABASE_URL" not in child and "NODE_OPTIONS" not in child
    assert child["FIELD_ENCRYPTION_MASTER_KEY"] != "production-secret"
    assert not any("32786" in str(command) or "55433" in str(command) for command in boundary.calls)


@pytest.mark.parametrize(
    "drift", ["label", "volume", "binding", "network", "attached_network", "unpublished"]
)
def test_gate_preserves_unknown_resources_and_private_evidence_when_ownership_drifts(
    tmp_path, drift
):
    boundary = DockerBoundary(drift=drift)
    result = gate().run_gate(
        ROOT,
        sys.executable,
        IMAGE,
        {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "DOCKER_HOST": "unix:///synthetic/local.sock",
        },
        process=boundary,
        launcher=boundary,
        scratch_parent=tmp_path,
    )
    assert result["exit_code"] != 0
    assert result["cleanup_failed"] is True
    assert result["private_scratch_removed"] is False
    assert not boundary.child_environments
    assert not boundary.container_removed and not boundary.network_removed


@pytest.mark.parametrize(
    "environment",
    [
        {"DOCKER_HOST": "ssh://remote.invalid"},
        {"DOCKER_HOST": "tcp://127.0.0.1:2375"},
    ],
)
def test_gate_refuses_remote_before_resource_creation(tmp_path, environment):
    boundary = DockerBoundary()
    result = gate().run_gate(
        ROOT,
        sys.executable,
        IMAGE,
        environment,
        process=boundary,
        launcher=boundary,
        scratch_parent=tmp_path,
    )
    assert result["exit_code"] != 0
    assert not boundary.calls
    assert not list(tmp_path.iterdir())
