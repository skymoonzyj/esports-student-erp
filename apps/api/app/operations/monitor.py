from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.operations.cloud_status import cloud_conditions, read_cloud_evidence, with_cloud_copy
from app.operations.metrics import RequestMetricsStore
from app.operations.models import OperationsAlert, OperationsServiceHeartbeat
from app.operations.persistence import persist_batch, persistent_snapshot, prune_metrics
from app.operations.probes import (
    AlertCondition,
    backup_conditions,
    certificate_conditions,
    disk_conditions,
    read_backup_status,
)
from app.operations.runtime import RuntimeSampler
from app.operations.service import OperationsMonitorService

logger = logging.getLogger(__name__)
MONITOR_LOCK = 420_005_202


def reconcile_alerts(
    db: Session, conditions: list[AlertCondition], *, now: datetime,
    preserve_keys: set[str] | None = None,
) -> None:
    """An incident stays one row until recovery; a recurrence starts a new incident."""
    active = {row.key: row for row in db.scalars(
        select(OperationsAlert).where(OperationsAlert.status == "active").with_for_update()
    )}
    seen = set()
    for condition in conditions:
        if condition.key in seen:
            continue
        seen.add(condition.key)
        alert = active.pop(condition.key, None)
        if alert is None:
            db.add(OperationsAlert(
                key=condition.key, severity=condition.severity, title=condition.title,
                detail=condition.detail, status="active", first_seen_at=now, last_seen_at=now,
            ))
        else:
            alert.last_seen_at = now
            alert.severity = condition.severity
            alert.title = condition.title
            alert.detail = condition.detail
    for alert in active.values():
        if preserve_keys and alert.key in preserve_keys:
            continue
        alert.status = "resolved"
        alert.resolved_at = now
    db.flush()


def service_conditions(
    db: Session, store: RequestMetricsStore, sampler: RuntimeSampler,
    *, now: datetime, scheduler_interval_seconds: int,
) -> list[AlertCondition]:
    conditions: list[AlertCondition] = []
    traffic = persistent_snapshot(db, store, window_minutes=5, now=now)
    if traffic.server_error_count:
        conditions.append(AlertCondition(
            "api_server_errors", "critical" if traffic.server_error_count >= 5 else "warning",
            "接口发生服务器错误",
            f"近5分钟出现{traffic.server_error_count}次HTTP 5xx错误，错误率{traffic.error_rate_percent:.1f}%，请检查接口服务日志。",
        ))
    if any(row.route == "/{aggregated}" for row in traffic.top_routes):
        conditions.append(AlertCondition(
            "metrics_buffer_pressure", "warning", "请求统计缓冲区压力较大",
            "数据库写入延迟期间部分请求已合并统计，总数保留，但接口和分钟分布精度降低，请检查数据库连接。",
        ))
    service = OperationsMonitorService(
        db, request_metrics=store, runtime_sampler=sampler,
        scheduler_interval_seconds=scheduler_interval_seconds,
    )
    background = service._background_metrics(now)
    if background.scheduler_status != "healthy":
        conditions.append(AlertCondition(
            "scheduler_unhealthy",
            "critical" if background.scheduler_status == "critical" else "warning",
            "教学任务调度异常",
            "调度任务失败或心跳超时，请检查调度服务。" if background.scheduler_status == "critical"
            else "尚未收到正常的调度心跳，或心跳出现延迟，请检查调度服务。",
        ))
    if background.failed_tasks:
        conditions.append(AlertCondition(
            "evaluation_tasks_failed", "warning", "系统评价任务失败",
            f"当前有{background.failed_tasks}个失败的系统评价任务，请查看评价任务并处理。",
        ))
    runtime = sampler.sample()
    if runtime.cpu_percent >= 95 or (runtime.memory_percent or 0) >= 90:
        conditions.append(AlertCondition(
            "runtime_capacity", "warning", "接口服务资源使用较高",
            f"API进程CPU使用率{runtime.cpu_percent:.1f}%，运行环境内存使用率{(runtime.memory_percent or 0):.1f}%，请检查负载。",
        ))
    return conditions


class OperationsBackgroundMonitor:
    def __init__(self, settings: Settings, store: RequestMetricsStore, sampler: RuntimeSampler) -> None:
        self.settings = settings
        self.store = store
        self.sampler = sampler
        # A small independent pool prevents monitoring failures exhausting request connections.
        self.engine = create_engine(
            settings.database_url, pool_pre_ping=True, pool_size=1, max_overflow=0,
            pool_timeout=5, hide_parameters=True,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
        )
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._last_monitor = 0.0
        self._last_prune = 0.0
        self._next_certificate = 0.0
        self._certificate_conditions: list[AlertCondition] = []

    def flush(self) -> None:
        with self.store.flush_lock:
            batch = self.store.pending_batch()
            if batch is None:
                return
            with self.sessions.begin() as db:
                persist_batch(db, batch, now=datetime.now(UTC),
                              retention_days=self.settings.operations_retention_days)
            self.store.acknowledge_batch(batch.id)

    def monitor(self) -> None:
        now = datetime.now(UTC)
        monotonic = time.monotonic()
        if monotonic >= self._next_certificate:
            self._certificate_conditions = certificate_conditions(
                self.settings.operations_certificate_host,
                self.settings.operations_certificate_port, now=now,
            )
            self._next_certificate = monotonic + (300 if self._certificate_conditions else 21600)
        backup = read_backup_status(self.settings.operations_backup_status_path)
        conditions: list[AlertCondition] = []
        conditions.extend(disk_conditions(self.settings.operations_disk_path))
        conditions.extend(disk_conditions(
            self.settings.evaluation_media_root, key="evaluation_disk", label="评价文件所在磁盘"
        ))
        conditions.extend(self._certificate_conditions)
        observed_keys = {condition.key for condition in conditions}
        preserve_keys: set[str] = set()
        if backup.status in {"running", "unknown"}:
            preserve_keys.update({
                "backup_failed", "backup_stale", "backup_restore_unverified",
                "backup_secondary_failed", "backup_secondary_missing", "backup_running_stale",
            })
        if "certificate_unavailable" in observed_keys:
            preserve_keys.update({"certificate_invalid", "certificate_expiry"})
        for disk_key in ("backup_disk", "evaluation_disk"):
            if f"{disk_key}_unavailable" in observed_keys:
                preserve_keys.add(f"{disk_key}_usage")
        with self.sessions.begin() as db:
            # Every API worker flushes its counters; only one evaluates alert state per interval.
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": MONITOR_LOCK}):
                return
            heartbeat = db.get(OperationsServiceHeartbeat, "operations_monitor")
            if heartbeat is not None and (
                now - heartbeat.last_seen_at
            ).total_seconds() < self.settings.operations_monitor_interval_seconds - 1:
                return
            targets, worker_last_seen = read_cloud_evidence(db)
            conditions.extend(backup_conditions(with_cloud_copy(backup, targets, now=now), now=now))
            conditions.extend(cloud_conditions(targets, worker_last_seen_at=worker_last_seen, now=now))
            conditions.extend(service_conditions(
                db, self.store, self.sampler, now=now,
                scheduler_interval_seconds=self.settings.cohort_scheduler_interval_seconds,
            ))
            reconcile_alerts(db, conditions, now=now, preserve_keys=preserve_keys)
            if heartbeat is None:
                db.add(OperationsServiceHeartbeat(
                    service_key="operations_monitor", status="healthy", last_seen_at=now,
                    last_success_at=now, consecutive_failures=0, last_result_count=len(conditions),
                ))
            else:
                heartbeat.last_seen_at = heartbeat.last_success_at = now
                heartbeat.status = "healthy"
                heartbeat.last_result_count = len(conditions)
            if monotonic - self._last_prune >= 3600:
                prune_metrics(db, now=now, retention_days=self.settings.operations_retention_days)
                db.execute(delete(OperationsAlert).where(
                    OperationsAlert.status == "resolved",
                    OperationsAlert.resolved_at < now - timedelta(days=self.settings.operations_retention_days),
                ))
                self._last_prune = monotonic

    def tick(self) -> None:
        try:
            self.flush()
        except Exception:  # noqa: BLE001 -- isolate all monitoring faults from serving requests
            # Raw database/IO errors can contain infrastructure identifiers or secrets.
            logger.warning("operations_metrics_flush_failed; buffered batch retained for retry")
        if time.monotonic() - self._last_monitor >= self.settings.operations_monitor_interval_seconds:
            self._last_monitor = time.monotonic()
            try:
                self.monitor()
            except Exception:  # noqa: BLE001 -- keep the periodic monitor alive after probe failures
                logger.warning("operations_monitor_tick_failed; retrying at next interval")

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                await asyncio.to_thread(self.tick)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.settings.operations_flush_interval_seconds)
                except TimeoutError:
                    pass
            try:
                await asyncio.to_thread(self.flush)
            except Exception:  # noqa: BLE001 -- graceful shutdown still disposes the connection pool
                logger.warning("operations_metrics_shutdown_flush_failed")
        finally:
            await asyncio.to_thread(self.engine.dispose)
