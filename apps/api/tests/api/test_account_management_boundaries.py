from __future__ import annotations

import hashlib
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import admin_router
from app.auth.models import AuthSession, UserAccount
from app.identity.models import Person
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


def _ordinary_admin(db: Session, platform_users) -> UserAccount:
    actor = platform_users["accounts"]["customer_service"]
    db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    db.add(UserRole(user_id=actor.id, role_id=ROLES["admin"].id))
    db.commit()
    assert not RbacService(db).account_has_role(actor.id, "super_admin")
    return actor


def _mutation(client: TestClient, csrf: str, target: UserAccount, action: str) -> Response:
    path = f"/api/v1/accounts/{target.id}"
    headers = {"X-CSRF-Token": csrf}
    if action == "delete":
        return client.delete(path, headers=headers)
    return client.post(f"{path}/{action}", headers=headers)


def _password_fingerprint(account: UserAccount) -> str:
    return hashlib.sha256(account.password_hash.encode()).hexdigest()


@pytest.mark.parametrize("action", ["reset-password", "disable", "enable", "delete"])
def test_ordinary_admin_cannot_manage_super_admin_account(
    db_client: TestClient,
    platform_users,
    db: Session,
    action: str,
) -> None:
    _ordinary_admin(db, platform_users)
    target = platform_users["accounts"]["admin"]
    _sign_in(db_client, platform_users, "admin")
    if action == "enable":
        target.status = "disabled"
        db.commit()
    original_status = target.status
    original_password = _password_fingerprint(target)
    original_sessions = list(db.execute(
        select(AuthSession.id, AuthSession.revoked_at).where(AuthSession.user_id == target.id)
    ))
    csrf = _sign_in(db_client, platform_users, "customer_service")

    response = _mutation(db_client, csrf, target, action)

    assert response.status_code == 403
    assert response.json()["code"] == "super_admin_account_protected"
    assert "temporary_password" not in response.json()
    db.refresh(target)
    person = db.get(Person, target.person_id)
    assert person is not None
    db.refresh(person)
    assert target.status == original_status
    assert _password_fingerprint(target) == original_password
    assert person.deleted_at is None
    assert list(db.execute(
        select(AuthSession.id, AuthSession.revoked_at).where(AuthSession.user_id == target.id)
    )) == original_sessions


def test_ordinary_admin_keeps_normal_account_management(
    db_client: TestClient, platform_users, db: Session
) -> None:
    _ordinary_admin(db, platform_users)
    target = platform_users["accounts"]["self_student"]
    csrf = _sign_in(db_client, platform_users, "customer_service")
    for action in ("reset-password", "disable", "enable", "delete"):
        response = _mutation(db_client, csrf, target, action)
        assert response.status_code == (200 if action == "reset-password" else 204)


@pytest.mark.parametrize("action", ["reset-password", "disable", "enable", "delete"])
def test_super_admin_can_manage_another_super_admin(
    db_client: TestClient, platform_users, db: Session, action: str
) -> None:
    target = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=target.id, role_id=ROLES["super_admin"].id))
    if action == "enable":
        target.status = "disabled"
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")

    response = _mutation(db_client, csrf, target, action)

    assert response.status_code == (200 if action == "reset-password" else 204)
    assert db_client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("ineffective_other_admin", [False, True])
def test_last_effective_super_admin_cannot_be_deleted(
    db_client: TestClient, platform_users, db: Session, ineffective_other_admin: bool
) -> None:
    target = platform_users["accounts"]["admin"]
    if ineffective_other_admin:
        other = platform_users["accounts"]["customer_service"]
        db.add(UserRole(user_id=other.id, role_id=ROLES["super_admin"].id))
        db.add_all([
            UserPermissionOverride(
                user_id=other.id,
                permission_id=PERMISSIONS[key].id,
                effect="deny",
                scope_kind="global",
            )
            for key in ("accounts.manage", "permissions.manage")
        ])
        db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")

    response = _mutation(db_client, csrf, target, "delete")

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin_protected"
    db.refresh(target)
    person = db.get(Person, target.person_id)
    assert person is not None
    db.refresh(person)
    assert target.status == "active"
    assert person.deleted_at is None
    assert RbacService(db).has_effective_recovery_super_admin([target])
    assert db_client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("change", ["promote_target", "revoke_actor"])
def test_management_rechecks_roles_and_actor_authority_inside_write_transaction(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    actor = _ordinary_admin(db, platform_users)
    target = platform_users["accounts"]["self_student"]
    original_password = _password_fingerprint(target)
    csrf = _sign_in(db_client, platform_users, "customer_service")
    original: Callable = admin_router._require_manage

    def change_after_route_check(context, current_db, tokens):
        original(context, current_db, tokens)
        if change == "promote_target":
            current_db.add(UserRole(user_id=target.id, role_id=ROLES["super_admin"].id))
        else:
            current_db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
        current_db.flush()

    monkeypatch.setattr(admin_router, "_require_manage", change_after_route_check)
    response = _mutation(db_client, csrf, target, "reset-password")

    assert response.status_code == 403
    assert response.json()["code"] == (
        "super_admin_account_protected" if change == "promote_target" else "permission_denied"
    )
    db.refresh(target)
    assert _password_fingerprint(target) == original_password
