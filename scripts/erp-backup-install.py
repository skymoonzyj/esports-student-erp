#!/usr/bin/env python3
"""Install one managed daily backup crontab entry, preserving unrelated jobs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

BEGIN = "# BEGIN ESPORTS ERP ENCRYPTED BACKUP"
END = "# END ESPORTS ERP ENCRYPTED BACKUP"


def update_crontab(existing, line):
    result = []
    inside = False
    for entry in existing.splitlines():
        if entry == BEGIN:
            if inside:
                raise ValueError("invalid managed cron block")
            inside = True
        elif entry == END:
            if not inside:
                raise ValueError("invalid managed cron block")
            inside = False
        elif not inside:
            result.append(entry)
    if inside:
        raise ValueError("unterminated managed cron block")
    return "\n".join(result + [BEGIN, line, END, ""])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--project", default="esports-erp-staging")
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--secondary-dir", type=Path)
    parser.add_argument("--clear-secondary", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", args.project):
        raise SystemExit("Invalid compose project name")
    # fnOS currently uses China time. Do not silently install a 03:00 job in
    # another timezone; CRON_TZ is not supported by every cron implementation.
    time.tzset()
    if time.localtime().tm_gmtoff != 8 * 3600:
        raise SystemExit(
            "Host timezone must use UTC+08:00 before installing the 03:00 backup job"
        )
    app_dir = args.app_dir.resolve(strict=True)
    runtime = app_dir / "backup-tools"
    runtime.mkdir(exist_ok=True, mode=0o700)
    wrapper = runtime / "run.py"
    configuration_file = runtime / "config.json"
    previous = (
        json.loads(configuration_file.read_text())
        if configuration_file.is_file()
        else {}
    )
    configuration = {
        "app_dir": str(app_dir),
        "project": args.project,
        "backup_dir": str(args.backup_dir.resolve()),
        "key_file": str(args.key_file.resolve()),
        "status_file": str(args.status_file.resolve()),
        "secondary_dir": str(args.secondary_dir.resolve())
        if args.secondary_dir
        else (None if args.clear_secondary else previous.get("secondary_dir")),
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=runtime)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(configuration, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, configuration_file)
    finally:
        Path(temporary).unlink(missing_ok=True)
    # The generated wrapper contains only paths/settings, never recovery secrets.
    script = """#!/usr/bin/env python3
import fcntl, json, os, pathlib, subprocess
configuration = json.loads(pathlib.Path(CONFIGURATION_FILE).read_text())
app = pathlib.Path(configuration["app_dir"])
lock = (app / ".deploy.lock").open("a")
try:
    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("ERP deployment is in progress; automatic backup is blocked")
marker = app / ".active-release"
state_file = app / ".deployment-state"
if state_file.exists():
    state = dict(line.split("=", 1) for line in state_file.read_text().splitlines() if "=" in line)
    eligible = state.get("status") in ("healthy", "complete", "backup_failed") or (
        state.get("status") == "attempt_failed" and state.get("active_unchanged") == "true"
    )
    if not eligible or not marker.is_file():
        raise SystemExit("ERP deployment is not verified; automatic backup is blocked")
release = pathlib.Path(marker.read_text().strip()) if marker.exists() else app / "current"
release = release.resolve(strict=True)
if not release.is_relative_to(app):
    raise SystemExit("Invalid active ERP release")
if state_file.exists() and (release != (app / "current").resolve(strict=True) or state.get("release") != str(release)):
    raise SystemExit("ERP active release and deployment state disagree; automatic backup is blocked")
command = ["python3", str(release / "scripts/erp-backup.py"), "backup"]
for name, value in configuration.items():
    if value is not None:
        command.extend(["--" + name.replace("_", "-"), value])
os.environ["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
raise SystemExit(subprocess.call(command))
""".replace("CONFIGURATION_FILE", repr(str(configuration_file)))
    descriptor, temporary = tempfile.mkstemp(prefix=".run-", dir=runtime)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(script)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o700)
        os.replace(temporary, wrapper)
    finally:
        Path(temporary).unlink(missing_ok=True)
    current = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=False
    )
    if current.returncode not in (0, 1) or (
        current.returncode == 1 and "no crontab" not in current.stderr.lower()
    ):
        raise SystemExit("Unable to read existing crontab; left unchanged")
    line = (
        "0 3 * * * /usr/bin/python3 "
        + shlex.quote(str(wrapper))
        + " >> "
        + shlex.quote(str(runtime / "backup.log"))
        + " 2>&1"
    )
    # A percent sign in cron commands is interpreted even inside shell quotes.
    if "%" in line or "\n" in line:
        raise SystemExit("Unsupported characters in scheduled backup paths")
    replacement = update_crontab(current.stdout, line)
    subprocess.run(
        ["crontab", "-"], input=replacement, text=True, check=True, capture_output=True
    )
    print(
        "Daily encrypted backup and isolated restore verification installed at 03:00 China time."
    )


if __name__ == "__main__":
    main()
