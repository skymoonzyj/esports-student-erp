from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.operations.heartbeat import (
    TEACHING_SCHEDULER_SERVICE_KEY,
    record_service_heartbeat,
)
from app.operations.metrics import RequestMetricsStore
from app.operations.runtime import RuntimeSampler
from app.operations.service import OperationsMonitorService


def _sign_in(client: TestClient, platform_users, key: str) -> None:
    identifier, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert response.status_code == 200


def test_super_admin_reads_real_operations_overview(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    now = datetime.now(UTC)
    record_service_heartbeat(
        db,
        service_key=TEACHING_SCHEDULER_SERVICE_KEY,
        succeeded=True,
        occurred_at=now,
        result_count=2,
    )
    _sign_in(db_client, platform_users, "admin")

    response = db_client.get("/api/v1/operations/overview?window_minutes=60")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    body = response.json()
    assert body["window_minutes"] == 60
    assert 0 <= body["health"]["score"] <= 100
    assert body["traffic"]["request_count"] >= 1
    assert body["traffic"]["client_error_details"] == []
    assert len(body["traffic"]["series"]) == 24
    assert body["database"]["status"] in {"healthy", "warning"}
    assert body["database"]["max_connections"] > 0
    assert body["runtime"]["uptime_seconds"] >= 0
    assert body["background_tasks"]["scheduler_status"] == "healthy"
    assert body["background_tasks"]["scheduler_last_result_count"] == 2
    assert "phone" not in str(body).lower()
    assert "identity" not in str(body).lower()


def test_operations_overview_exposes_client_error_code_and_reason(
    db_client: TestClient,
    platform_users,
) -> None:
    _sign_in(db_client, platform_users, "admin")

    missing_response = db_client.get("/api/v1/operations/missing-resource")
    assert missing_response.status_code == 404

    response = db_client.get("/api/v1/operations/overview?window_minutes=60")

    assert response.status_code == 200
    assert response.json()["traffic"]["client_error_details"] == [
        {
            "status_code": 404,
            "count": 1,
            "reason": "请求资源不存在",
        }
    ]


def test_non_super_admin_cannot_read_operations_overview(
    db_client: TestClient,
    platform_users,
) -> None:
    _sign_in(db_client, platform_users, "customer_service")

    response = db_client.get("/api/v1/operations/overview")

    assert response.status_code == 403
    assert response.json()["detail"] == "只有超级管理员可以查看运维监控"


def test_operations_window_validation_is_bounded(
    db_client: TestClient,
    platform_users,
) -> None:
    _sign_in(db_client, platform_users, "admin")

    assert db_client.get("/api/v1/operations/overview?window_minutes=4").status_code == 422
    assert db_client.get("/api/v1/operations/overview?window_minutes=1441").status_code == 422


class FailingSession:
    def execute(self, *args, **kwargs):
        del args, kwargs
        raise SQLAlchemyError("database unavailable")


def test_database_probe_degrades_without_exposing_the_exception() -> None:
    service = OperationsMonitorService(
        cast(Session, FailingSession()),
        request_metrics=RequestMetricsStore(),
        runtime_sampler=RuntimeSampler(),
        scheduler_interval_seconds=30,
    )

    metrics = service._database_metrics()

    assert metrics.status == "critical"
    assert metrics.response_time_ms is None
