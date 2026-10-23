from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

HealthStatus = Literal["healthy", "warning", "critical", "unknown"]


class OperationsHealth(BaseModel):
    score: int
    status: HealthStatus


class TrafficSeriesPoint(BaseModel):
    starts_at: datetime
    request_count: int
    error_count: int
    average_latency_ms: float


class RouteMetricResponse(BaseModel):
    method: str
    route: str
    request_count: int
    error_count: int
    average_latency_ms: float


class ClientErrorMetricResponse(BaseModel):
    status_code: int
    count: int
    reason: str


class TrafficMetrics(BaseModel):
    request_count: int
    client_error_count: int
    client_error_details: list[ClientErrorMetricResponse]
    server_error_count: int
    error_rate_percent: float
    requests_per_second: float
    average_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    series: list[TrafficSeriesPoint]
    top_routes: list[RouteMetricResponse]


class DatabaseMetrics(BaseModel):
    status: HealthStatus
    response_time_ms: float | None
    active_connections: int | None
    idle_connections: int | None
    total_connections: int | None
    max_connections: int | None
    utilization_percent: float | None


class RuntimeMetrics(BaseModel):
    status: HealthStatus
    cpu_percent: float
    memory_used_mb: float
    memory_limit_mb: float | None
    memory_percent: float | None
    process_memory_mb: float
    uptime_seconds: int


class BackgroundTaskMetrics(BaseModel):
    status: HealthStatus
    scheduler_status: HealthStatus
    scheduler_last_seen_at: datetime | None
    scheduler_lag_seconds: int | None
    scheduler_consecutive_failures: int
    scheduler_last_result_count: int | None
    total_tasks: int
    queued_tasks: int
    processing_tasks: int
    pending_review_tasks: int
    failed_tasks: int
    needs_attention_tasks: int


class OperationsOverview(BaseModel):
    generated_at: datetime
    window_minutes: int
    health: OperationsHealth
    traffic: TrafficMetrics
    database: DatabaseMetrics
    runtime: RuntimeMetrics
    background_tasks: BackgroundTaskMetrics


class OperationsHistoryPoint(BaseModel):
    starts_at: datetime
    request_count: int
    client_error_count: int
    server_error_count: int
    average_latency_ms: float
    max_latency_ms: float


class OperationsHistoryPage(BaseModel):
    items: list[OperationsHistoryPoint]
    total: int
    page: int
    page_size: int
    retention_days: int


class OperationsAlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    severity: Literal["warning", "critical"]
    title: str
    detail: str
    status: Literal["active", "resolved"]
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: UUID | None


class OperationsAlertPage(BaseModel):
    items: list[OperationsAlertResponse]
    total: int
    page: int
    page_size: int


class BackupSecondaryStatus(BaseModel):
    configured: bool = False
    status: Literal["not_configured", "ok", "failed", "unknown"] = "not_configured"
    last_success_at: datetime | None = None


class OperationsBackupStatus(BaseModel):
    status: Literal["running", "ok", "failed", "unknown"] = "unknown"
    completed_at: datetime | None = None
    last_success_at: datetime | None = None
    started_at: datetime | None = None
    verification_at: datetime | None = None
    verification_status: Literal["verified", "unverified"] = "unverified"
    size_bytes: int | None = None
    error_code: str | None = None
    reason: str
    secondary_copy: BackupSecondaryStatus = BackupSecondaryStatus()
