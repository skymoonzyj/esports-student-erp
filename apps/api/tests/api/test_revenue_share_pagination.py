from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.admissions.models import Payment
from app.revenue_share.models import RevenueShareLedgerEntry


def login(client, users, key):
    identifier, password, _ = users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login", json={"identifier": identifier, "password": password}
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_me_paginates_with_whole_balance_and_never_returns_other_people(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    actor = platform_users["accounts"]["admin"]
    coach = platform_users["people"]["coach"]
    other = platform_users["people"]["customer_service"]
    payment = Payment(
        source_kind="manual",
        payer_person_id=coach.id,
        amount_cents=10_000,
        currency="CNY",
        paid_at=datetime(2026, 8, 1, tzinfo=UTC),
        manual_note="流水分页测试",
        created_by_user_id=actor.id,
    )
    db.add(payment)
    db.flush()
    owner_ids = []
    for person, count in ((coach, 5), (other, 2)):
        for _ in range(count):
            entry = RevenueShareLedgerEntry(
                event_key=f"api-page:{uuid4()}",
                payment_id=payment.id,
                entry_kind="earning",
                role_kind="additional",
                beneficiary_kind="person",
                beneficiary_person_id=person.id,
                calculation_type="percentage",
                percentage_basis_points_snapshot=1000,
                base_amount_cents=10_000,
                amount_cents=1000,
                occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
                created_by_user_id=actor.id,
            )
            db.add(entry)
            db.flush()
            if person == coach:
                owner_ids.append(str(entry.id))
    db.commit()
    login(db_client, platform_users, "coach")
    items = []
    params = {"page_size": 2}
    while True:
        response = db_client.get("/api/v1/revenue-share/me", params=params)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, private"
        payload = response.json()
        assert (payload["earned_cents"], payload["total"], payload["page_size"]) == (5000, 5, 2)
        assert len(payload["items"]) <= 2
        items.extend(item["id"] for item in payload["items"])
        if not payload["has_more"]:
            assert payload["next_cursor"] is None
            break
        params["cursor"] = payload["next_cursor"]
    assert items == sorted(owner_ids, reverse=True)
    assert len(items) == len(set(items)) == 5


@pytest.mark.parametrize("params", [{"page_size": 0}, {"page_size": 101}, {"cursor": "invalid"}])
def test_me_rejects_unbounded_sizes_and_bad_cursors(db_client, platform_users, params):
    login(db_client, platform_users, "coach")
    assert db_client.get("/api/v1/revenue-share/me", params=params).status_code == 422


def test_incremental_reconcile_keeps_permission_csrf_and_batch_limits(db_client, platform_users):
    csrf = login(db_client, platform_users, "admin")
    assert db_client.post("/api/v1/revenue-share/reconcile").status_code == 403
    headers = {"X-CSRF-Token": csrf}
    for body in ({"batch_size": 0}, {"batch_size": 101}, {"cursor": "invalid"}):
        assert (
            db_client.post(
                "/api/v1/revenue-share/reconcile", json=body, headers=headers
            ).status_code
            == 422
        )
    response = db_client.post("/api/v1/revenue-share/reconcile", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "processed": 0,
        "allocated": 0,
        "pending": 0,
        "has_more": False,
        "next_cursor": None,
    }
    csrf = login(db_client, platform_users, "coach")
    assert (
        db_client.post(
            "/api/v1/revenue-share/reconcile", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 403
    )
