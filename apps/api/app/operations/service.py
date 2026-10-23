from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.evaluations.task_models import SystemEvaluationTask
from app.operations.heartbeat import TEACHING_SCHEDULER_SERVICE_KEY
from app.operations.metrics import RequestMetricsStore
from app.operations.models import OperationsServiceHeartbeat
from app.operations.persistence import persistent_snapshot
from app.operations.runtime import RuntimeSampler
from app.operations.schemas import (
    BackgroundTaskMetrics,
    ClientErrorMetricResponse,
    DatabaseMetrics,
    HealthStatus,
    OperationsHealth,
    OperationsOverview,
    RouteMetricResponse,
    RuntimeMetrics,
    TrafficMetrics,
    TrafficSeriesPoint,
)


def _rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


class OperationsMonitorService:
    def __init__(
        self,
        db: Session,
        *,
        request_metrics: RequestMetricsStore,
        runtime_sampler: RuntimeSampler,
        scheduler_interval_seconds: int,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.request_metrics = request_metrics
        self.runtime_sampler = runtime_sampler
        self.scheduler_interval_seconds = scheduler_interval_seconds
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self._now_provider()
        if value.tzinfo is None:
            raise ValueError("operations monitor clock must return an aware datetime")
        return value

    def _database_metrics(self) -> DatabaseMetrics:
        started = time.perf_counter()
        try:
            row = self.db.execute(
                text(
                    "SELECT "
                    "count(*) FILTER (WHERE state = 'active')::integer AS active, "
                    "count(*) FILTER (WHERE state = 'idle')::integer AS idle, "
                    "count(*)::integer AS total "
                    "FROM pg_stat_activity WHERE datname = current_database()"
                )
            ).mappings().one()
            max_connections = int(self.db.scalar(text("SHOW max_connections")) or 0)
            response_time_ms = (time.perf_counter() - started) * 1000
            total = int(row["total"])
            utilization = total / max_connections * 100 if max_connections else None
            status: HealthStatus = "healthy"
            if utilization is not None and utilization >= 90:
                status = "critical"
            elif utilization is not None and utilization >= 75:
                status = "warning"
            return DatabaseMetrics(
                status=status,
                response_time_ms=_rounded(response_time_ms),
                active_connections=int(row["active"]),
                idle_connections=int(row["idle"]),
                total_connections=total,
                max_connections=max_connections,
                utilization_percent=_rounded(utilization),
            )
        except (SQLAlchemyError, TypeError, ValueError, KeyError):
            return DatabaseMetrics(
                status="critical",
                response_time_ms=None,
                active_connections=None,
                idle_connections=None,
                total_connections=None,
                max_connections=None,
                utilization_percent=None,
            )

    def _background_metrics(self, now: datetime) -> BackgroundTaskMetrics:
        empty_counts: dict[str, int] = {}
        heartbeat: OperationsServiceHeartbeat | None = None
        query_failed = False
        try:
            empty_counts = {
                status: int(count)
                for status, count in self.db.execute(
                    select(SystemEvaluationTask.status, func.count(SystemEvaluationTask.id))
                    .group_by(SystemEvaluationTask.status)
                ).tuples()
            }
            heartbeat = self.db.get(
                OperationsServiceHeartbeat,
                TEACHING_SCHEDULER_SERVICE_KEY,
            )
        except SQLAlchemyError:
            query_failed = True

        scheduler_status: HealthStatus = "unknown"
        scheduler_lag: int | None = None
        if heartbeat is not None:
            scheduler_lag = max(0, int((now - heartbeat.last_seen_at).total_seconds()))
            stale_after = self.scheduler_interval_seconds * 3 + 10
            if scheduler_lag > stale_after or heartbeat.status == "failed":
                scheduler_status = "critical"
            elif scheduler_lag > self.scheduler_interval_seconds * 2:
                scheduler_status = "warning"
            else:
                scheduler_status = "healthy"

        failed = empty_counts.get("failed", 0)
        attention = sum(
            empty_counts.get(status, 0)
            for status in ("manual_input", "correction_pending")
        )
        if query_failed or scheduler_status == "critical":
            status: HealthStatus = "critical"
        elif scheduler_status in {"unknown", "warning"} or failed > 0 or attention > 0:
            status = "warning"
        else:
            status = "healthy"
        return BackgroundTaskMetrics(
            status=status,
            scheduler_status=scheduler_status,
            scheduler_last_seen_at=heartbeat.last_seen_at if heartbeat else None,
            scheduler_lag_seconds=scheduler_lag,
            scheduler_consecutive_failures=heartbeat.consecutive_failures if heartbeat else 0,
            scheduler_last_result_count=heartbeat.last_result_count if heartbeat else None,
            total_tasks=sum(empty_counts.values()),
            queued_tasks=empty_counts.get("queued", 0),
            processing_tasks=empty_counts.get("processing", 0),
            pending_review_tasks=sum(
                empty_counts.get(status, 0)
                for status in ("needs_review", "revision_draft")
            ),
            failed_tasks=failed,
            needs_attention_tasks=attention,
        )

    @staticmethod
    def _health(
        *,
        traffic: TrafficMetrics,
        database: DatabaseMetrics,
        runtime: RuntimeMetrics,
        background: BackgroundTaskMetrics,
    ) -> OperationsHealth:
        score = 100
        score -= min(45, round(traffic.error_rate_percent * 5))
        if traffic.p95_latency_ms >= 5_000:
            score -= 25
        elif traffic.p95_latency_ms >= 1_500:
            score -= 12
        elif traffic.p95_latency_ms >= 750:
            score -= 5
        if database.status == "critical":
            score -= 30
        elif database.status == "warning":
            score -= 12
        if runtime.status == "critical":
            score -= 18
        elif runtime.status == "warning":
            score -= 8
        if background.status == "critical":
            score -= 20
        elif background.status == "warning":
            score -= 7
        score = max(0, min(100, score))

        if (
            score < 60
            or database.status == "critical"
            or background.status == "critical"
            or traffic.error_rate_percent >= 10
        ):
            status: HealthStatus = "critical"
        elif score < 85 or any(
            component == "warning"
            for component in (database.status, runtime.status, background.status)
        ):
            status = "warning"
        else:
            status = "healthy"
        return OperationsHealth(score=score, status=status)

    def overview(self, *, window_minutes: int) -> OperationsOverview:
        now = self._now()
        snapshot = persistent_snapshot(
            self.db, self.request_metrics,
            window_minutes=window_minutes,
            now=now,
        )
        traffic = TrafficMetrics(
            request_count=snapshot.request_count,
            client_error_count=snapshot.client_error_count,
            client_error_details=[
                ClientErrorMetricResponse(
                    status_code=item.status_code,
                    count=item.count,
                    reason=item.reason,
                )
                for item in snapshot.client_error_details
            ],
            server_error_count=snapshot.server_error_count,
            error_rate_percent=round(snapshot.error_rate_percent, 2),
            requests_per_second=round(snapshot.requests_per_second, 3),
            average_latency_ms=round(snapshot.average_latency_ms, 2),
            p50_latency_ms=round(snapshot.p50_latency_ms, 2),
            p95_latency_ms=round(snapshot.p95_latency_ms, 2),
            p99_latency_ms=round(snapshot.p99_latency_ms, 2),
            max_latency_ms=round(snapshot.max_latency_ms, 2),
            series=[
                TrafficSeriesPoint(
                    starts_at=item.starts_at,
                    request_count=item.request_count,
                    error_count=item.error_count,
                    average_latency_ms=round(item.average_latency_ms, 2),
                )
                for item in snapshot.series
            ],
            top_routes=[
                RouteMetricResponse(
                    method=item.method,
                    route=item.route,
                    request_count=item.request_count,
                    error_count=item.error_count,
                    average_latency_ms=round(item.average_latency_ms, 2),
                )
                for item in snapshot.top_routes
            ],
        )
        runtime_sample = self.runtime_sampler.sample()
        memory_percent = _rounded(runtime_sample.memory_percent)
        runtime_status: HealthStatus = "healthy"
        if runtime_sample.cpu_percent >= 95 or (memory_percent or 0) >= 95:
            runtime_status = "critical"
        elif runtime_sample.cpu_percent >= 80 or (memory_percent or 0) >= 80:
            runtime_status = "warning"
        runtime = RuntimeMetrics(
            status=runtime_status,
            cpu_percent=round(runtime_sample.cpu_percent, 2),
            memory_used_mb=round(runtime_sample.memory_used_mb, 1),
            memory_limit_mb=_rounded(runtime_sample.memory_limit_mb),
            memory_percent=memory_percent,
            process_memory_mb=round(runtime_sample.process_memory_mb, 1),
            uptime_seconds=runtime_sample.uptime_seconds,
        )
        database = self._database_metrics()
        background = self._background_metrics(now)
        return OperationsOverview(
            generated_at=now,
            window_minutes=window_minutes,
            health=self._health(
                traffic=traffic,
                database=database,
                runtime=runtime,
                background=background,
            ),
            traffic=traffic,
            database=database,
            runtime=runtime,
            background_tasks=background,
        )
