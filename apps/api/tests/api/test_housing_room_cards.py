import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import Session

from app.housing.models import HousingRoom, HousingStay
from app.housing.seed_inventory import INITIAL_ROOMS
from app.rbac.models import RolePermission, UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from tests.db.test_housing_cleaning_concurrency import (
    committed_cleaning_room as _committed_cleaning_room,
)


def test_room_card_routes_require_authentication(client):
    response = client.get(f"/api/v1/housing/rooms/{uuid4()}/room-cards")
    assert response.status_code == 401


def login(client, users, key="admin"):
    phone, password, _ = users["credentials"][key]
    response = client.post("/api/v1/auth/login", json={"identifier": phone, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


@pytest.fixture
def card_stay(db, platform_users):
    room = db.get(HousingRoom, INITIAL_ROOMS[0].id)
    now = datetime.now(UTC)
    stay = HousingStay(
        person_id=platform_users["people"]["self_student"].id,
        room_id=room.id,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=now - timedelta(days=3),
        actual_check_in_at=now - timedelta(days=3),
        status="checked_in",
        room_name_snapshot=room.room_number,
        floor_name_snapshot="测试楼层",
        building_name_snapshot="测试楼栋",
        created_by_user_id=platform_users["accounts"]["admin"].id,
        checked_in_by_user_id=platform_users["accounts"]["admin"].id,
    )
    db.add(stay)
    db.commit()
    return stay


def change_admin_role(db, users, role):
    account_id = users["accounts"]["admin"].id
    db.execute(delete(UserRole).where(UserRole.user_id == account_id))
    db.add(UserRole(user_id=account_id, role_id=ROLES[role].id))
    db.commit()


@pytest.mark.parametrize("role", ["super_admin", "administration", "property"])
def test_authorized_roles_save_dates_directly(db_client, db, platform_users, card_stay, role):
    change_admin_role(db, platform_users, role)
    response = db_client.put(
        f"/api/v1/housing/stays/{card_stay.id}/room-card",
        headers=login(db_client, platform_users),
        json={"issued_on": "2026-11-05", "expires_on": "2026-11-12", "expected_version": 0},
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert response.json()["can_edit"] is True
    assert response.headers["cache-control"] == "no-store, private"
    from app.audit.models import AuditEvent

    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "housing.room_card.save"))
    assert audit is not None


@pytest.mark.parametrize("role", ["admin", "coach"])
def test_other_roles_cannot_save_even_when_they_manage_housing(
    db_client, db, platform_users, card_stay, role
):
    change_admin_role(db, platform_users, role)
    response = db_client.put(
        f"/api/v1/housing/stays/{card_stay.id}/room-card",
        headers=login(db_client, platform_users),
        json={"issued_on": "2026-11-05", "expires_on": "2026-11-12", "expected_version": 0},
    )
    assert response.status_code == 403, response.text


def test_self_cannot_save_dates(db_client, platform_users, card_stay):
    response = db_client.put(
        f"/api/v1/housing/stays/{card_stay.id}/room-card",
        headers=login(db_client, platform_users, "self_student"),
        json={"issued_on": "2026-11-05", "expires_on": "2026-11-12", "expected_version": 0},
    )
    assert response.status_code == 403


def test_versions_clear_and_terminal_stay(db_client, db, platform_users, card_stay):
    from app.housing.room_card_models import HousingRoomCardRecord

    headers = login(db_client, platform_users)
    url = f"/api/v1/housing/stays/{card_stay.id}/room-card"
    payload = {"issued_on": "2026-11-05", "expires_on": "2026-11-12"}
    assert db_client.put(url, headers=headers, json=payload).status_code == 409
    assert (
        db_client.put(url, headers=headers, json={**payload, "expected_version": 0}).status_code
        == 200
    )
    assert (
        db_client.put(url, headers=headers, json={**payload, "expected_version": 0}).status_code
        == 409
    )
    clear = db_client.put(
        url, headers=headers, json={"issued_on": None, "expires_on": None, "expected_version": 1}
    )
    assert clear.status_code == 200, clear.text
    assert clear.json()["version"] == 2 and clear.json()["expires_on"] is None
    actor_id = platform_users["accounts"]["admin"].id
    card_stay.status = "checked_out"
    card_stay.actual_check_out_at = datetime.now(UTC)
    card_stay.checked_out_by_user_id = actor_id
    db.commit()
    assert (
        db_client.put(url, headers=headers, json={**payload, "expected_version": 2}).status_code
        == 409
    )
    db.expire_all()
    assert db.get(HousingRoomCardRecord, card_stay.id).version == 2


def test_room_list_is_current_and_read_only_for_admin(db_client, db, platform_users, card_stay):
    change_admin_role(db, platform_users, "admin")
    login(db_client, platform_users)
    response = db_client.get(f"/api/v1/housing/rooms/{card_stay.room_id}/room-cards")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["version"] == 0
    assert response.json()["items"][0]["can_edit"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"issued_on": "2026-11-05", "expires_on": None, "expected_version": 0},
        {"issued_on": "2026-11-06", "expires_on": "2026-11-05", "expected_version": 0},
    ],
)
def test_bad_date_requests_do_not_create_cards(db_client, platform_users, card_stay, payload):
    response = db_client.put(
        f"/api/v1/housing/stays/{card_stay.id}/room-card",
        headers=login(db_client, platform_users),
        json=payload,
    )
    assert response.status_code == 422, response.text


def test_keycard_explicit_deny_removes_edit_and_write_authority(
    db_client, db, platform_users, card_stay
):
    room = db.get(HousingRoom, card_stay.room_id)
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["housing.keycards.manage"].id,
            effect="deny",
            scope_kind="floor",
            scope_id=room.floor_id,
        )
    )
    db.commit()
    headers = login(db_client, platform_users)
    response = db_client.get(f"/api/v1/housing/rooms/{room.id}/room-cards")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["can_edit"] is False
    save = db_client.put(
        f"/api/v1/housing/stays/{card_stay.id}/room-card",
        headers=headers,
        json={"issued_on": "2026-11-05", "expires_on": "2026-11-12", "expected_version": 0},
    )
    assert save.status_code == 403, save.text


def test_role_gate_rejects_independent_keycard_grant_for_admin(
    db_client, db, platform_users, card_stay
):
    change_admin_role(db, platform_users, "admin")
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["housing.keycards.manage"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.commit()
    response = db_client.put(
        f"/api/v1/housing/stays/{card_stay.id}/room-card",
        headers=login(db_client, platform_users),
        json={"issued_on": "2026-11-05", "expires_on": "2026-11-12", "expected_version": 0},
    )
    assert response.status_code == 403, response.text


def test_property_cannot_edit_room_outside_granted_building(
    db_client, db, platform_users, card_stay
):
    change_admin_role(db, platform_users, "property")
    permissions = [PERMISSIONS[key].id for key in ("housing.read", "housing.keycards.manage")]
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == ROLES["property"].id,
            RolePermission.permission_id.in_(permissions),
        )
    )
    other_building = next(
        room.building_id
        for room in INITIAL_ROOMS
        if room.building_id != db.get(HousingRoom, card_stay.room_id).building_id
    )
    for permission_id in permissions:
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"]["admin"].id,
                permission_id=permission_id,
                effect="allow",
                scope_kind="building",
                scope_id=other_building,
            )
        )
    db.commit()
    headers = login(db_client, platform_users)
    assert db_client.get(f"/api/v1/housing/rooms/{card_stay.room_id}/room-cards").status_code == 404
    response = db_client.put(
        f"/api/v1/housing/stays/{card_stay.id}/room-card",
        headers=headers,
        json={"issued_on": "2026-11-05", "expires_on": "2026-11-12", "expected_version": 0},
    )
    assert response.status_code == 403, response.text


@pytest.fixture
def committed_card_room(postgres_engine):
    yield from _committed_cleaning_room.__wrapped__(postgres_engine)


def test_concurrent_first_card_save_has_exactly_one_winner(postgres_engine, committed_card_room):
    from app.core.errors import ProblemError
    from app.housing.room_card_models import HousingRoomCardRecord
    from app.housing.room_card_schemas import RoomCardSave
    from app.housing.room_card_service import HousingRoomCardService

    actor, _room_id, stay_id, _planned_id = committed_card_room
    barrier = Barrier(2)

    def worker(index):
        with Session(postgres_engine) as session:
            barrier.wait(timeout=10)
            try:
                with session.begin():
                    result, _, _ = HousingRoomCardService(session).save(
                        stay_id,
                        RoomCardSave(
                            issued_on=date(2026, 11, 5),
                            expires_on=date(2026, 11, 12 + index),
                            expected_version=0,
                        ),
                        actor_user_id=actor,
                        observed_at=datetime.now(UTC),
                        authorize=lambda room: None,
                    )
                return result.version
            except ProblemError as exc:
                return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, (0, 1)))
        assert results.count(1) == 1
        assert results.count("housing_room_card_stale_version") == 1
        with Session(postgres_engine) as observer:
            assert observer.get(HousingRoomCardRecord, stay_id).version == 1
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(HousingRoomCardRecord).where(HousingRoomCardRecord.stay_id == stay_id)
            )


def test_room_card_downgrade_holds_exclusive_lock_before_data_guard(
    db, platform_users, card_stay, monkeypatch
):
    from app.housing.room_card_models import HousingRoomCardRecord

    db.add(
        HousingRoomCardRecord(
            stay_id=card_stay.id,
            issued_on=date(2026, 11, 5),
            expires_on=date(2026, 11, 12),
            version=1,
            updated_by_user_id=platform_users["accounts"]["admin"].id,
        )
    )
    db.flush()
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0082_housing_room_cards.py"
    spec = importlib.util.spec_from_file_location("card_downgrade_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connection = db.connection()
    monkeypatch.setattr(module.op, "get_bind", lambda: connection)
    guard_locks = []

    def observe_guard(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT EXISTS (SELECT 1 FROM housing_room_cards)"):
            guard_locks.extend(
                conn.scalars(
                    text(
                        "SELECT mode FROM pg_locks WHERE pid = pg_backend_pid() "
                        "AND relation = 'housing_room_cards'::regclass AND granted"
                    )
                )
            )

    event.listen(connection, "after_cursor_execute", observe_guard)
    try:
        with pytest.raises(RuntimeError, match="禁止静默降级"):
            module.downgrade()
    finally:
        event.remove(connection, "after_cursor_execute", observe_guard)
    assert "AccessExclusiveLock" in guard_locks
    assert db.get(HousingRoomCardRecord, card_stay.id).version == 1
