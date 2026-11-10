from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import Payment
from app.audit.models import AuditEvent
from app.procurement.models import DailyPurchaseRecord


def _login(client: TestClient, platform_users: dict[str, Any]) -> dict[str, str]:
    phone, password, _ = platform_users["credentials"]["admin"]
    response = client.post(
        "/api/v1/auth/login", json={"identifier": phone, "password": password}
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _write_counts(db: Session) -> tuple[int, ...]:
    return tuple(
        db.scalar(select(func.count()).select_from(model)) or 0
        for model in (Payment, DailyPurchaseRecord, AuditEvent)
    )


@pytest.mark.parametrize(
    "amount",
    ["1e1000000", "NaN", "sNaN", "Infinity", "9" * 1000, "92233720368547758.08"],
    ids=["extreme-exponent", "nan", "signaling-nan", "infinity", "oversized", "bigint-plus-one"],
)
def test_invalid_payment_amount_returns_422_without_financial_or_audit_writes(
    db_client: TestClient, db: Session, platform_users: dict[str, Any], amount: str
) -> None:
    headers = _login(db_client, platform_users)
    before = _write_counts(db)
    response = db_client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "idempotency_key": str(uuid4()),
            "student_person_id": str(platform_users["people"]["self_student"].id),
            "amount": amount,
            "paid_at": datetime.now(UTC).isoformat(),
            "note": "Invalid monetary amount regression",
        },
    )
    assert response.status_code == 422
    assert _write_counts(db) == before


def _purchase_payload(center_id: str, **amounts: object) -> dict[str, object]:
    return {
        "operation_key": str(uuid4()),
        "purchase_date": "2026-10-25",
        "item_name": "Bounded purchase regression",
        "settlement_center_id": center_id,
        "quantity": "1",
        "unit_price_cents": 100,
        **amounts,
    }


@pytest.mark.parametrize(
    "amounts",
    [
        {"quantity": "2000000", "unit_price_cents": 9_000_000_000_000},
        {"quantity": "1e1000000"},
        {"quantity": "NaN"},
        {"quantity": "sNaN"},
        {"quantity": "Infinity"},
    ],
    ids=["product-overflow", "extreme-exponent", "nan", "signaling-nan", "infinity"],
)
def test_invalid_purchase_amount_returns_422_without_financial_or_audit_writes(
    db_client: TestClient,
    db: Session,
    platform_users: dict[str, Any],
    amounts: dict[str, object],
) -> None:
    headers = _login(db_client, platform_users)
    center = db_client.post(
        "/api/v1/procurement/settlement-centers",
        headers=headers,
        json={"name": f"Money bounds {uuid4()}"},
    )
    assert center.status_code == 201
    before = _write_counts(db)
    response = db_client.post(
        "/api/v1/procurement",
        headers=headers,
        json=_purchase_payload(center.json()["id"], **amounts),
    )
    assert response.status_code == 422
    assert _write_counts(db) == before


@pytest.mark.parametrize(
    ("initial", "change"),
    [
        ({"quantity": "2000000", "unit_price_cents": 1}, {"unit_price_cents": 9_000_000_000_000}),
        ({"quantity": "1", "unit_price_cents": 9_000_000_000_000}, {"quantity": "2000000"}),
        (
            {"quantity": "1", "unit_price_cents": 1},
            {"quantity": "2000000", "unit_price_cents": 9_000_000_000_000},
        ),
    ],
    ids=["price-only", "quantity-only", "both-fields"],
)
def test_purchase_updates_reject_merged_total_overflow_without_mutation(
    db_client: TestClient,
    db: Session,
    platform_users: dict[str, Any],
    initial: dict[str, object],
    change: dict[str, object],
) -> None:
    headers = _login(db_client, platform_users)
    center = db_client.post(
        "/api/v1/procurement/settlement-centers",
        headers=headers,
        json={"name": f"Money bounds update {uuid4()}"},
    )
    assert center.status_code == 201
    created = db_client.post(
        "/api/v1/procurement",
        headers=headers,
        json=_purchase_payload(center.json()["id"], **initial),
    )
    assert created.status_code == 201
    record = created.json()
    before = _write_counts(db)
    response = db_client.patch(
        f"/api/v1/procurement/{record['id']}", headers=headers, json=change
    )
    assert response.status_code == 422
    assert _write_counts(db) == before
    current = db_client.get(
        "/api/v1/procurement", params={"settlement_center_id": center.json()["id"]}
    )
    assert current.status_code == 200
    items = current.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == record["id"]
    for field in ("quantity", "unit_price_cents", "total_price_cents"):
        assert items[0][field] == record[field]
