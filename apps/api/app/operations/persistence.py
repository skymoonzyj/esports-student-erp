from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.operations.metrics import (
    CLIENT_ERROR_REASONS,
    LATENCY_BOUNDS,
    ClientErrorMetric,
    MetricBatch,
    MinuteAggregate,
    MinuteKey,
    RequestMetricsSnapshot,
    RequestMetricsStore,
    RouteMetric,
    TrafficBucket,
)
from app.operations.models import OperationsMetricBatch, OperationsRequestMinute

METRIC_LOCK = 420_005_201


def persist_batch(db: Session, batch: MetricBatch, *, now: datetime, retention_days: int) -> bool:
    """Caller commits before acknowledging. The receipt and every counter are atomic."""
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": METRIC_LOCK})
    inserted = db.scalar(
        insert(OperationsMetricBatch).values(id=batch.id, created_at=now)
        .on_conflict_do_nothing().returning(OperationsMetricBatch.id)
    )
    if inserted is None:
        return False
    cutoff = now - timedelta(days=retention_days)
    for key, aggregate in batch.rows.items():
        if key[0] < cutoff:
            continue
        row = db.get(OperationsRequestMinute, key)
        if row is None:
            db.add(OperationsRequestMinute(
                starts_at=key[0], method=key[1], route=key[2], status_code=key[3],
                request_count=aggregate.request_count,
                latency_sum_ms=aggregate.latency_sum_ms,
                max_latency_ms=aggregate.max_latency_ms,
                latency_histogram=list(aggregate.histogram),
            ))
        else:
            row.request_count += aggregate.request_count
            row.latency_sum_ms += aggregate.latency_sum_ms
            row.max_latency_ms = max(row.max_latency_ms, aggregate.max_latency_ms)
            row.latency_histogram = [
                left + right for left, right in zip(
                    row.latency_histogram, aggregate.histogram, strict=True
                )
            ]
    db.flush()
    return True


def prune_metrics(db: Session, *, now: datetime, retention_days: int) -> None:
    cutoff = now - timedelta(days=retention_days)
    db.execute(delete(OperationsRequestMinute).where(OperationsRequestMinute.starts_at < cutoff))
    db.execute(delete(OperationsMetricBatch).where(OperationsMetricBatch.created_at < cutoff))


def _percentile(histogram: list[int], percentile: float, maximum: float) -> float:
    target = math.ceil(sum(histogram) * percentile)
    if target == 0:
        return 0
    total = 0
    for index, count in enumerate(histogram):
        total += count
        if total >= target:
            return min(float(LATENCY_BOUNDS[index]), maximum) if index < len(LATENCY_BOUNDS) else maximum
    return maximum


def persistent_snapshot(
    db: Session, store: RequestMetricsStore, *, window_minutes: int, now: datetime,
    bucket_count: int = 24,
) -> RequestMetricsSnapshot:
    # Match minute boundaries so the totals and history are stable across restarts.
    cutoff = (now - timedelta(minutes=window_minutes)).replace(second=0, microsecond=0)
    with store.flush_lock:
        persisted = db.scalars(select(OperationsRequestMinute).where(
            OperationsRequestMinute.starts_at >= cutoff,
            OperationsRequestMinute.starts_at <= now,
        )).all()
        rows: list[tuple[MinuteKey, MinuteAggregate]] = [
            ((row.starts_at, row.method, row.route, row.status_code), MinuteAggregate(
                row.request_count, row.latency_sum_ms, row.max_latency_ms,
                tuple(row.latency_histogram),
            )) for row in persisted
        ]
        # A failed commit acknowledgement can leave a committed receipt in the buffer.
        batch = store.pending_batch()
        if batch is not None and db.get(OperationsMetricBatch, batch.id) is not None:
            store.acknowledge_batch(batch.id)
        rows.extend((key, value) for key, value in store.pending_rows() if cutoff <= key[0] <= now)
    requests = clients = servers = 0
    latency = maximum = 0.0
    histogram = [0] * (len(LATENCY_BOUNDS) + 1)
    client_counts: Counter[int] = Counter()
    routes: dict[tuple[str, str], list[float]] = {}
    buckets: list[list[float]] = [[0, 0, 0] for _ in range(bucket_count)]
    width = max(1.0, (now - cutoff).total_seconds()) / bucket_count
    for (minute, method, route, status), row in rows:
        requests += row.request_count
        clients += row.request_count if 400 <= status < 500 else 0
        errors = row.request_count if status >= 500 else 0
        servers += errors
        latency += row.latency_sum_ms
        maximum = max(maximum, row.max_latency_ms)
        if 400 <= status < 500:
            client_counts[status] += row.request_count
        histogram = [left + right for left, right in zip(histogram, row.histogram, strict=True)]
        route_totals = routes.setdefault((method, route), [0, 0, 0])
        route_totals[0] += row.request_count
        route_totals[1] += errors
        route_totals[2] += row.latency_sum_ms
        bucket = buckets[min(bucket_count - 1, max(0, int((minute - cutoff).total_seconds() / width)))]
        bucket[0] += row.request_count
        bucket[1] += errors
        bucket[2] += row.latency_sum_ms
    return RequestMetricsSnapshot(
        request_count=requests, client_error_count=clients, server_error_count=servers,
        client_error_details=tuple(ClientErrorMetric(
            status_code=code, count=count,
            reason=CLIENT_ERROR_REASONS.get(code, "客户端请求错误"),
        ) for code, count in sorted(client_counts.items(), key=lambda item: (-item[1], item[0]))[:5]),
        error_rate_percent=servers / requests * 100 if requests else 0,
        requests_per_second=requests / max(1, (now - cutoff).total_seconds()),
        average_latency_ms=latency / requests if requests else 0,
        p50_latency_ms=_percentile(histogram, .50, maximum),
        p95_latency_ms=_percentile(histogram, .95, maximum),
        p99_latency_ms=_percentile(histogram, .99, maximum),
        max_latency_ms=maximum,
        series=tuple(TrafficBucket(
            starts_at=datetime.fromtimestamp(cutoff.timestamp() + index * width, tz=UTC),
            request_count=int(bucket[0]), error_count=int(bucket[1]),
            average_latency_ms=bucket[2] / bucket[0] if bucket[0] else 0,
        ) for index, bucket in enumerate(buckets)),
        top_routes=tuple(RouteMetric(
            method=key[0], route=key[1], request_count=int(values[0]), error_count=int(values[1]),
            average_latency_ms=values[2] / values[0] if values[0] else 0,
        ) for key, values in sorted(routes.items(), key=lambda item: -item[1][0])[:5]),
    )
