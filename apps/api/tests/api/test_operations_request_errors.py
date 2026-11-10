from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.operations.metrics import RequestMetricsStore
from app.operations.persistence import persist_batch, prune_metrics
from app.rbac.models import RolePermission, UserRole
from app.rbac.seed import PERMISSIONS, ROLES


def _login(client: TestClient, users, key: str = "admin") -> None:
    phone, password, _ = users["credentials"][key]
    assert client.post("/api/v1/auth/login", json={"identifier": phone, "password": password}).status_code == 200


def _event(store: RequestMetricsStore, when: datetime, code: int, *, account_id: UUID | None,
           source_ip: str) -> None:
    store.record(method="GET", route="/api/v1/people/{person_id}", status_code=code,
                 duration_ms=12, occurred_at=when.timestamp(), account_id=account_id,
                 source_ip=source_ip, request_id="safe-request")


def test_super_admin_can_reach_request_error_collection(db_client: TestClient, platform_users) -> None:
    _login(db_client, platform_users)
    response = db_client.get("/api/v1/operations/request-errors")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1,
                               "page_size": 20, "retention_days": 30}


def test_request_error_retry_is_idempotent_and_expiry_is_independent(db: Session) -> None:
    from app.operations.models import OperationsRequestError
    now = datetime.now(UTC)
    store = RequestMetricsStore()
    _event(store, now - timedelta(days=30, seconds=1), 400,
           account_id=None, source_ip="192.0.2.1")
    _event(store, now - timedelta(days=1), 403,
           account_id=UUID("00000000-0000-4000-8000-000000000777"), source_ip="192.0.2.2")
    batch = store.pending_batch()
    assert batch is not None
    assert persist_batch(db, batch, now=now, retention_days=90)
    db.commit()
    assert not persist_batch(db, batch, now=now, retention_days=90)
    db.commit()
    assert db.scalar(select(func.count()).select_from(OperationsRequestError)) == 1
    row = db.scalar(select(OperationsRequestError))
    assert row is not None
    assert row.status_code == 403
    assert row.account_id == UUID("00000000-0000-4000-8000-000000000777")
    prune_metrics(db, now=now + timedelta(days=30), retention_days=90)
    db.commit()
    assert db.scalar(select(func.count()).select_from(OperationsRequestError)) == 0


def test_request_errors_authorization_filters_paging_and_current_account_name(
    db_client: TestClient, platform_users, db: Session,
) -> None:
    from app.operations.models import OperationsRequestError

    now = datetime.now(UTC)
    account_id = platform_users["accounts"]["admin"].id
    missing_id = UUID("00000000-0000-4000-8000-000000000777")
    store = RequestMetricsStore()
    _event(store, now - timedelta(hours=1), 403, account_id=account_id, source_ip="192.0.2.10")
    _event(store, now - timedelta(hours=2), 404, account_id=missing_id, source_ip="192.0.2.20")
    _event(store, now - timedelta(days=2), 500, account_id=None, source_ip="192.0.2.30")
    _event(store, now - timedelta(days=31), 401, account_id=None, source_ip="192.0.2.40")
    batch = store.pending_batch()
    assert batch is not None
    assert persist_batch(db, batch, now=now, retention_days=90)
    # Simulate a row that passed its deadline before the hourly purge tick.
    db.add(OperationsRequestError(
        id=uuid4(), occurred_at=now - timedelta(days=31), account_id=None,
        source_ip="192.0.2.99", request_id=None, method="GET",
        route="/{unmatched}", status_code=500, reason="服务器内部错误",
    ))
    db.commit()
    _login(db_client, platform_users)

    first = db_client.get("/api/v1/operations/request-errors?page_size=1")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store, private"
    assert first.json()["total"] == 2
    assert first.json()["retention_days"] == 30
    assert first.json()["items"][0]["account_id"] == str(account_id)
    assert first.json()["items"][0]["account_name"] == "平台管理员"
    assert first.json()["items"][0]["reason"] == "没有访问权限"
    second = db_client.get("/api/v1/operations/request-errors?page_size=1&page=2")
    assert second.json()["items"][0]["account_id"] == str(missing_id)
    assert second.json()["items"][0]["account_name"] is None
    filtered = db_client.get(
        f"/api/v1/operations/request-errors?days=7&account_id={account_id}&source_ip=192.0.2.10&status_code=403"
    )
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["request_id"] == "safe-request"
    assert db_client.get("/api/v1/operations/request-errors?days=2").status_code == 422
    assert db_client.get("/api/v1/operations/request-errors?account_id=not-a-uuid").status_code == 422
    assert db_client.get("/api/v1/operations/request-errors?source_ip=not-an-ip").status_code == 422
    assert db_client.get("/api/v1/operations/request-errors?status_code=600").status_code == 422
    assert db_client.get("/api/v1/operations/request-errors?page=0").status_code == 422
    assert db_client.get("/api/v1/operations/request-errors?page_size=101").status_code == 422

    _login(db_client, platform_users, "customer_service")
    denied = db_client.get("/api/v1/operations/request-errors")
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "no-store, private"


def test_ordinary_admin_with_operations_permission_still_cannot_read_errors(
    db_client: TestClient, platform_users, db: Session,
) -> None:
    account_id = platform_users["accounts"]["customer_service"].id
    db.add(UserRole(user_id=account_id, role_id=ROLES["admin"].id))
    db.add(RolePermission(
        role_id=ROLES["admin"].id,
        permission_id=PERMISSIONS["operations.monitor.read"].id,
        scope_kind="global",
    ))
    db.commit()
    _login(db_client, platform_users, "customer_service")
    response = db_client.get("/api/v1/operations/request-errors")
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store, private"


def test_request_errors_denied_anonymously_with_no_store(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/operations/request-errors")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"


def test_real_auth_attribution_survives_csrf_denial_but_not_failed_login(
    db_client: TestClient, platform_users,
) -> None:
    _login(db_client, platform_users)
    account_id = platform_users["accounts"]["admin"].id
    denied = db_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "invalid"})
    assert denied.status_code == 403
    wrong = db_client.post("/api/v1/auth/login", json={
        "identifier": platform_users["credentials"]["admin"][0],
        "password": "wrong-password",
    })
    assert wrong.status_code == 401
    batch = db_client.app.state.operations_request_metrics.pending_batch()
    assert batch is not None
    by_route = {event.route: event for event in batch.errors}
    assert by_route["/api/v1/auth/logout"].account_id == account_id
    assert by_route["/api/v1/auth/login"].account_id is None


def test_scoped_forwarded_ip_cannot_block_flush_or_following_valid_event(db: Session) -> None:
    from app.operations.models import OperationsRequestError

    now = datetime.now(UTC)
    store = RequestMetricsStore()
    store.record(method="GET", route="/{unmatched}", status_code=404, duration_ms=1,
                 source_ip="fe80::1%" + "a" * 100, occurred_at=now.timestamp())
    first = store.pending_batch()
    assert first is not None
    assert persist_batch(db, first, now=now, retention_days=30)
    db.commit()
    store.acknowledge_batch(first.id)

    store.record(method="GET", route="/api/v1/people", status_code=500, duration_ms=1,
                 source_ip="192.0.2.9", occurred_at=now.timestamp())
    second = store.pending_batch()
    assert second is not None
    assert persist_batch(db, second, now=now, retention_days=30)
    db.commit()
    rows = db.scalars(select(OperationsRequestError).order_by(OperationsRequestError.status_code)).all()
    assert [(row.status_code, row.source_ip) for row in rows] == [
        (404, None), (500, "192.0.2.9"),
    ]
