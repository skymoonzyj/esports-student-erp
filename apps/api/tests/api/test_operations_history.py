from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.operations.metrics import RequestMetricsStore
from app.operations.models import OperationsAlert, OperationsMetricBatch, OperationsRequestMinute
from app.operations.monitor import reconcile_alerts
from app.operations.persistence import persist_batch, persistent_snapshot, prune_metrics
from app.operations.probes import AlertCondition


def _login(client: TestClient, users, key="admin") -> str:
    identifier, password, _ = users["credentials"][key]
    response = client.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _record(store: RequestMetricsStore, now: datetime, code=200) -> None:
    store.record(method="GET", route="/api/v1/people/{person_id}", status_code=code,
                 duration_ms=50, occurred_at=now.timestamp())


def test_persistence_survives_new_store_and_retries_do_not_double_count(db: Session) -> None:
    now = datetime.now(UTC)
    first = RequestMetricsStore()
    _record(first, now)
    _record(first, now, 500)
    batch = first.pending_batch()
    assert batch is not None
    assert persist_batch(db, batch, now=now, retention_days=30)
    db.commit()
    # Simulate lost commit acknowledgement and a retry of the identical batch.
    assert not persist_batch(db, batch, now=now, retention_days=30)
    db.commit()
    before_ack = persistent_snapshot(db, first, now=now, window_minutes=5)
    assert before_ack.request_count == 2
    restarted = RequestMetricsStore()
    snapshot = persistent_snapshot(db, restarted, now=now, window_minutes=5)
    assert snapshot.request_count == 2
    assert snapshot.server_error_count == 1
    assert snapshot.average_latency_ms == 50
    assert db.scalar(select(func.count()).select_from(OperationsMetricBatch)) == 1


def test_independent_workers_aggregate_into_one_minute_and_retention_prunes(db: Session) -> None:
    now = datetime.now(UTC)
    for _ in range(2):
        store = RequestMetricsStore()
        _record(store, now)
        batch = store.pending_batch()
        assert batch is not None
        persist_batch(db, batch, now=now, retention_days=30)
        db.commit()
    row = db.scalar(select(OperationsRequestMinute))
    assert row is not None and row.request_count == 2
    assert db.scalar(select(func.count()).select_from(OperationsRequestMinute)) == 1
    prune_metrics(db, now=now + timedelta(days=31), retention_days=30)
    db.commit()
    assert db.scalar(select(func.count()).select_from(OperationsRequestMinute)) == 0
    assert db.scalar(select(func.count()).select_from(OperationsMetricBatch)) == 0


def test_alert_deduplicates_recovers_and_recurrence_is_new_incident(db: Session) -> None:
    now = datetime.now(UTC)
    condition = AlertCondition("backup_failed", "critical", "自动备份失败", "请检查备份任务。")
    reconcile_alerts(db, [condition], now=now)
    first = db.scalar(select(OperationsAlert))
    assert first is not None
    first_id = first.id
    first.acknowledged_at = now
    reconcile_alerts(db, [condition], now=now + timedelta(minutes=1))
    assert db.scalar(select(func.count()).select_from(OperationsAlert)) == 1
    assert first.acknowledged_at == now
    reconcile_alerts(db, [], now=now + timedelta(minutes=2), preserve_keys={"backup_failed"})
    assert first.status == "active"  # Missing status is not evidence of recovery.
    reconcile_alerts(db, [], now=now + timedelta(minutes=3))
    assert first.status == "resolved"
    assert first.resolved_at == now + timedelta(minutes=3)
    reconcile_alerts(db, [condition], now=now + timedelta(minutes=4))
    active = db.scalar(select(OperationsAlert).where(OperationsAlert.status == "active"))
    assert active is not None and active.id != first_id
    assert active.acknowledged_at is None


def test_history_pagination_and_super_admin_boundary(db_client, platform_users, db: Session) -> None:
    now = datetime.now(UTC)
    store = RequestMetricsStore()
    _record(store, now - timedelta(minutes=1))
    _record(store, now - timedelta(minutes=2), 404)
    batch = store.pending_batch()
    assert batch is not None
    persist_batch(db, batch, now=now, retention_days=30)
    db.commit()
    _login(db_client, platform_users)
    response = db_client.get("/api/v1/operations/history?page_size=1")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1
    second = db_client.get("/api/v1/operations/history?page_size=1&page=2").json()
    assert second["items"][0]["client_error_count"] == 1
    assert db_client.get("/api/v1/operations/history?starts_at=2026-01-01T00:00:00").status_code == 422
    _login(db_client, platform_users, "customer_service")
    for path in ("history", "alerts", "backup-status"):
        assert db_client.get(f"/api/v1/operations/{path}").status_code == 403


def test_acknowledge_requires_csrf_and_is_idempotent_audited(db_client, platform_users, db: Session) -> None:
    now = datetime.now(UTC)
    reconcile_alerts(db, [AlertCondition("backup_failed", "critical", "备份失败", "请检查。")], now=now)
    db.commit()
    alert = db.scalar(select(OperationsAlert))
    assert alert is not None
    csrf = _login(db_client, platform_users)
    path = f"/api/v1/operations/alerts/{alert.id}/acknowledge"
    assert db_client.post(path).status_code == 403
    response = db_client.post(path, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json()["acknowledged_at"] is not None
    assert response.json()["status"] == "active"
    assert db_client.post(path, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == "operations.alert.acknowledge"
    )) == 1
    csrf = _login(db_client, platform_users, "customer_service")
    assert db_client.post(path, headers={"X-CSRF-Token": csrf}).status_code == 403
