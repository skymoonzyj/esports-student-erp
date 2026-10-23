from __future__ import annotations

import math
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_EXCLUDED_PATHS = frozenset(
    {
        "/api/v1/health/live",
        "/api/v1/operations/overview",
        "/api/v1/operations/history",
        "/api/v1/operations/alerts",
        "/api/v1/operations/backup-status",
    }
)

# Keep the explanation tied to the status code instead of response bodies. API
# responses can contain personal data, while the status code is safe to retain
# in the in-memory operations snapshot.
CLIENT_ERROR_REASONS: dict[int, str] = {
    400: "请求参数错误",
    401: "未登录或登录已失效",
    403: "没有访问权限",
    404: "请求资源不存在",
    405: "请求方法不被允许",
    408: "请求超时",
    409: "请求冲突",
    413: "请求内容过大",
    415: "不支持的内容类型",
    422: "请求数据校验失败",
    423: "账号已临时锁定",
    429: "请求过于频繁",
}
_MAX_CLIENT_ERROR_DETAILS = 5
LATENCY_BOUNDS = (1, 5, 10, 25, 50, 100, 250, 500, 750, 1500, 3000, 5000, 10000, 30000)
MinuteKey = tuple[datetime, str, str, int]


@dataclass(slots=True)
class MinuteAggregate:
    request_count: int = 0
    latency_sum_ms: float = 0
    max_latency_ms: float = 0
    histogram: tuple[int, ...] = (0,) * (len(LATENCY_BOUNDS) + 1)

    def add(self, duration_ms: float) -> None:
        self.request_count += 1
        self.latency_sum_ms += duration_ms
        self.max_latency_ms = max(self.max_latency_ms, duration_ms)
        index = next(
            (index for index, bound in enumerate(LATENCY_BOUNDS) if duration_ms <= bound),
            len(LATENCY_BOUNDS),
        )
        histogram = list(self.histogram)
        histogram[index] += 1
        self.histogram = tuple(histogram)


@dataclass(frozen=True, slots=True)
class MetricBatch:
    id: UUID
    rows: dict[MinuteKey, MinuteAggregate]


@dataclass(frozen=True, slots=True)
class RequestSample:
    occurred_at: float
    method: str
    route: str
    status_code: int
    duration_ms: float


@dataclass(frozen=True, slots=True)
class TrafficBucket:
    starts_at: datetime
    request_count: int
    error_count: int
    average_latency_ms: float


@dataclass(frozen=True, slots=True)
class RouteMetric:
    method: str
    route: str
    request_count: int
    error_count: int
    average_latency_ms: float


@dataclass(frozen=True, slots=True)
class ClientErrorMetric:
    status_code: int
    count: int
    reason: str


@dataclass(frozen=True, slots=True)
class RequestMetricsSnapshot:
    request_count: int
    client_error_count: int
    client_error_details: tuple[ClientErrorMetric, ...]
    server_error_count: int
    error_rate_percent: float
    requests_per_second: float
    average_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    series: tuple[TrafficBucket, ...]
    top_routes: tuple[RouteMetric, ...]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def normalized_route(scope: Scope) -> str:
    route = scope.get("route")
    path_template = getattr(route, "path", None)
    if isinstance(path_template, str) and path_template:
        request_path = str(scope.get("path", "/"))
        if request_path.startswith("/api/v1/") and not path_template.startswith("/api/v1/"):
            return f"/api/v1{path_template}"
        return path_template
    # Unmatched paths are user-controlled and can contain emails, names or secrets.
    # Only a router-owned template is safe to persist.
    return "/{unmatched}"


class RequestMetricsStore:
    def __init__(
        self,
        *,
        retention_seconds: int = 24 * 60 * 60,
        max_samples: int = 100_000,
        max_pending_keys: int = 10_000,
        clock: Any = time.time,
    ) -> None:
        self.retention_seconds = retention_seconds
        self._samples: deque[RequestSample] = deque(maxlen=max_samples)
        self._lock = threading.Lock()
        self.flush_lock = threading.RLock()
        self._pending: dict[MinuteKey, MinuteAggregate] = {}
        self._overflow: dict[int, tuple[MinuteKey, MinuteAggregate]] = {}
        self._max_pending_keys = max_pending_keys
        self._inflight: MetricBatch | None = None
        self._clock = clock
        self.started_at_epoch = float(clock())

    def record(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_ms: float,
        occurred_at: float | None = None,
    ) -> None:
        timestamp = float(self._clock() if occurred_at is None else occurred_at)
        sample = RequestSample(
            occurred_at=timestamp,
            method=method.upper(),
            route=route,
            status_code=status_code,
            duration_ms=max(0.0, duration_ms),
        )
        cutoff = timestamp - self.retention_seconds
        with self._lock:
            self._samples.append(sample)
            while self._samples and self._samples[0].occurred_at < cutoff:
                self._samples.popleft()
            minute = datetime.fromtimestamp(timestamp, tz=UTC).replace(second=0, microsecond=0)
            safe_method = sample.method if sample.method in {
                "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"
            } else "OTHER"
            key = (minute, safe_method, route[:300], status_code)
            if len(self._pending) >= self._max_pending_keys and key not in self._pending:
                # Bound memory without dropping counters during a prolonged outage.
                # Overflow loses route/minute precision; totals stay grouped by status,
                # attributed to the most recent minute until the next successful flush.
                overflow_status = status_code if 100 <= status_code <= 599 else 500
                key = (minute, "OTHER", "/{aggregated}", overflow_status)
                previous = self._overflow.get(overflow_status)
                aggregate = previous[1] if previous else MinuteAggregate()
                aggregate.add(sample.duration_ms)
                self._overflow[overflow_status] = (key, aggregate)
            else:
                self._pending.setdefault(key, MinuteAggregate()).add(sample.duration_ms)

    def pending_batch(self) -> MetricBatch | None:
        """Caller holds flush_lock until commit and acknowledgement."""
        with self._lock:
            if self._inflight is None and self._pending:
                self._pending.update(dict(self._overflow.values()))
                self._overflow = {}
                self._inflight = MetricBatch(id=uuid4(), rows=self._pending)
                self._pending = {}
            return self._inflight

    def acknowledge_batch(self, batch_id: UUID) -> None:
        with self._lock:
            if self._inflight is not None and self._inflight.id == batch_id:
                self._inflight = None

    def pending_rows(self) -> list[tuple[MinuteKey, MinuteAggregate]]:
        with self._lock:
            rows = list(self._pending.items())
            rows.extend(self._overflow.values())
            if self._inflight is not None:
                rows.extend(self._inflight.rows.items())
            # Aggregates mutate in record(), return detached values to readers.
            return [
                (key, MinuteAggregate(row.request_count, row.latency_sum_ms,
                                      row.max_latency_ms, row.histogram))
                for key, row in rows
            ]

    def snapshot(
        self,
        *,
        window_minutes: int,
        bucket_count: int = 24,
        now: float | None = None,
    ) -> RequestMetricsSnapshot:
        current = float(self._clock() if now is None else now)
        window_seconds = window_minutes * 60
        cutoff = current - window_seconds
        retention_cutoff = current - self.retention_seconds
        with self._lock:
            while self._samples and self._samples[0].occurred_at < retention_cutoff:
                self._samples.popleft()
            samples = tuple(sample for sample in self._samples if sample.occurred_at >= cutoff)

        durations = [sample.duration_ms for sample in samples]
        request_count = len(samples)
        client_error_count = sum(400 <= sample.status_code < 500 for sample in samples)
        client_error_counts: Counter[int] = Counter(
            sample.status_code
            for sample in samples
            if 400 <= sample.status_code < 500
        )
        client_error_details = tuple(
            ClientErrorMetric(
                status_code=status_code,
                count=count,
                reason=CLIENT_ERROR_REASONS.get(status_code, "客户端请求错误"),
            )
            for status_code, count in sorted(
                client_error_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:_MAX_CLIENT_ERROR_DETAILS]
        )
        server_error_count = sum(sample.status_code >= 500 for sample in samples)
        observed_seconds = max(
            1.0,
            min(float(window_seconds), current - self.started_at_epoch),
        )
        bucket_width = window_seconds / bucket_count
        bucket_requests = [0] * bucket_count
        bucket_errors = [0] * bucket_count
        bucket_latency: list[list[float]] = [[] for _ in range(bucket_count)]
        route_counts: Counter[tuple[str, str]] = Counter()
        route_errors: Counter[tuple[str, str]] = Counter()
        route_latency: dict[tuple[str, str], list[float]] = {}

        for sample in samples:
            bucket_index = min(
                bucket_count - 1,
                max(0, int((sample.occurred_at - cutoff) / bucket_width)),
            )
            bucket_requests[bucket_index] += 1
            bucket_errors[bucket_index] += int(sample.status_code >= 500)
            bucket_latency[bucket_index].append(sample.duration_ms)
            route_key = (sample.method, sample.route)
            route_counts[route_key] += 1
            route_errors[route_key] += int(sample.status_code >= 500)
            route_latency.setdefault(route_key, []).append(sample.duration_ms)

        series = tuple(
            TrafficBucket(
                starts_at=datetime.fromtimestamp(cutoff + index * bucket_width, tz=UTC),
                request_count=bucket_requests[index],
                error_count=bucket_errors[index],
                average_latency_ms=(
                    sum(bucket_latency[index]) / len(bucket_latency[index])
                    if bucket_latency[index]
                    else 0.0
                ),
            )
            for index in range(bucket_count)
        )
        top_routes = tuple(
            RouteMetric(
                method=method,
                route=route,
                request_count=count,
                error_count=route_errors[(method, route)],
                average_latency_ms=(
                    sum(route_latency[(method, route)]) / len(route_latency[(method, route)])
                ),
            )
            for (method, route), count in route_counts.most_common(5)
        )
        return RequestMetricsSnapshot(
            request_count=request_count,
            client_error_count=client_error_count,
            client_error_details=client_error_details,
            server_error_count=server_error_count,
            error_rate_percent=(server_error_count / request_count * 100 if request_count else 0.0),
            requests_per_second=request_count / observed_seconds,
            average_latency_ms=(sum(durations) / request_count if request_count else 0.0),
            p50_latency_ms=_percentile(durations, 0.50),
            p95_latency_ms=_percentile(durations, 0.95),
            p99_latency_ms=_percentile(durations, 0.99),
            max_latency_ms=max(durations, default=0.0),
            series=series,
            top_routes=top_routes,
        )


class RequestMetricsMiddleware:
    def __init__(self, app: ASGIApp, *, store: RequestMetricsStore) -> None:
        self.app = app
        self.store = store

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in _EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def capture_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            self.store.record(
                method=str(scope.get("method", "GET")),
                route=normalized_route(scope),
                status_code=status_code,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
