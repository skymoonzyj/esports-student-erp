from __future__ import annotations

import json
import shutil
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.operations.backup_messages import backup_message
from app.operations.schemas import BackupSecondaryStatus, OperationsBackupStatus


@dataclass(frozen=True)
class AlertCondition:
    key: str
    severity: Literal["warning", "critical"]
    title: str
    detail: str


def _date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def read_backup_status(path: Path) -> OperationsBackupStatus:
    """Only expose explicitly allowed fields; no filenames, exception text or secrets."""
    try:
        with path.open("rb") as handle:
            payload = handle.read(131_073)
        if len(payload) > 131_072:
            raise ValueError("status too large")
        raw = json.loads(payload)
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("invalid status schema")
        status = raw.get("status")
        if status not in {"running", "ok", "failed"}:
            raise ValueError("invalid status")
        secondary = raw.get("secondary_copy")
        secondary = secondary if isinstance(secondary, dict) else {}
        secondary_status = secondary.get("status")
        recovery = raw.get("recovery")
        recovery = recovery if isinstance(recovery, dict) else {}
        size = raw.get("size_bytes")
        error_code, reason = backup_message(status, raw.get("error_code"), raw.get("stage"))
        return OperationsBackupStatus(
            status=status,
            completed_at=_date(raw.get("completed_at")),
            started_at=_date(raw.get("started_at")),
            last_success_at=_date(raw.get("last_success_at")),
            verification_at=_date(raw.get("verification_at")),
            verification_status="verified" if recovery.get("verified") is True else "unverified",
            size_bytes=size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
            error_code=error_code,
            reason=reason,
            secondary_copy=BackupSecondaryStatus(
                configured=secondary.get("configured") is True,
                status=secondary_status if secondary_status in {"not_configured", "ok", "failed"} else "unknown",
                last_success_at=_date(secondary.get("last_success_at")),
            ),
        )
    except (OSError, ValueError, TypeError):
        return OperationsBackupStatus(
            error_code="backup_status_unavailable", reason="尚未取得有效备份状态，请检查备份任务和状态文件挂载"
        )


def backup_conditions(status: OperationsBackupStatus, *, now: datetime) -> list[AlertCondition]:
    result: list[AlertCondition] = []
    if status.status == "failed":
        result.append(AlertCondition("backup_failed", "critical", "自动备份失败", status.reason))
    if status.status == "unknown":
        result.append(AlertCondition("backup_unavailable", "warning", "备份状态不可用", status.reason))
    elif status.last_success_at is None or now - status.last_success_at > timedelta(hours=36):
        result.append(AlertCondition("backup_stale", "critical", "备份未按时完成", "超过36小时没有成功备份，或尚无成功记录，请检查备份计划。"))
    if status.status == "running" and (
        status.started_at is None or now - status.started_at > timedelta(hours=2)
    ):
        result.append(AlertCondition("backup_running_stale", "warning", "备份执行时间异常", "备份持续超过2小时，或无法确认启动时间，请检查备份任务。"))
    if status.status != "unknown":
        if (status.verification_status != "verified" or status.verification_at is None
                or now - status.verification_at > timedelta(days=8)):
            result.append(AlertCondition("backup_restore_unverified", "warning", "备份恢复验证待完成", "近8天没有成功的恢复验证记录，请运行隔离恢复验证。"))
        if not status.secondary_copy.configured:
            result.append(AlertCondition("backup_secondary_missing", "warning", "尚未配置独立备份副本", "当前备份未配置独立设备副本，主机或磁盘损坏时可能无法恢复。"))
        elif status.secondary_copy.status != "ok" or (
            status.secondary_copy.last_success_at is None
            or now - status.secondary_copy.last_success_at > timedelta(hours=36)
        ):
            result.append(AlertCondition("backup_secondary_failed", "critical", "独立备份副本异常", "独立副本复制失败或超过36小时没有成功记录，请检查备份设备。"))
    return result


def disk_conditions(
    path: Path, *, key: str = "backup_disk", label: str = "备份目录所在磁盘",
) -> list[AlertCondition]:
    try:
        usage = shutil.disk_usage(path)
        percent = usage.used / usage.total * 100
        if percent >= 80:
            return [AlertCondition(
                f"{key}_usage", "critical" if percent >= 90 else "warning", f"{label}空间不足",
                f"{label}已使用{percent:.1f}%，请清理可删除文件或扩容。",
            )]
        return []
    except (OSError, ZeroDivisionError):
        return [AlertCondition(f"{key}_unavailable", "warning", f"{label}状态不可用", f"无法读取{label}的容量，请检查目录挂载。")]


def certificate_conditions(host: str, port: int, *, now: datetime) -> list[AlertCondition]:
    if not host:
        return []
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=5) as raw, context.wrap_socket(
            raw, server_hostname=host
        ) as connection:
            certificate = connection.getpeercert()
        not_after = certificate.get("notAfter") if certificate else None
        if not isinstance(not_after, str):
            raise TypeError("no expiry")
        expires_at = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=UTC)
        days = (expires_at - now).total_seconds() / 86400
        if days <= 30:
            return [AlertCondition(
                "certificate_expiry", "critical" if days <= 7 else "warning", "HTTPS证书即将到期",
                f"当前站点证书将在{max(0, int(days))}天后到期，请检查自动续期任务。",
            )]
        return []
    except ssl.SSLCertVerificationError:
        return [AlertCondition("certificate_invalid", "critical", "HTTPS证书校验失败", "证书可能已过期、域名不匹配或证书链不受信任，请检查HTTPS配置。")]
    except (OSError, ValueError, TypeError):
        return [AlertCondition("certificate_unavailable", "warning", "HTTPS证书检查失败", "服务器无法连接配置的HTTPS地址，请检查域名解析、网络和站点服务。")]
