"""Installer boundaries: real generated wrappers and isolated crontab executable."""

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def installed(tmp_path):
    app = tmp_path / "erp"
    release = app / "releases" / "one"
    (release / "scripts").mkdir(parents=True)
    (app / "current").symlink_to(release, target_is_directory=True)
    (app / ".active-release").write_text(str(release))
    (app / ".deployment-state").write_text(f"status=healthy\nrelease={release}\n")
    record = tmp_path / "runner.json"
    (release / "scripts" / "erp-cloud-backup.py").write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    cron = tmp_path / "crontab.txt"
    cron.write_text("MAILTO=operator\n15 4 * * * other-job\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "crontab"
    fake.write_text(
        "#!/usr/bin/env python3\nimport pathlib, sys\n"
        f"p=pathlib.Path({str(cron)!r})\n"
        "print(p.read_text(), end='') if sys.argv[1]=='-l' else p.write_text(sys.stdin.read())\n"
    )
    fake.chmod(0o700)
    env = {**os.environ, "TZ": "Asia/Shanghai", "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    args = [sys.executable, str(ROOT / "scripts/erp-backup-install.py"),
            "--app-dir", str(app), "--backup-dir", str(tmp_path / "backups"),
            "--key-file", str(tmp_path / "recovery.key"),
            "--status-file", str(tmp_path / "status.json")]
    return app, release, record, cron, env, args


def test_install_keeps_daily_and_unrelated_jobs_and_preserves_connector(installed):
    app, _, _, cron, env, args = installed
    runtime = app / "backup-tools"
    runtime.mkdir()
    (runtime / "config.json").write_text(json.dumps({"secondary_dir": "/separate", "connector_url": "http://127.0.0.1:6000"}))
    subprocess.run(args, env=env, check=True, capture_output=True)
    first = cron.read_text()
    assert "MAILTO=operator\n15 4 * * * other-job\n" in first
    assert first.count("0 3 * * * ") == 1
    assert first.count("*/2 * * * * ") == 1
    subprocess.run(args, env=env, check=True, capture_output=True)
    assert cron.read_text() == first
    config = json.loads((runtime / "config.json").read_text())
    assert config["secondary_dir"] == "/separate"
    assert config["connector_url"] == "http://127.0.0.1:6000"
    assert (runtime / "config.json").stat().st_mode & 0o777 == 0o600


def test_cloud_wrapper_uses_authoritative_release_and_config(installed):
    app, _, record, _, env, args = installed
    subprocess.run(args, env=env, check=True, capture_output=True)
    wrapper = app / "backup-tools/run-cloud.py"
    result = subprocess.run([sys.executable, str(wrapper)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(record.read_text()) == ["--config", str(app / "backup-tools/config.json")]
    assert json.loads((app / "backup-tools/config.json").read_text())["connector_url"] == "http://127.0.0.1:5244"


@pytest.mark.parametrize("state", ["status=deploying\n", "status=healthy\nrelease=/wrong\n"])
def test_cloud_wrapper_blocks_unverified_deployment(installed, state):
    app, _, record, _, env, args = installed
    subprocess.run(args, env=env, check=True, capture_output=True)
    assert (app / "backup-tools/run-cloud.py").is_file()
    (app / ".deployment-state").write_text(state)
    result = subprocess.run([sys.executable, str(app / "backup-tools/run-cloud.py")], capture_output=True, check=False)
    assert result.returncode != 0
    assert not record.exists()


def test_cloud_wrapper_blocks_release_outside_app(installed, tmp_path):
    app, _, record, _, env, args = installed
    subprocess.run(args, env=env, check=True, capture_output=True)
    (app / ".active-release").write_text(str(tmp_path))
    result = subprocess.run([sys.executable, str(app / "backup-tools/run-cloud.py")], capture_output=True, check=False)
    assert result.returncode != 0
    assert not record.exists()


def test_cloud_wrapper_blocks_exclusive_deploy_lock(installed):
    app, _, record, _, env, args = installed
    subprocess.run(args, env=env, check=True, capture_output=True)
    with (app / ".deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = subprocess.run([sys.executable, str(app / "backup-tools/run-cloud.py")], capture_output=True, check=False)
    assert result.returncode != 0
    assert not record.exists()


def test_daily_wrapper_does_not_pass_cloud_or_unrelated_settings(installed):
    app, release, record, _, env, args = installed
    (release / "scripts/erp-backup.py").write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    subprocess.run(args, env=env, check=True, capture_output=True)
    result = subprocess.run([sys.executable, str(app / "backup-tools/run.py")], capture_output=True, check=False)
    assert result.returncode == 0
    argv = json.loads(record.read_text())
    assert argv[0] == "backup"
    assert "--connector-url" not in argv
    assert "--key-file" in argv
