"""Cross-module HTTP acceptance with committed, synthetic asset records."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.asset_management.models import Asset, AssetAssignment
from app.asset_management.rental_models import AssetRentalOperation, AssetRentalRenewal
from app.audit.models import AuditEvent
from app.core.database import get_db
from app.core.time import business_date_at
from app.main import create_app
from tests.departure_helpers import committed_scene


def test_rental_http_workflow_keeps_custody_lease_and_workbench_consistent(
    postgres_engine,
    test_settings,
):
    now = datetime.now(UTC)
    today = business_date_at(now)
    created_ids: list[UUID] = []
    with committed_scene(postgres_engine) as (db, context, subject, _source):
        session_token, csrf_token = uuid4().hex, uuid4().hex
        pepper = base64.urlsafe_b64decode(test_settings.session_token_pepper.get_secret_value())
        context.session.token_digest = hmac.new(
            pepper, session_token.encode(), hashlib.sha256
        ).hexdigest()
        context.session.csrf_digest = hmac.new(
            pepper, csrf_token.encode(), hashlib.sha256
        ).hexdigest()
        db.commit()
        app = create_app(test_settings)
        app.dependency_overrides[get_db] = lambda: db
        try:
            with TestClient(app) as client:
                client.cookies.set("erp_session", session_token, path="/api")
                client.headers["X-CSRF-Token"] = csrf_token

                def create(name, days):
                    response = client.post(
                        "/api/v1/assets",
                        json={
                            "name": name,
                            "ownership_type": "rented",
                            "rental_started_on": (today - timedelta(days=14)).isoformat(),
                            "rental_expires_on": (today + timedelta(days=days)).isoformat(),
                            "location": "合成租赁验收训练室",
                        },
                    )
                    assert response.status_code == 201, response.text
                    asset = response.json()
                    created_ids.append(UUID(asset["id"]))
                    return asset

                first = create("即将到期的合成训练设备", 1)
                reminders = client.get("/api/v1/portal/rental-reminders")
                assert reminders.status_code == 200, reminders.text
                assert reminders.json()["available"] is True
                assert [item["asset_id"] for item in reminders.json()["items"]] == [first["id"]]
                assert reminders.json()["items"][0]["days_remaining"] == 1
                payload = {
                    "operation_key": str(uuid4()),
                    "period": "week",
                    "assets": [{"asset_id": first["id"], "expected_rental_revision": 0}],
                }
                renewed = client.post("/api/v1/assets/rentals/renew", json=payload)
                assert renewed.status_code == 200, renewed.text
                assert (
                    renewed.json()["items"][0]["rental_expires_on"]
                    == (today + timedelta(days=8)).isoformat()
                )
                assert client.get("/api/v1/portal/rental-reminders").json()["total"] == 0
                replay = client.post("/api/v1/assets/rentals/renew", json=payload)
                assert replay.status_code == 200, replay.text
                assert replay.json() == {**renewed.json(), "replayed": True}
                assert (
                    client.get(f"/api/v1/assets/{first['id']}/rental-renewals").json()["total"] == 1
                )

                second = create("第七天到期的合成训练设备", 7)
                assigned = client.post(
                    f"/api/v1/assets/{second['id']}/assign",
                    json={
                        "person_id": str(subject.id),
                        "assigned_at": (now - timedelta(days=1)).isoformat(),
                        "expected_return_at": (now + timedelta(days=3)).isoformat(),
                    },
                )
                assert assigned.status_code == 200, assigned.text
                item = client.get("/api/v1/portal/rental-reminders").json()["items"][0]
                assert (item["asset_id"], item["days_remaining"], item["person_name"]) == (
                    second["id"],
                    7,
                    subject.full_name,
                )
                end_payload = {"returned_on": today.isoformat(), "expected_rental_revision": 0}
                assert (
                    client.post(
                        f"/api/v1/assets/{second['id']}/rental-return", json=end_payload
                    ).status_code
                    == 409
                )
                returned = client.post(
                    f"/api/v1/assets/{second['id']}/return", json={"returned_at": now.isoformat()}
                )
                assert returned.status_code == 200, returned.text
                still_rented = client.get("/api/v1/portal/rental-reminders").json()
                assert still_rented["total"] == 1
                assert still_rented["items"][0]["person_name"] is None
                ended = client.post(
                    f"/api/v1/assets/{second['id']}/rental-return", json=end_payload
                )
                assert ended.status_code == 200, ended.text
                assert ended.json()["rental_expires_on"] == (today + timedelta(days=7)).isoformat()
                assert ended.json()["rental_returned_on"] == today.isoformat()
                assert client.get("/api/v1/portal/rental-reminders").json()["total"] == 0
                custody = client.get(f"/api/v1/assets/{second['id']}/history").json()
                assert len(custody) == 1 and custody[0]["returned_at"] is not None
        finally:
            db.rollback()
            db.execute(
                delete(AssetRentalRenewal).where(AssetRentalRenewal.asset_id.in_(created_ids))
            )
            db.execute(
                delete(AssetRentalOperation).where(
                    AssetRentalOperation.actor_user_id == context.account.id
                )
            )
            db.execute(delete(AssetAssignment).where(AssetAssignment.asset_id.in_(created_ids)))
            db.execute(delete(Asset).where(Asset.id.in_(created_ids)))
            # Only the disposable database's own committed fixture needs cleanup.
            # Keep the append-only trigger active throughout the actual HTTP test.
            db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            try:
                db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == context.account.id))
            finally:
                db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            db.commit()
