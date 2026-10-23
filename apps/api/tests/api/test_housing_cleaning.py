from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select, update

from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.models import AuthSession, UserAccount
from app.housing.models import HousingRoom
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from tests.api.test_housing_occupancy import _sign_in
from tests.db.test_housing_transfer_checkout import _room


@pytest.fixture
def care_room(db):
    room = _room(db, capacity=3)
    room.cleaning_status = "pending"
    room.cleaning_pending_at = datetime.now(UTC)
    db.commit()
    return room


def _complete(client, room, csrf, version=None):
    return client.post(
        f"/api/v1/housing/rooms/{room.id}/cleaning/complete",
        json={"version": room.version if version is None else version},
        headers={"x-csrf-token": csrf},
    )


@pytest.mark.parametrize("role", ["admin", "administration", "super_admin"])
def test_actual_care_roles_complete_once(db_client, db, platform_users, care_room, role):
    actor = platform_users["accounts"]["admin"]
    db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    db.add(UserRole(user_id=actor.id, role_id=ROLES[role].id))
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")
    response = _complete(db_client, care_room, csrf)
    assert response.status_code == 200, response.text
    assert response.json()["cleaning_status"] == "ready"
    assert response.json()["version"] == 2
    assert response.json()["cleaning_pending_at"] is not None
    assert response.json()["cleaning_completed_at"] is not None
    replay = _complete(db_client, care_room, csrf, version=2)
    assert replay.status_code == 200, replay.text
    assert replay.json()["version"] == 2
    audits = list(db.scalars(select(AuditEvent).where(
        AuditEvent.action == "housing.room.cleaning.complete",
        AuditEvent.resource_id == care_room.id,
        AuditEvent.outcome == "allowed",
    )))
    assert len(audits) == 1


@pytest.mark.parametrize("role", ["coach", "team_manager", "customer_service"])
def test_profile_or_permission_override_is_not_care_role(
    db_client, db, platform_users, care_room, role
):
    actor = platform_users["accounts"]["admin"]  # profile remains administration
    db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    db.add(UserRole(user_id=actor.id, role_id=ROLES[role].id))
    for permission in ("housing.read", "housing.rooms.manage"):
        db.add(UserPermissionOverride(
            id=uuid4(), user_id=actor.id, permission_id=PERMISSIONS[permission].id,
            effect="allow", scope_kind="global",
        ))
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")
    assert _complete(db_client, care_room, csrf).status_code in (403, 404)
    db.expire_all()
    assert db.get(HousingRoom, care_room.id).cleaning_status == "pending"


def test_scope_deny_overrides_actual_care_role(db_client, db, platform_users, care_room):
    actor = platform_users["accounts"]["admin"]
    db.add(UserPermissionOverride(
        id=uuid4(), user_id=actor.id, permission_id=PERMISSIONS["housing.rooms.manage"].id,
        effect="deny", scope_kind="floor", scope_id=care_room.floor_id,
    ))
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")
    assert _complete(db_client, care_room, csrf).status_code == 403


@pytest.mark.parametrize("revocation", ["role", "account", "session", "deny"])
def test_care_rechecks_authority_after_room_lock(
    db_client, db, platform_users, care_room, revocation
):
    actor_id = platform_users["accounts"]["admin"].id
    csrf = _sign_in(db_client, platform_users, "admin")
    fired = False

    def revoke(_conn, _cursor, statement, _params, _ctx, _many):
        nonlocal fired
        if fired or "FROM housing_rooms" not in statement or "FOR UPDATE" not in statement:
            return
        fired = True
        if revocation == "role":
            db.execute(delete(UserRole).where(UserRole.user_id == actor_id))
        elif revocation == "account":
            db.get(UserAccount, actor_id).status = "disabled"
        elif revocation == "session":
            for session in db.scalars(select(AuthSession).where(AuthSession.user_id == actor_id)):
                session.revoked_at = datetime.now(UTC)
        else:
            db.add(UserPermissionOverride(
                id=uuid4(), user_id=actor_id,
                permission_id=PERMISSIONS["housing.rooms.manage"].id,
                effect="deny", scope_kind="global",
            ))
        db.flush()

    engine = db.get_bind()
    event.listen(engine, "after_cursor_execute", revoke)
    try:
        response = _complete(db_client, care_room, csrf)
    finally:
        event.remove(engine, "after_cursor_execute", revoke)
    assert fired
    assert response.status_code in (403, 404), response.text
    db.expire_all()
    assert db.get(HousingRoom, care_room.id).cleaning_status == "pending"


def test_daily_cleaning_keeps_verification_and_service_status(
    db_client, db, platform_users, care_room
):
    care_room.verification_status = "pending_verification"
    care_room.service_status = "maintenance"
    db.commit()
    csrf = _sign_in(db_client, platform_users, "admin")
    response = db_client.patch(
        f"/api/v1/housing/rooms/{care_room.id}",
        headers={"x-csrf-token": csrf},
        json={"version": 1, "daily_cleaning_enabled": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["daily_cleaning_enabled"] is True
    assert response.json()["verification_status"] == "pending_verification"
    assert response.json()["service_status"] == "maintenance"
    assert response.json()["cleaning_status"] == "pending"
    audit = db.scalar(select(AuditEvent).where(
        AuditEvent.action == "housing.room.update",
        AuditEvent.resource_id == care_room.id,
        AuditEvent.outcome == "allowed",
    ))
    assert audit.event_metadata == {
        "room_id": str(care_room.id),
        "previous_daily_cleaning_enabled": False,
        "daily_cleaning_enabled": True,
    }


@pytest.mark.parametrize("daily", [None, False])
def test_room_update_does_not_invent_daily_cleaning_change(
    db_client, db, platform_users, care_room, daily
):
    csrf = _sign_in(db_client, platform_users, "admin")
    payload = {"version": 1, "notes": "虚拟房间备注"}
    if daily is not None:
        payload["daily_cleaning_enabled"] = daily
    response = db_client.patch(
        f"/api/v1/housing/rooms/{care_room.id}",
        headers={"x-csrf-token": csrf}, json=payload,
    )
    assert response.status_code == 200, response.text
    audit = db.scalar(select(AuditEvent).where(
        AuditEvent.action == "housing.room.update",
        AuditEvent.resource_id == care_room.id,
        AuditEvent.outcome == "allowed",
    ))
    assert audit.event_metadata == {"room_id": str(care_room.id)}


def test_daily_cleaning_audit_failure_rolls_back_setting(
    db_client, db, platform_users, care_room, monkeypatch
):
    import app.housing.router as inventory_router

    csrf = _sign_in(db_client, platform_users, "admin")
    original = inventory_router._audit

    def audit(*args, **kwargs):
        if kwargs["action"] == "housing.room.update" and kwargs["outcome"] == "allowed":
            raise AuditMetadataRejected("forced")
        return original(*args, **kwargs)

    monkeypatch.setattr(inventory_router, "_audit", audit)
    response = db_client.patch(
        f"/api/v1/housing/rooms/{care_room.id}",
        headers={"x-csrf-token": csrf},
        json={"version": 1, "daily_cleaning_enabled": True},
    )
    assert response.status_code == 500
    db.expire_all()
    room = db.get(HousingRoom, care_room.id)
    assert room.daily_cleaning_enabled is False
    assert room.version == 1


def test_daily_cleaning_audit_uses_locked_refresh_not_route_precheck(
    db_client, db, platform_users, care_room, monkeypatch
):
    from app.housing.inventory_service import HousingInventoryService

    csrf = _sign_in(db_client, platform_users, "admin")
    original = HousingInventoryService._locked_room

    def lock_after_change(service, room_id):
        # Model a value changed after the route precheck, before lock acquisition.
        service.db.execute(update(HousingRoom).where(HousingRoom.id == room_id).values(
            daily_cleaning_enabled=True,
        ).execution_options(synchronize_session=False))
        return original(service, room_id)

    monkeypatch.setattr(HousingInventoryService, "_locked_room", lock_after_change)
    response = db_client.patch(
        f"/api/v1/housing/rooms/{care_room.id}",
        headers={"x-csrf-token": csrf},
        json={"version": 1, "daily_cleaning_enabled": False},
    )
    assert response.status_code == 200, response.text
    audit = db.scalar(select(AuditEvent).where(
        AuditEvent.action == "housing.room.update",
        AuditEvent.resource_id == care_room.id,
        AuditEvent.outcome == "allowed",
    ))
    assert audit.event_metadata == {
        "room_id": str(care_room.id),
        "previous_daily_cleaning_enabled": True,
        "daily_cleaning_enabled": False,
    }


def test_audit_exception_rolls_back_cleaning(db_client, db, platform_users, care_room, monkeypatch):
    import app.housing.occupancy_router as occupancy

    csrf = _sign_in(db_client, platform_users, "admin")
    original = occupancy._audit

    def audit(*args, **kwargs):
        if kwargs["action"] == "housing.room.cleaning.complete" and kwargs["outcome"] == "allowed":
            raise AuditMetadataRejected("forced")
        return original(*args, **kwargs)

    monkeypatch.setattr(occupancy, "_audit", audit)
    response = _complete(db_client, care_room, csrf)
    assert response.status_code == 500, response.text
    db.expire_all()
    room = db.get(HousingRoom, care_room.id)
    assert room.cleaning_status == "pending"
    assert room.version == 1


def test_overview_cleaning_filters_and_summary(db_client, db, platform_users, care_room):
    care_room.daily_cleaning_enabled = True
    db.commit()
    _sign_in(db_client, platform_users, "admin")
    response = db_client.get("/api/v1/housing/overview", params={
        "cleaning_statuses": "pending", "daily_cleaning_enabled": "true",
        "building_id": str(care_room.building_id),
    })
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["pending_cleaning_rooms"] == 1
    assert [room["id"] for room in response.json()["rooms"]] == [str(care_room.id)]
    assert response.json()["rooms"][0]["is_assignable"] is False
    assert response.json()["rooms"][0]["allowed_actions"]["complete_cleaning"] is True
    empty = db_client.get("/api/v1/housing/overview", params={
        "cleaning_statuses": "ready", "building_id": str(care_room.building_id),
    })
    assert empty.json()["rooms"] == []


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("operation", ["create", "update"])
def test_daily_field_is_role_protected_but_legacy_omission_is_preserved(
    db_client, db, platform_users, care_room, explicit, operation
):
    actor = platform_users["accounts"]["customer_service"]
    for permission in ("housing.read", "housing.rooms.manage"):
        db.add(UserPermissionOverride(
            id=uuid4(), user_id=actor.id, permission_id=PERMISSIONS[permission].id,
            effect="allow", scope_kind="global",
        ))
    db.commit()
    csrf = _sign_in(db_client, platform_users, "customer_service")
    headers = {"x-csrf-token": csrf}
    payload = {"daily_cleaning_enabled": False} if explicit else {}
    if operation == "create":
        payload.update(floor_id=str(care_room.floor_id), room_number="LEGACY-TEST",
                       room_type="double", capacity=2)
        response = db_client.post("/api/v1/housing/rooms", json=payload, headers=headers)
        expected = 201
    else:
        payload.update(version=care_room.version, notes="legacy update")
        response = db_client.patch(f"/api/v1/housing/rooms/{care_room.id}",
                                   json=payload, headers=headers)
        expected = 200
    assert response.status_code == (403 if explicit else expected), response.text


def test_new_room_daily_option_and_floor_care_flag(db_client, db, platform_users, care_room):
    csrf = _sign_in(db_client, platform_users, "admin")
    catalog = db_client.get("/api/v1/housing/catalog").json()
    floor = next(item for item in catalog["floors"] if item["id"] == str(care_room.floor_id))
    assert "manage_care" in floor["allowed_actions"]
    response = db_client.post("/api/v1/housing/rooms", headers={"x-csrf-token": csrf}, json={
        "floor_id": str(care_room.floor_id), "room_number": "DAILY-NEW",
        "room_type": "double", "capacity": 2, "daily_cleaning_enabled": True,
    })
    assert response.status_code == 201, response.text
    assert response.json()["daily_cleaning_enabled"] is True
