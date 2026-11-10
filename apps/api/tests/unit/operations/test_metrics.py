from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Scope

from app.core.errors import install_exception_handlers
from app.operations.metrics import RequestMetricsMiddleware, RequestMetricsStore, normalized_route


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


def test_error_details_are_error_only_safe_and_bounded() -> None:
    store = RequestMetricsStore(max_pending_errors=2, clock=lambda: 1_000)
    account_id = UUID("00000000-0000-4000-8000-000000000123")
    store.record(method="GET", route="/safe", status_code=200, duration_ms=1,
                 account_id=account_id, source_ip="203.0.113.1")
    store.record(method="GET", route="/safe", status_code=401, duration_ms=1,
                 source_ip="203.0.113.1", occurred_at=1_001)
    store.record(method="POST", route="/safe", status_code=403, duration_ms=1,
                 account_id=account_id, source_ip="203.0.113.2", occurred_at=1_002)
    store.record(method="GET", route="/safe", status_code=500, duration_ms=1,
                 source_ip="203.0.113.3", occurred_at=1_003)

    batch = store.pending_batch()
    assert batch is not None
    assert [(event.status_code, event.account_id, event.reason) for event in batch.errors] == [
        (401, None, "未登录或登录已失效"),
        (403, account_id, "没有访问权限"),
    ]
    assert store.dropped_error_details == 1
    assert store.error_pressure_recent(now=1_003)
    assert store.pending_batch() is batch
    store.acknowledge_batch(batch.id)
    assert store.pending_batch() is None
    store.record(method="GET", route="/safe", status_code=500, duration_ms=1,
                 occurred_at=1_004)
    recovered = store.pending_batch()
    assert recovered is not None and len(recovered.errors) == 1
    assert store.dropped_error_details == 1
    assert not store.error_pressure_recent(now=1_604)


def test_middleware_records_authenticated_template_and_untrusted_proxy_source() -> None:
    store = RequestMetricsStore(clock=lambda: 1_000)
    account_id = UUID("00000000-0000-4000-8000-000000000123")

    class Route:
        path = "/people/{person_id}"

    class Settings:
        trusted_proxy_cidrs = "10.0.0.0/8"

    class App:
        state = type("State", (), {"settings": Settings()})()

    async def endpoint(scope, receive, send) -> None:
        del receive
        scope["route"] = Route()
        scope["state"]["operations_authenticated_account_id"] = account_id
        await send({"type": "http.response.start", "status": 422, "headers": []})
        await send({"type": "http.response.body", "body": b"secret body"})

    scope = cast(Scope, {
        "type": "http", "method": "PATCH", "path": "/api/v1/people/private-uuid",
        "query_string": b"password=secret", "headers": [(b"x-forwarded-for", b"1.1.1.1")],
        "client": ("198.51.100.5", 1234), "state": {"request_id": "safe-id"}, "app": App(),
    })
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    asyncio.run(RequestMetricsMiddleware(endpoint, store=store)(scope, receive, send))
    batch = store.pending_batch()
    assert batch is not None
    assert len(batch.errors) == 1
    event = batch.errors[0]
    assert event.account_id == account_id
    assert event.source_ip == "198.51.100.5"
    assert event.route == "/api/v1/people/{person_id}"
    assert event.request_id == "safe-id"
    assert event.reason == "请求数据校验失败"
    assert "secret" not in repr(event)


def test_denied_monitoring_read_is_recorded_without_monitoring_itself() -> None:
    store = RequestMetricsStore()

    async def endpoint(scope, receive, send) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 403, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = cast(Scope, {
        "type": "http", "method": "GET", "path": "/api/v1/operations/overview",
        "headers": [], "client": ("127.0.0.1", 1), "state": {},
        "app": type("App", (), {"state": type("State", (), {"settings": type("Settings", (), {"trusted_proxy_cidrs": ""})()})()})(),
    })

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        del message

    asyncio.run(RequestMetricsMiddleware(endpoint, store=store)(scope, receive, send))
    batch = store.pending_batch()
    assert batch is not None
    assert batch.rows == {}
    assert len(batch.errors) == 1
    assert batch.errors[0].status_code == 403


def test_unhandled_request_error_read_is_private() -> None:
    app = FastAPI()
    app.state.settings = type("Settings", (), {"trusted_proxy_cidrs": ""})()
    store = RequestMetricsStore()
    app.add_middleware(RequestMetricsMiddleware, store=store)
    install_exception_handlers(app)

    @app.get("/api/v1/operations/request-errors")
    def fail() -> None:
        raise RuntimeError("private infrastructure detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/operations/request-errors")
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store, private"
    assert "private infrastructure detail" not in response.text


def test_scoped_ipv6_from_trusted_forwarder_cannot_poison_error_batch() -> None:
    store = RequestMetricsStore()

    class Settings:
        trusted_proxy_cidrs = "10.0.0.0/8"

    class App:
        state = type("State", (), {"settings": Settings()})()

    async def endpoint(scope, receive, send) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = cast(Scope, {
        "type": "http", "method": "GET", "path": "/private/unmatched",
        "headers": [(b"x-forwarded-for", ("fe80::1%" + "a" * 100).encode())],
        "client": ("10.0.0.2", 9999), "state": {}, "app": App(),
    })

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        del message

    asyncio.run(RequestMetricsMiddleware(endpoint, store=store)(scope, receive, send))
    batch = store.pending_batch()
    assert batch is not None
    assert len(batch.errors) == 1
    assert batch.errors[0].source_ip is None
    assert batch.errors[0].route == "/{unmatched}"


def test_scoped_or_oversize_ip_is_rejected_by_record_boundary() -> None:
    store = RequestMetricsStore()
    store.record(method="GET", route="/safe", status_code=500, duration_ms=1,
                 source_ip="fe80::1%" + "a" * 100)
    store.record(method="GET", route="/safe", status_code=500, duration_ms=1,
                 source_ip="192.0.2.3")
    batch = store.pending_batch()
    assert batch is not None
    assert [event.source_ip for event in batch.errors] == [None, "192.0.2.3"]


def test_pressure_warning_stays_active_while_old_batch_remains_saturated() -> None:
    store = RequestMetricsStore(max_pending_errors=1)
    store.record(method="GET", route="/safe", status_code=500, duration_ms=1,
                 occurred_at=1_000)
    store.record(method="GET", route="/safe", status_code=500, duration_ms=1,
                 occurred_at=1_001)
    batch = store.pending_batch()
    assert batch is not None and len(batch.errors) == 1
    assert store.error_pressure_recent(now=1_601)
    store.acknowledge_batch(batch.id)
    assert not store.error_pressure_recent(now=1_601)
    assert store.dropped_error_details == 1
