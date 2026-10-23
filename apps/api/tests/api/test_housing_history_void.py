from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select

from app.audit.models import AuditEvent
from app.auth.dependencies import require_standard_account
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from tests.services.test_housing_history_void import command, counts, setup_history


def test_void_route_is_atomic_private_and_replay_safe(db_client, db):
    actor, _, stay = setup_history(db)
    db.commit()
    db_client.app.dependency_overrides[require_standard_account] = lambda: actor
    payload = command().model_dump(mode="json")
    payload["reason"] = "历史导入重复记录"
    try:
        first = db_client.post(f"/api/v1/housing/stays/{stay.id}/void-history", json=payload)
        assert first.status_code == 200, first.text
        again = db_client.post(f"/api/v1/housing/stays/{stay.id}/void-history", json=payload)
        assert again.status_code == 200 and again.json() == first.json()
        assert first.headers["cache-control"] == "no-store, private"
        assert counts(db, stay.id) == (1, 1)
        before = db.scalar(select(func.count()).select_from(AuditEvent))
        payload["idempotency_key"] = str(command().idempotency_key)
        already = db_client.post(f"/api/v1/housing/stays/{stay.id}/void-history", json=payload)
        assert already.status_code == 409
        assert already.json()["detail"] == "已作废，无需重复操作"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == before
    finally:
        db_client.app.dependency_overrides.pop(require_standard_account)


def test_void_route_requires_authenticated_session(db_client):
    from uuid import uuid4

    response = db_client.post(f"/api/v1/housing/stays/{uuid4()}/void-history", json={})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"


def test_void_route_replay_denial_is_audited_without_reason(db_client, db):
    actor, _, stay = setup_history(db)
    db.commit()
    db_client.app.dependency_overrides[require_standard_account] = lambda: actor
    payload = command().model_dump(mode="json")
    payload["reason"] = "历史导入重复记录"
    try:
        first = db_client.post(f"/api/v1/housing/stays/{stay.id}/void-history", json=payload)
        assert first.status_code == 200
        db.execute(delete(UserRole).where(UserRole.user_id == actor.account.id))
        db.add(UserRole(user_id=actor.account.id, role_id=ROLES["admin"].id))
        db.commit()
        denied = db_client.post(f"/api/v1/housing/stays/{stay.id}/void-history", json=payload)
        assert denied.status_code == 403
        assert denied.json()["code"] == "permission_denied"
        assert counts(db, stay.id) == (1, 1)
        denials = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.resource_id == stay.id, AuditEvent.outcome == "denied"
                )
            )
        )
        assert len(denials) == 1
        assert denials[0].event_metadata == {"error_code": "permission_denied"}
        assert "历史导入" not in denied.text
    finally:
        db_client.app.dependency_overrides.pop(require_standard_account)


@pytest.mark.parametrize("denial", ["csrf", "password"])
def test_void_route_uses_current_csrf_and_standard_account(db_client, db, platform_users, denial):
    from uuid import uuid4

    identifier, password, _ = platform_users["credentials"]["admin"]
    login = db_client.post(
        "/api/v1/auth/login", json={"identifier": identifier, "password": password}
    )
    assert login.status_code == 200
    headers = {}
    if denial == "password":
        platform_users["accounts"]["admin"].force_password_change = True
        db.commit()
        headers["X-CSRF-Token"] = login.json()["csrf_token"]
    response = db_client.post(
        f"/api/v1/housing/stays/{uuid4()}/void-history", json={}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["code"] == (
        "csrf_invalid" if denial == "csrf" else "force_password_change_required"
    )
    assert response.headers["cache-control"] == "no-store, private"


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": "13812345678"},
        {"reason": "110105198001021023"},
        {"confirmed": False},
        {"expected_version": 0},
        {"unexpected": "invalid"},
    ],
)
def test_void_route_rejects_invalid_input_without_disclosure(db_client, db, changes):
    actor, _, stay = setup_history(db)
    db.commit()
    db_client.app.dependency_overrides[require_standard_account] = lambda: actor
    payload = command().model_dump(mode="json")
    payload["reason"] = "历史导入重复记录"
    payload.update(changes)
    try:
        response = db_client.post(f"/api/v1/housing/stays/{stay.id}/void-history", json=payload)
        assert response.status_code == 422
        assert "13812345678" not in response.text and "110105198001021023" not in response.text
        assert response.headers["cache-control"] == "no-store, private"
        assert counts(db, stay.id) == (0, 0)
    finally:
        db_client.app.dependency_overrides.pop(require_standard_account)
