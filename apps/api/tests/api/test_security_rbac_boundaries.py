from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService


def _sign_in(client: TestClient, platform_users, key: str) -> str:
    identifier, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login", json={"identifier": identifier, "password": password}
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _ordinary_role_manager(db: Session, platform_users) -> UserAccount:
    actor = platform_users["accounts"]["customer_service"]
    db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    db.add(UserRole(user_id=actor.id, role_id=ROLES["admin"].id))
    db.add(
        UserPermissionOverride(
            user_id=actor.id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.commit()
    assert not RbacService(db).account_has_role(actor.id, "super_admin")
    return actor


@pytest.mark.parametrize("target_has_role", [True, False])
def test_delegated_manager_cannot_remove_super_admin_role_even_when_absent(
    db_client: TestClient, platform_users, db: Session, target_has_role: bool
) -> None:
    # Removing the explicit root check must fail even with recovery available;
    # assignment absence must not change the authorization response.
    _ordinary_role_manager(db, platform_users)
    recovery = platform_users["accounts"]["coach"]
    db.add(UserRole(user_id=recovery.id, role_id=ROLES["super_admin"].id))
    db.commit()
    root = platform_users["accounts"]["admin"]
    service = RbacService(db)
    assert service.has_effective_recovery_super_admin([root])
    assert service.has_effective_recovery_super_admin([recovery])
    target = root if target_has_role else platform_users["accounts"]["self_student"]
    original_password = target.password_hash
    csrf = _sign_in(db_client, platform_users, "customer_service")

    response = db_client.delete(
        f"/api/v1/accounts/{target.id}/roles/{ROLES['super_admin'].id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_delegation_denied"
    db.expire_all()
    assert (db.get(UserRole, (target.id, ROLES["super_admin"].id)) is not None) == (
        target_has_role
    )
    assert db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.action == "account.role.remove",
            AuditEvent.resource_id == target.id,
            AuditEvent.outcome == "allowed",
        )
    ) is None
    if target_has_role:
        reset = db_client.post(
            f"/api/v1/accounts/{target.id}/reset-password",
            headers={"X-CSRF-Token": csrf},
        )
        assert reset.status_code == 403
        assert reset.json()["code"] == "super_admin_account_protected"
        assert "temporary_password" not in reset.json()
        db.refresh(target)
        assert target.password_hash == original_password


@pytest.mark.parametrize("target_has_role", [True, False])
def test_super_admin_can_remove_another_root_or_repeat_removal_with_audit(
    db_client: TestClient, platform_users, db: Session, target_has_role: bool
) -> None:
    target = platform_users["accounts"]["customer_service"]
    if target_has_role:
        db.add(UserRole(user_id=target.id, role_id=ROLES["super_admin"].id))
        db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")

    response = db_client.delete(
        f"/api/v1/accounts/{target.id}/roles/{ROLES['super_admin'].id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 204
    db.expire_all()
    assert db.get(UserRole, (target.id, ROLES["super_admin"].id)) is None
    assert db.get(UserRole, (target.id, ROLES["customer_service"].id)) is not None
    assert RbacService(db).has_effective_recovery_super_admin(
        [platform_users["accounts"]["admin"]]
    )
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "account.role.remove",
            AuditEvent.resource_id == target.id,
        )
    )
    assert audit is not None
    assert audit.actor_user_id == platform_users["accounts"]["admin"].id
    assert audit.outcome == "allowed"


def test_super_admin_cannot_remove_own_role_with_another_recovery_root(
    db_client: TestClient, platform_users, db: Session
) -> None:
    actor = platform_users["accounts"]["admin"]
    other = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=other.id, role_id=ROLES["super_admin"].id))
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")

    response = db_client.delete(
        f"/api/v1/accounts/{actor.id}/roles/{ROLES['super_admin'].id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin_protected"
    db.expire_all()
    assert db.get(UserRole, (actor.id, ROLES["super_admin"].id)) is not None


def test_super_admin_cannot_remove_last_effective_recovery_root(
    db_client: TestClient, platform_users, db: Session
) -> None:
    target = platform_users["accounts"]["admin"]
    actor = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=actor.id, role_id=ROLES["super_admin"].id))
    db.add(
        UserPermissionOverride(
            user_id=actor.id,
            permission_id=PERMISSIONS["accounts.manage"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.commit()
    assert not RbacService(db).has_effective_recovery_super_admin([actor])
    csrf = _sign_in(db_client, platform_users, "customer_service")

    response = db_client.delete(
        f"/api/v1/accounts/{target.id}/roles/{ROLES['super_admin'].id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin_protected"
    db.expire_all()
    assert db.get(UserRole, (target.id, ROLES["super_admin"].id)) is not None
    assert RbacService(db).has_effective_recovery_super_admin([target])
