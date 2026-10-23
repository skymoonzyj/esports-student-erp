import pytest
from pydantic import ValidationError

from app.operations.cloud_schemas import CloudTargetUpdate, validate_backup_id


@pytest.mark.parametrize(
    "path",
    [
        "relative",
        "/a/../b",
        "/a//b",
        "/a?x",
        "/a#x",
        "/a\\b",
        "/a/%2e%2e",
        "/a\x00",
        "/a\u0085b",
        "/a\u202eb",
        "/a/",
        "/" + "a" * 256,
    ],
)
def test_logical_paths_reject_unsafe_input(path):
    with pytest.raises(ValidationError):
        CloudTargetUpdate(remote_path=path, username="operator", password="secret", enabled=False)


def test_unicode_absolute_path_and_write_only_password():
    value = CloudTargetUpdate(
        remote_path="/百度/ERP备份", username="operator", password="secret", enabled=False
    )
    assert value.remote_path == "/百度/ERP备份"
    assert "secret" not in repr(value)


@pytest.mark.parametrize(
    "value",
    [
        "../backup",
        "backup-20261002T030000Z-1234abcd\n",
        "/backup-20261002T030000Z-1234abcd",
        "backup-２０２６１００２T０３００００Z-1234abcd",
    ],
)
def test_backup_identity_rejects_unsafe_input(value):
    with pytest.raises(ValueError):
        validate_backup_id(value)


def test_backup_identity_accepts_completed_archive_identity():
    assert (
        validate_backup_id("backup-20261002T030000Z-1234abcd") == "backup-20261002T030000Z-1234abcd"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "{secret",
        '{"action":"unknown","password":"secret"}',
        '{"action":"sync","backup_id":"../secret"}',
        '{"action":"claim","password":"secret"}',
        "x" * 65537,
    ],
)
def test_bridge_rejects_invalid_stdin_without_echoing_input(payload):
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "app.operations.cloud_bridge"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=dict(os.environ),
        check=False,
    )
    assert result.stdout.strip() == '{"ok":false,"error_code":"cloud_bridge_failed"}'
    assert result.stderr == ""
