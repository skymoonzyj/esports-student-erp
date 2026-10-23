from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.operations.probes import backup_conditions, read_backup_status


def test_backup_status_allowlist_never_exposes_raw_errors_paths_or_secrets(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    now = datetime.now(UTC).isoformat()
    path.write_text(json.dumps({
        "schema_version": 1, "status": "failed", "last_success_at": now,
        "error_code": "sensitive-password-in-exception", "archive_path": "/secret/archive",
        "secondary_copy": {"configured": True, "status": "ok", "last_success_at": now},
        "recovery": {"verified": True}, "verification_at": now,
    }))
    status = read_backup_status(path)
    assert status.error_code == "backup_failed"
    assert "sensitive" not in status.model_dump_json()
    assert "/secret" not in status.model_dump_json()
    assert status.verification_status == "verified"
    assert [condition.key for condition in backup_conditions(status, now=datetime.now(UTC))] == ["backup_failed"]


def test_stale_backup_and_missing_recovery_copy_report_separate_conditions(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    path = tmp_path / "status.json"
    path.write_text(json.dumps({
        "schema_version": 1, "status": "ok", "last_success_at": (now - timedelta(days=2)).isoformat(),
    }))
    status = read_backup_status(path)
    assert {condition.key for condition in backup_conditions(status, now=now)} == {
        "backup_stale", "backup_restore_unverified", "backup_secondary_missing",
    }


def test_unreadable_malformed_oversized_or_unknown_backup_status_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    assert read_backup_status(path).status == "unknown"
    for payload in ("{", "[]", '{"schema_version":2}', "x" * 131_073):
        path.write_text(payload)
        assert read_backup_status(path).status == "unknown"


@pytest.mark.parametrize(("code", "stage", "reason_fragment", "stage_fragment"), [
    ("runtime_image_archive_failed", "save_runtime_images", "导出运行镜像失败", "保存运行镜像"),
    ("mounted_api_secrets_copy_failed", "copy_recovery_configuration", "复制接口服务恢复密钥失败", "复制恢复配置和密钥"),
    ("restore_row_counts_mismatch", "verify_restored_database", "数据行数", "验证恢复后的数据库"),
    ("restore_schema_indexes_mismatch", "verify_restored_database", "数据库索引", "验证恢复后的数据库"),
    ("restored_mail_password_decryption_failed", "verify_restored_database", "邮件服务密码无法解密", "验证恢复后的数据库"),
    ("archive_authentication_failed", "authenticate_encrypted_archive", "完整性或签名校验失败", "校验加密备份完整性"),
    ("insufficient_disk_space", "preflight", "剩余空间不足", "备份前置检查"),
])
def test_known_backup_failure_exposes_safe_code_and_chinese_reason(
    tmp_path: Path, code: str, stage: str, reason_fragment: str, stage_fragment: str,
) -> None:
    path = tmp_path / "status.json"
    path.write_text(json.dumps({
        "schema_version": 1, "status": "failed", "error_code": code, "stage": stage,
        "raw_stderr": "secret-password", "command_return_code": 1,
    }))
    status = read_backup_status(path)
    assert status.error_code == code
    assert reason_fragment in status.reason
    assert stage_fragment in status.reason
    assert "secret-password" not in status.model_dump_json()
    alert = next(item for item in backup_conditions(status, now=datetime.now(UTC))
                 if item.key == "backup_failed")
    assert alert.detail == status.reason


def test_running_backup_uses_chinese_stage_and_unknown_values_stay_private(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text(json.dumps({
        "schema_version": 1, "status": "running", "stage": "restore_isolated_database",
        "error_code": "restore_database_failed",
    }))
    status = read_backup_status(path)
    assert status.error_code is None
    assert status.reason == "正在还原隔离验证数据库"
    for code, stage in (("private-password", "secret-host/path"), (["secret"], {"secret": "value"})):
        path.write_text(json.dumps({
            "schema_version": 1, "status": "failed", "stage": stage, "error_code": code,
        }))
        status = read_backup_status(path)
        assert status.error_code == "backup_failed"
        assert "secret" not in status.reason
        assert "private" not in status.reason
