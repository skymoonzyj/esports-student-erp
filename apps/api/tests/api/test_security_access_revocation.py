from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.rbac.service import RbacService
from app.travel_reports.service import TravelReportService
from tests.api.test_travel_reports import _login, _source_workbook


def _revoke(db: Session, actor_id, permission: str, kind: str) -> None:
    # SQL updates intentionally bypass the identity map: the request still holds
    # its previously authenticated objects, just as after an external commit.
    now = datetime.now(UTC)
    if kind == "permission":
        db.add(UserPermissionOverride(
            user_id=actor_id,
            permission_id=PERMISSIONS[permission].id,
            effect="deny",
            scope_kind="global",
        ))
        db.flush()
    elif kind == "session":
        db.execute(update(AuthSession).where(AuthSession.user_id == actor_id).values(
            revoked_at=now,
        ).execution_options(synchronize_session=False))
    elif kind == "expired_session":
        db.execute(update(AuthSession).where(AuthSession.user_id == actor_id).values(
            expires_at=now - timedelta(seconds=1),
        ).execution_options(synchronize_session=False))
    elif kind == "account":
        db.execute(update(UserAccount).where(UserAccount.id == actor_id).values(
            status="disabled",
        ).execution_options(synchronize_session=False))
    elif kind == "password_change":
        db.execute(update(UserAccount).where(UserAccount.id == actor_id).values(
            force_password_change=True,
        ).execution_options(synchronize_session=False))


@pytest.mark.parametrize("change, expected_status", [
    ("permission", 403), ("session", 401), ("expired_session", 401),
    ("account", 401), ("password_change", 403), ("unchanged", 204),
])
def test_override_delete_rechecks_access_after_override_lock(
    db_client: TestClient, db: Session, platform_users, monkeypatch,
    change: str, expected_status: int,
) -> None:
    actor = platform_users["accounts"]["customer_service"]
    target = platform_users["accounts"]["coach"]
    actor_id, target_id = actor.id, target.id
    db.execute(delete(UserRole).where(UserRole.user_id == actor_id))
    db.add(UserPermissionOverride(
        user_id=actor_id, permission_id=PERMISSIONS["permissions.manage"].id,
        effect="allow", scope_kind="person", scope_id=target.person_id,
    ))
    override = UserPermissionOverride(
        user_id=target_id, permission_id=PERMISSIONS["travel_reports.read"].id,
        effect="allow", scope_kind="global",
    )
    db.add(override)
    db.commit()
    override_id = override.id
    login = _login(db_client, platform_users, "customer_service")
    original = RbacService._require_permission
    reads = 0

    def require_after_lock(service, permission_id):
        nonlocal reads
        permission = original(service, permission_id)
        if permission_id == PERMISSIONS["travel_reports.read"].id:
            reads += 1
            if reads == 2:
                # The second read is immediately after SELECT override FOR UPDATE.
                # Only schedule the state change; authorization/deletion stay real.
                _revoke(service.db, actor_id, "permissions.manage", change)
        return permission

    monkeypatch.setattr(RbacService, "_require_permission", require_after_lock)
    response = db_client.delete(
        f"/api/v1/accounts/{target_id}/permission-overrides/{override_id}",
        headers={"X-CSRF-Token": login["csrf_token"]},
    )

    assert response.status_code == expected_status
    db.expire_all()
    assert (db.get(UserPermissionOverride, override_id) is not None) == (change != "unchanged")
    audit = db.scalar(select(AuditEvent.id).where(
        AuditEvent.action == "account.permission_override.remove",
        AuditEvent.resource_id == target_id,
        AuditEvent.outcome == "allowed",
    ))
    assert (audit is not None) == (change == "unchanged")


@pytest.mark.parametrize("content_kind", ["source", "converted"])
@pytest.mark.parametrize("change, expected_status", [
    ("permission", 403), ("session", 401), ("expired_session", 401),
    ("account", 401), ("password_change", 403), ("unchanged", 200),
])
def test_travel_download_rechecks_access_after_retrieval(
    db_client: TestClient, db: Session, platform_users, monkeypatch,
    content_kind: str, change: str, expected_status: int,
) -> None:
    actor_id = platform_users["accounts"]["admin"].id
    login = _login(db_client, platform_users, "admin")
    imported = db_client.post(
        "/api/v1/travel-reports/imports",
        headers={"X-CSRF-Token": login["csrf_token"]},
        files={"file": ("synthetic-billing.xlsx", _source_workbook())},
    )
    assert imported.status_code == 201
    report_id = imported.json()["id"]
    original = TravelReportService.get

    def retrieve_then_revoke(service, *args, **kwargs):
        report = original(service, *args, **kwargs)
        _revoke(service.db, actor_id, "travel_reports.export", change)
        return report

    monkeypatch.setattr(TravelReportService, "get", retrieve_then_revoke)
    response = db_client.get(f"/api/v1/travel-reports/{report_id}/{content_kind}")

    assert response.status_code == expected_status
    if change == "unchanged":
        assert response.content.startswith(b"PK")
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["x-content-type-options"] == "nosniff"
    else:
        assert not response.content.startswith(b"PK")
        assert "application/vnd.openxmlformats" not in response.headers["content-type"]
    assert (db.scalar(select(AuditEvent.id).where(
        AuditEvent.action == f"travel_report.{content_kind}.download",
        AuditEvent.resource_id == report_id,
        AuditEvent.outcome == "allowed",
    )) is not None) == (change == "unchanged")
