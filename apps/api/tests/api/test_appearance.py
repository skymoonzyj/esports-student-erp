from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, update

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.rbac.models import RolePermission, UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES

PUBLIC = "/api/v1/appearance"
ADMIN = "/api/v1/operations/appearance"


def _events(db):
    return list(
        db.scalars(select(AuditEvent).where(AuditEvent.action == "operations.appearance.update"))
    )


def test_anonymous_appearance_is_only_public_fields_and_not_cached(db_client):
    response = db_client.get(PUBLIC)
    assert response.status_code == 200
    assert response.json() == {"theme": "clear", "revision": 1}
    assert "no-store" in response.headers["cache-control"]


def test_anonymous_admin_read_and_write_are_denied(db_client):
    for response in (
        db_client.get(ADMIN),
        db_client.put(ADMIN, json={"theme": "command", "expected_revision": 1}),
    ):
        assert response.status_code == 401
        assert "no-store" in response.headers["cache-control"]


def test_ordinary_admin_even_with_permission_override_cannot_manage(
    db_client, db, platform_users, login_as_admin
):
    actor = login_as_admin.account_id
    db.execute(delete(UserRole).where(UserRole.user_id == actor))
    db.add(UserRole(user_id=actor, role_id=ROLES["admin"].id))
    db.add(
        UserPermissionOverride(
            user_id=actor,
            permission_id=PERMISSIONS["operations.appearance.manage"].id,
            effect="allow",
            scope_kind="global",
            created_by_user_id=actor,
        )
    )
    db.commit()
    assert db_client.get(ADMIN).status_code == 403
    assert (
        db_client.put(
            ADMIN,
            json={"theme": "command", "expected_revision": 1},
            headers={"X-CSRF-Token": login_as_admin.csrf_token},
        ).status_code
        == 403
    )
    assert db_client.get(PUBLIC).json() == {"theme": "clear", "revision": 1}
    assert _events(db) == []


def test_superadmin_needs_dedicated_permission(db_client, db, login_as_admin):
    db.add(
        UserPermissionOverride(
            user_id=login_as_admin.account_id,
            permission_id=PERMISSIONS["operations.appearance.manage"].id,
            effect="deny",
            scope_kind="global",
            created_by_user_id=login_as_admin.account_id,
        )
    )
    db.commit()
    assert db_client.get(ADMIN).status_code == 403
    assert (
        db_client.put(
            ADMIN,
            json={"theme": "command", "expected_revision": 1},
            headers={"X-CSRF-Token": login_as_admin.csrf_token},
        ).status_code
        == 403
    )
    assert _events(db) == []


def test_appearance_write_requires_csrf(db_client, db, login_as_admin):
    response = db_client.put(ADMIN, json={"theme": "command", "expected_revision": 1})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"
    assert db_client.get(PUBLIC).json() == {"theme": "clear", "revision": 1}
    assert _events(db) == []


def test_save_publishes_global_theme_once_and_stale_revision_conflicts(
    db_client, db, login_as_admin
):
    headers = {"X-CSRF-Token": login_as_admin.csrf_token}
    assert db_client.get(ADMIN).json() == {"theme": "clear", "revision": 1}
    response = db_client.put(
        ADMIN, json={"theme": "command", "expected_revision": 1}, headers=headers
    )
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.json() == {"theme": "command", "revision": 2}
    assert db_client.get(PUBLIC).json() == {"theme": "command", "revision": 2}
    event = _events(db)[0]
    assert event.actor_user_id == login_as_admin.account_id
    assert event.request_id == response.headers["x-request-id"]
    assert event.event_metadata == {
        "old_theme": "clear",
        "new_theme": "command",
        "old_version": 1,
        "new_version": 2,
    }
    assert AuditService.public_metadata(event.event_metadata) == event.event_metadata
    unchanged = db_client.put(
        ADMIN, json={"theme": "command", "expected_revision": 2}, headers=headers
    )
    assert unchanged.json() == {"theme": "command", "revision": 2}
    assert len(_events(db)) == 1
    stale = db_client.put(ADMIN, json={"theme": "clear", "expected_revision": 1}, headers=headers)
    assert stale.status_code == 409
    assert stale.json()["code"] == "appearance_revision_conflict"
    assert "no-store" in stale.headers["cache-control"]
    assert db_client.get(PUBLIC).json() == {"theme": "command", "revision": 2}
    assert len(_events(db)) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"theme": "<script>private-marker</script>", "expected_revision": 1},
        {"theme": "https://private-marker.invalid/style.css", "expected_revision": 1},
        {"theme": "command", "expected_revision": 0},
        {"theme": "command", "expected_revision": True},
        {"theme": "command", "expected_revision": "1"},
        {"theme": "command", "expected_revision": 1, "private-marker": "private-marker"},
    ],
)
def test_invalid_appearance_payload_is_safe_422(db_client, db, login_as_admin, payload):
    response = db_client.put(
        ADMIN, json=payload, headers={"X-CSRF-Token": login_as_admin.csrf_token}
    )
    assert response.status_code == 422
    assert "private-marker" not in response.text
    assert "no-store" in response.headers["cache-control"]
    assert db_client.get(PUBLIC).json() == {"theme": "clear", "revision": 1}
    assert _events(db) == []


@pytest.mark.parametrize("revocation", ["disabled", "session", "role", "permission", "password"])
def test_service_rechecks_stale_actor_at_write_boundary(
    db, platform_users, login_as_admin, revocation
):
    from app.appearance.schemas import AppearanceUpdate
    from app.appearance.service import AppearanceService

    account = platform_users["accounts"]["admin"]
    login = db.scalar(
        select(AuthSession).where(
            AuthSession.user_id == account.id, AuthSession.revoked_at.is_(None)
        )
    )
    context = AuthContext(account=account, session=login, person=platform_users["people"]["admin"])
    if revocation == "disabled":
        db.execute(
            update(UserAccount)
            .where(UserAccount.id == account.id)
            .values(status="disabled")
            .execution_options(synchronize_session=False)
        )
    elif revocation == "password":
        db.execute(
            update(UserAccount)
            .where(UserAccount.id == account.id)
            .values(force_password_change=True)
            .execution_options(synchronize_session=False)
        )
    elif revocation == "session":
        db.execute(
            update(AuthSession)
            .where(AuthSession.id == login.id)
            .values(revoked_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
    elif revocation == "role":
        db.execute(delete(UserRole).where(UserRole.user_id == account.id))
    else:
        db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == ROLES["super_admin"].id,
                RolePermission.permission_id == PERMISSIONS["operations.appearance.manage"].id,
            )
        )
    # Keep the old ORM actor intact: the service must reread database truth.
    with pytest.raises(ProblemError) as raised:
        AppearanceService(db).update(
            context,
            AppearanceUpdate(theme="command", expected_revision=1),
            request_id="appearance-stale-actor",
        )
    assert raised.value.status in {401, 403}
    assert AppearanceService(db).read().model_dump() == {"theme": "clear", "revision": 1}
    assert _events(db) == []


def test_audit_failure_rolls_back_theme_and_revision(
    db, platform_users, login_as_admin, monkeypatch
):
    from app.appearance.schemas import AppearanceUpdate
    from app.appearance.service import AppearanceService

    account = platform_users["accounts"]["admin"]
    login = db.scalar(
        select(AuthSession).where(
            AuthSession.user_id == account.id, AuthSession.revoked_at.is_(None)
        )
    )
    context = AuthContext(account=account, session=login, person=platform_users["people"]["admin"])

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        AppearanceService(db).update(
            context,
            AppearanceUpdate(theme="command", expected_revision=1),
            request_id="appearance-rollback",
        )
    assert AppearanceService(db).read().model_dump() == {"theme": "clear", "revision": 1}
    assert _events(db) == []
