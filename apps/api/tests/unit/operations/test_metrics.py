from __future__ import annotations

from typing import cast

import pytest
from starlette.types import Scope

from app.operations.metrics import RequestMetricsStore, normalized_route


class Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_request_metrics_calculates_errors_percentiles_buckets_and_routes() -> None:
    clock = Clock(1_000)
    store = RequestMetricsStore(clock=clock)
    store.record(
        method="get",
        route="/api/v1/people",
        status_code=200,
        duration_ms=10,
        occurred_at=1_010,
    )
    store.record(
        method="GET",
        route="/api/v1/people",
        status_code=404,
        duration_ms=20,
        occurred_at=1_020,
    )
    store.record(
        method="POST",
        route="/api/v1/enrollments",
        status_code=500,
        duration_ms=90,
        occurred_at=1_030,
    )

    snapshot = store.snapshot(window_minutes=5, bucket_count=5, now=1_060)

    assert snapshot.request_count == 3
    assert snapshot.client_error_count == 1
    assert len(snapshot.client_error_details) == 1
    assert snapshot.client_error_details[0].status_code == 404
    assert snapshot.client_error_details[0].count == 1
    assert snapshot.client_error_details[0].reason == "请求资源不存在"
    assert snapshot.server_error_count == 1
    assert snapshot.error_rate_percent == pytest.approx(100 / 3)
    assert snapshot.requests_per_second == 3 / 60
    assert snapshot.average_latency_ms == 40
    assert snapshot.p50_latency_ms == 20
    assert snapshot.p95_latency_ms == 83
    assert snapshot.p99_latency_ms == 88.6
    assert snapshot.max_latency_ms == 90
    assert sum(point.request_count for point in snapshot.series) == 3
    assert sum(point.error_count for point in snapshot.series) == 1
    assert snapshot.top_routes[0].route == "/api/v1/people"
    assert snapshot.top_routes[0].request_count == 2
    assert snapshot.top_routes[0].error_count == 0


def test_request_metrics_groups_client_errors_by_code_and_limits_details() -> None:
    clock = Clock(10_000)
    store = RequestMetricsStore(clock=clock)
    for status_code, count in (
        (404, 4),
        (401, 4),
        (422, 3),
        (429, 2),
        (400, 1),
        (403, 1),
        (499, 1),
    ):
        for offset in range(count):
            store.record(
                method="GET",
                route="/api/v1/test",
                status_code=status_code,
                duration_ms=10,
                occurred_at=9_950 + offset,
            )

    snapshot = store.snapshot(window_minutes=5, now=10_000)

    assert snapshot.client_error_count == 16
    assert [item.status_code for item in snapshot.client_error_details] == [
        401,
        404,
        422,
        429,
        400,
    ]
    assert [item.count for item in snapshot.client_error_details] == [4, 4, 3, 2, 1]
    assert [item.reason for item in snapshot.client_error_details] == [
        "未登录或登录已失效",
        "请求资源不存在",
        "请求数据校验失败",
        "请求过于频繁",
        "请求参数错误",
    ]


def test_request_metrics_prunes_expired_samples() -> None:
    clock = Clock(10_000)
    store = RequestMetricsStore(retention_seconds=60, clock=clock)
    store.record(
        method="GET",
        route="/old",
        status_code=500,
        duration_ms=50,
        occurred_at=9_900,
    )
    store.record(
        method="GET",
        route="/current",
        status_code=200,
        duration_ms=5,
        occurred_at=9_990,
    )

    snapshot = store.snapshot(window_minutes=5, now=10_000)

    assert snapshot.request_count == 1
    assert snapshot.server_error_count == 0
    assert snapshot.top_routes[0].route == "/current"


def test_unknown_routes_remove_identifiers_without_reading_query_or_body() -> None:
    scope = cast(
        Scope,
        {
            "type": "http",
            "path": "/api/v1/people/019f9d04-d6bf-7782-94d1-b128af5bb6ef/123",
        },
    )

    assert normalized_route(scope) == "/{unmatched}"


def test_unknown_route_never_persists_email_or_secret() -> None:
    scope = cast(Scope, {"type": "http", "path": "/missing/user@example.com/private-password"})
    assert normalized_route(scope) == "/{unmatched}"


def test_buffer_overflow_preserves_counts_while_bounding_cardinality() -> None:
    store = RequestMetricsStore(max_pending_keys=2)
    for index in range(100):
        store.record(method="GET", route=f"/route/{index}", status_code=200,
                     duration_ms=10, occurred_at=1000 + index * 60)
    batch = store.pending_batch()
    assert batch is not None
    assert len(batch.rows) == 3
    assert sum(row.request_count for row in batch.rows.values()) == 100
    assert sum(row.latency_sum_ms for row in batch.rows.values()) == 1000
    assert store.pending_batch() is batch
    store.record(method="POST", route="/new", status_code=500, duration_ms=20)
    store.acknowledge_batch(batch.id)
    next_batch = store.pending_batch()
    assert next_batch is not None
    assert next_batch.id != batch.id
    assert sum(row.request_count for row in next_batch.rows.values()) == 1


def test_mounted_route_templates_keep_the_public_api_prefix() -> None:
    class Route:
        path = "/auth/me"

    scope = cast(
        Scope,
        {
            "type": "http",
            "path": "/api/v1/auth/me",
            "route": Route(),
        },
    )

    assert normalized_route(scope) == "/api/v1/auth/me"
