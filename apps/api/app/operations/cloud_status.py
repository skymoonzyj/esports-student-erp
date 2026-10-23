"""Combine independent-copy evidence without conflating connection tests with backups."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.operations.cloud_models import CloudBackupTarget
from app.operations.models import OperationsServiceHeartbeat
from app.operations.probes import AlertCondition
from app.operations.schemas import BackupSecondaryStatus, OperationsBackupStatus

PROVIDER_NAMES = {"baidu": "百度网盘", "115": "115 网盘", "quark": "夸克网盘"}


@dataclass(frozen=True)
class CloudCopyEvidence:
    provider: str
    enabled: bool
    last_success_at: datetime | None
    last_error_code: str | None
    authorization_status: str = "manual"


def read_cloud_evidence(db: Session) -> tuple[list[CloudCopyEvidence], datetime | None]:
    # Monitoring has no need to load or decrypt connector credentials.
    rows = db.execute(
        select(
            CloudBackupTarget.provider,
            CloudBackupTarget.enabled,
            CloudBackupTarget.last_success_at,
            CloudBackupTarget.last_error_code,
            CloudBackupTarget.authorization_status,
        )
    )
    evidence = [CloudCopyEvidence(*row) for row in rows]
    last_seen = db.scalar(
        select(OperationsServiceHeartbeat.last_seen_at).where(
            OperationsServiceHeartbeat.service_key == "cloud_backup",
        )
    )
    return evidence, last_seen


def _fresh(value: datetime | None, *, now: datetime) -> bool:
    return value is not None and timedelta(0) <= now - value <= timedelta(hours=36)


def with_cloud_copy(
    status: OperationsBackupStatus,
    targets: Sequence[CloudCopyEvidence],
    *,
    now: datetime,
) -> OperationsBackupStatus:
    enabled = [
        target
        for target in targets
        if target.enabled and target.authorization_status != "reauthorization_required"
    ]
    if not enabled:
        return status
    secondary = status.secondary_copy
    successes = [
        target.last_success_at
        for target in enabled
        if not target.last_error_code
        and _fresh(target.last_success_at, now=now)
        and target.last_success_at is not None
    ]
    if (
        secondary.configured
        and secondary.status == "ok"
        and _fresh(secondary.last_success_at, now=now)
    ):
        assert secondary.last_success_at is not None
        successes.append(secondary.last_success_at)
    if successes:
        merged = BackupSecondaryStatus(configured=True, status="ok", last_success_at=max(successes))
    else:
        previous = [
            target.last_success_at
            for target in enabled
            if target.last_success_at is not None and target.last_success_at <= now
        ]
        if secondary.last_success_at and secondary.last_success_at <= now:
            previous.append(secondary.last_success_at)
        merged = BackupSecondaryStatus(
            configured=True, status="failed", last_success_at=max(previous) if previous else None
        )
    return status.model_copy(update={"secondary_copy": merged})


def cloud_conditions(
    targets: Sequence[CloudCopyEvidence],
    *,
    worker_last_seen_at: datetime | None,
    now: datetime,
) -> list[AlertCondition]:
    enabled = [target for target in targets if target.enabled]
    conditions: list[AlertCondition] = []
    for target in targets:
        if target.authorization_status == "reauthorization_required":
            name = PROVIDER_NAMES.get(target.provider, "网盘")
            conditions.append(
                AlertCondition(
                    f"cloud_backup_{target.provider}_authorization_invalid",
                    "critical",
                    f"{name}授权已失效",
                    "已停止此网盘的新上传，请重新绑定。已有历史备份保留，其他网盘与本地备份不受影响。",
                )
            )
    for target in enabled:
        if target.authorization_status == "reauthorization_required":
            continue
        name = PROVIDER_NAMES.get(target.provider, "网盘")
        if target.last_error_code:
            conditions.append(
                AlertCondition(
                    f"cloud_backup_{target.provider}_failed",
                    "critical",
                    f"{name}备份异常",
                    "云端传输或回读校验失败，请在备份管理中检查连接授权、网盘容量并重试。其他网盘副本不受影响。",
                )
            )
        elif not _fresh(target.last_success_at, now=now):
            conditions.append(
                AlertCondition(
                    f"cloud_backup_{target.provider}_stale",
                    "warning",
                    f"{name}备份待验证",
                    "尚无经过回读校验的云端副本，或超过36小时未成功。连接测试通过不代表备份已完成。",
                )
            )
    if enabled and (
        worker_last_seen_at is None
        or not timedelta(0) <= now - worker_last_seen_at <= timedelta(minutes=10)
    ):
        conditions.append(
            AlertCondition(
                "cloud_backup_worker_stale",
                "warning",
                "云备份执行器心跳异常",
                "超过10分钟未收到云备份执行器心跳。请检查ERP备份计划；长时间传输也需要持续心跳。",
            )
        )
    return conditions
