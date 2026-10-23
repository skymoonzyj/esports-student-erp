from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from starlette.requests import Request

import app.housing.api_support as housing_api_support
import app.housing.router as housing_router
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.housing.dependencies import get_housing_cipher, get_housing_lookup
from app.housing.errors import HousingInvalidRoomConfiguration
from app.housing.inventory_service import HousingInventoryService
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom
from app.housing.schemas import FloorCreate, RoomVersionInput
from app.housing.seed_inventory import FLOOR_SEEDS, INITIAL_BUILDINGS, INITIAL_ROOMS
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS

_BUILDING = INITIAL_BUILDINGS[0]
_BUILDING_FLOORS = tuple(
    floor for floor in FLOOR_SEEDS if floor.building_id == _BUILDING.id
)
_FLOOR = _BUILDING_FLOORS[0]
_OTHER_BUILDING = INITIAL_BUILDINGS[1]
_OTHER_ROOM = next(
    room for room in INITIAL_ROOMS if room.building_id == _OTHER_BUILDING.id
)
_FLOOR_ROOMS = tuple(room for room in INITIAL_ROOMS if room.floor_id == _FLOOR.id)
_SINGLE_ROOM = next(room for room in _FLOOR_ROOMS if room.room_type == "single")
_DOUBLE_ROOM = next(room for room in _FLOOR_ROOMS if room.room_type == "double")


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


@pytest.fixture
def scoped_housing_accounts(db: Session, platform_users):
    accounts = platform_users["accounts"]
    read_permission = PERMISSIONS["housing.read"].id
    manage_permission = PERMISSIONS["housing.rooms.manage"].id
    db.add_all(
        [
            UserPermissionOverride(
                user_id=accounts["customer_service"].id,
                permission_id=read_permission,
                effect="allow",
                scope_kind="floor",
                scope_id=_FLOOR.id,
            ),
            UserPermissionOverride(
                user_id=accounts["guardian"].id,
                permission_id=read_permission,
                effect="allow",
                scope_kind="building",
                scope_id=_BUILDING.id,
            ),
            UserPermissionOverride(
                user_id=accounts["guardian"].id,
                permission_id=manage_permission,
                effect="allow",
                scope_kind="building",
                scope_id=_BUILDING.id,
            ),
            UserPermissionOverride(
                user_id=accounts["minor_student"].id,
                permission_id=read_permission,
                effect="allow",
                scope_kind="building",
                scope_id=_BUILDING.id,
            ),
            UserPermissionOverride(
                user_id=accounts["minor_student"].id,
                permission_id=read_permission,
                effect="deny",
                scope_kind="floor",
                scope_id=_FLOOR.id,
            ),
        ]
    )
    db.commit()
    return accounts


def _assert_private(response) -> None:
    assert response.headers["cache-control"] == "no-store, private"


@pytest.fixture
def global_housing_reader(db: Session, platform_users):
    # Keep the global read-only assertions under genuine administrative
    # authority; a coach no longer provides an implicit global reader.
    for permission in (
        "housing.rooms.manage",
        "housing.stays.manage",
        "housing.transfers.manage",
        "housing.checkouts.manage",
        "housing.issues.manage",
    ):
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"]["admin"].id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="global",
            )
        )
    db.commit()


def test_housing_crypto_dependencies_use_application_settings(db_client: TestClient) -> None:
    request = Request({"type": "http", "app": db_client.app})

    cipher = get_housing_cipher(request)
    lookup = get_housing_lookup(request)
    encrypted = cipher.encrypt("13812348000", field_name="phone")

    assert cipher.decrypt(encrypted, field_name="phone") == "13812348000"
    assert lookup.digest("13812348000") == lookup.digest("13812348000")


def test_housing_collections_enforce_building_floor_and_deny_visibility(
    db_client: TestClient,
    platform_users,
    scoped_housing_accounts,
    global_housing_reader,
) -> None:
    sign_in(db_client, platform_users, "self_student")
    denied = db_client.get("/api/v1/housing/overview")
    denied_catalog = db_client.get("/api/v1/housing/catalog")
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"
    assert denied_catalog.status_code == 403
    assert denied_catalog.json()["code"] == "permission_denied"
    _assert_private(denied)
    _assert_private(denied_catalog)

    sign_in(db_client, platform_users, "coach")
    assert db_client.get("/api/v1/housing/catalog").status_code == 403
    assert db_client.get("/api/v1/housing/overview").status_code == 403
    sign_in(db_client, platform_users, "admin")
    building_catalog = db_client.get("/api/v1/housing/catalog")
    building_overview = db_client.get(
        "/api/v1/housing/overview",
        params={"page_size": 100},
    )
    assert building_catalog.status_code == 200
    assert [item["code"] for item in building_catalog.json()["buildings"]] == [
        "A",
        "4",
        "5",
        "9",
    ]
    assert {item["id"] for item in building_catalog.json()["buildings"]} == {
        str(building.id) for building in INITIAL_BUILDINGS
    }
    assert {item["building_id"] for item in building_catalog.json()["floors"]} == {
        str(building.id) for building in INITIAL_BUILDINGS
    }
    assert building_overview.json()["total"] == len(INITIAL_ROOMS)
    assert {item["building_id"] for item in building_overview.json()["rooms"]} <= {
        str(building.id) for building in INITIAL_BUILDINGS
    }
    assert all(
        item["allowed_actions"] == {
            "edit": False,
            "delete": False,
            "verify": False,
            "create_stay": False,
            "report_issue": False,
            "view_detail": False,
            "manage_care": False,
            "complete_cleaning": False,
            "manage_photos": False,
        }
        for item in building_overview.json()["rooms"]
    )

    sign_in(db_client, platform_users, "customer_service")
    floor_catalog = db_client.get("/api/v1/housing/catalog")
    floor_overview = db_client.get("/api/v1/housing/overview")
    assert [item["name"] for item in floor_catalog.json()["buildings"]] == ["4幢"]
    assert [item["id"] for item in floor_catalog.json()["floors"]] == [str(_FLOOR.id)]
    assert floor_overview.json()["total"] == len(_FLOOR_ROOMS)
    assert {item["floor_id"] for item in floor_overview.json()["rooms"]} == {
        str(_FLOOR.id)
    }

    sign_in(db_client, platform_users, "minor_student")
    deny_catalog = db_client.get("/api/v1/housing/catalog")
    deny_overview = db_client.get("/api/v1/housing/overview")
    assert str(_FLOOR.id) not in {
        item["id"] for item in deny_catalog.json()["floors"]
    }
    assert str(_FLOOR.id) not in {
        item["floor_id"] for item in deny_overview.json()["rooms"]
    }
    assert deny_overview.json()["total"] == 32

    for response in (
        building_catalog,
        building_overview,
        floor_catalog,
        floor_overview,
        deny_catalog,
        deny_overview,
    ):
        _assert_private(response)


def test_catalog_and_overview_share_access_revision_across_catalog_edits(
    db_client: TestClient,
    db: Session,
    platform_users,
    scoped_housing_accounts,
    global_housing_reader,
) -> None:
    sign_in(db_client, platform_users, "admin")

    catalog_before = db_client.get("/api/v1/housing/catalog")
    overview_before = db_client.get("/api/v1/housing/overview")
    assert catalog_before.status_code == overview_before.status_code == 200
    revision = catalog_before.json()["access_revision"]
    assert overview_before.json()["access_revision"] == revision

    building = db.get(HousingBuilding, _BUILDING.id)
    assert building is not None
    building.name = f"{building.name}-修订"
    db.commit()

    catalog_after = db_client.get("/api/v1/housing/catalog")
    overview_after = db_client.get("/api/v1/housing/overview")
    assert catalog_after.json()["access_revision"] == revision
    assert overview_after.json()["access_revision"] == revision

    temporary = HousingBuilding(
        code=f"T-{uuid4().hex[:8]}",
        name=f"临时楼栋-{uuid4().hex[:8]}",
        display_order=99_999,
    )
    db.add(temporary)
    db.commit()
    assert db_client.get("/api/v1/housing/catalog").json()["access_revision"] == revision
    assert db_client.get("/api/v1/housing/overview").json()["access_revision"] == revision

    db.delete(temporary)
    db.commit()
    assert db_client.get("/api/v1/housing/catalog").json()["access_revision"] == revision
    assert db_client.get("/api/v1/housing/overview").json()["access_revision"] == revision


def test_missing_and_out_of_scope_rooms_have_identical_private_404(
    db_client: TestClient,
    platform_users,
    scoped_housing_accounts,
) -> None:
    sign_in(db_client, platform_users, "customer_service")
    headers = {"X-Request-ID": "housing-private-not-found"}
    missing = db_client.get(
        "/api/v1/housing/rooms/52000000-0000-4000-8000-000000009999",
        headers=headers,
    )
    out_of_scope = db_client.get(
        f"/api/v1/housing/rooms/{_OTHER_ROOM.id}",
        headers=headers,
    )

    assert missing.status_code == out_of_scope.status_code == 404
    assert missing.json() == out_of_scope.json()
    assert missing.json()["code"] == "housing_resource_not_found"
    _assert_private(missing)
    _assert_private(out_of_scope)


def test_scoped_mutations_record_one_safe_allowed_denied_and_failed_audit(
    db_client: TestClient,
    db: Session,
    platform_users,
    scoped_housing_accounts,
) -> None:
    room = db.get(HousingRoom, _SINGLE_ROOM.id)
    assert room is not None

    reader_csrf = sign_in(db_client, platform_users, "customer_service")
    denied = db_client.patch(
        f"/api/v1/housing/rooms/{room.id}",
        headers={
            "X-CSRF-Token": reader_csrf,
            "X-Request-ID": "housing-room-denied",
        },
        json={"version": room.version, "notes": "不得写入的备注"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"

    manager_csrf = sign_in(db_client, platform_users, "guardian")
    allowed = db_client.patch(
        f"/api/v1/housing/rooms/{room.id}",
        headers={
            "X-CSRF-Token": manager_csrf,
            "X-Request-ID": "housing-room-allowed",
        },
        json={"version": room.version, "notes": "管理员核对"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["notes"] == "管理员核对"
    assert allowed.json()["version"] == 2

    failed = db_client.patch(
        f"/api/v1/housing/rooms/{room.id}",
        headers={
            "X-CSRF-Token": manager_csrf,
            "X-Request-ID": "housing-room-failed",
        },
        json={"version": 1, "notes": "陈旧修改"},
    )
    assert failed.status_code == 409
    assert failed.json()["code"] == "housing_stale_version"

    out_of_scope = db_client.patch(
        f"/api/v1/housing/rooms/{_OTHER_ROOM.id}",
        headers={
            "X-CSRF-Token": manager_csrf,
            "X-Request-ID": "housing-room-out-of-scope",
        },
        json={"version": 1, "notes": "越权修改"},
    )
    assert out_of_scope.status_code == 404
    assert out_of_scope.json()["code"] == "housing_resource_not_found"

    events = list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.request_id.in_(
                    [
                        "housing-room-denied",
                        "housing-room-allowed",
                        "housing-room-failed",
                        "housing-room-out-of-scope",
                    ]
                )
            )
            .order_by(AuditEvent.request_id)
        )
    )
    assert len(events) == 4
    by_request = {event.request_id: event for event in events}
    assert by_request["housing-room-denied"].outcome == "denied"
    assert by_request["housing-room-allowed"].outcome == "allowed"
    assert by_request["housing-room-failed"].outcome == "failed"
    assert by_request["housing-room-out-of-scope"].outcome == "denied"
    assert by_request["housing-room-allowed"].event_metadata == {
        "room_id": str(room.id)
    }
    assert by_request["housing-room-denied"].event_metadata == {
        "room_id": str(room.id),
        "error_code": "permission_denied",
    }
    assert by_request["housing-room-failed"].event_metadata == {
        "room_id": str(room.id),
        "error_code": "housing_stale_version",
    }
    assert all(
        "notes" not in event.event_metadata and "room_number" not in event.event_metadata
        for event in events
    )
    for response in (denied, allowed, failed, out_of_scope):
        _assert_private(response)


def test_initial_overview_truthfully_projects_static_empty_occupancy(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")
    response = db_client.get(
        "/api/v1/housing/overview",
        params={"page": 1, "page_size": 100},
    )

    assert response.status_code == 200
    assert response.json()["summary"] == {
        "total_rooms": 188,
        "occupied_rooms": 0,
        "unoccupied_rooms": 188,
        "assignable_rooms": 0,
        "unassignable_rooms": 188,
        "problem_rooms": 0,
        "pending_verification_rooms": 171,
        "pending_cleaning_rooms": 0,
        "total_capacity": 223,
        "current_occupants": 0,
        "remaining_capacity": 223,
        "occupancy_rate": "0.00",
        "single_rooms": 119,
        "double_rooms": 52,
        "triple_rooms": 0,
        "custom_rooms": 0,
        "functional_rooms": 17,
    }
    assert response.json()["total"] == 188
    assert all(
        room["current_occupants"] == 0
        and room["current_gender"] is None
        and room["has_future_reservation"] is False
        and room["occupancy_status"] == "unoccupied"
        for room in response.json()["rooms"]
    )
    assert all(room["is_assignable"] is False for room in response.json()["rooms"])
    _assert_private(response)

    catalog = db_client.get("/api/v1/housing/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["can_create_building"] is True
    assert all(
        building["allowed_actions"] == ["edit", "delete", "create_floor"]
        for building in catalog.json()["buildings"]
    )
    assert all(
        floor["allowed_actions"] == ["edit", "delete", "create_room", "manage_care"]
        for floor in catalog.json()["floors"]
    )
    _assert_private(catalog)

    for params in ({"page": 0}, {"page_size": 101}):
        invalid = db_client.get("/api/v1/housing/overview", params=params)
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "validation_error"
        _assert_private(invalid)


def test_overview_combines_access_filters_summary_and_pagination_from_one_set(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    actor_id = platform_users["accounts"]["admin"].id
    HousingInventoryService(db).verify_rooms(
        [
            RoomVersionInput(room_id=_SINGLE_ROOM.id, version=1),
            RoomVersionInput(room_id=_DOUBLE_ROOM.id, version=1),
        ],
        actor_user_id=actor_id,
    )
    sign_in(db_client, platform_users, "admin")
    params = [
        ("building_id", str(_BUILDING.id)),
        ("floor_id", str(_FLOOR.id)),
        ("query", "4-2"),
        ("room_type", "single"),
        ("room_type", "double"),
        ("verification_status", "verified"),
        ("service_status", "available"),
        ("current_gender", "unspecified"),
        ("occupancy", "unoccupied"),
        ("assignability", "assignable"),
        ("page", "1"),
        ("page_size", "1"),
    ]
    response = db_client.get("/api/v1/housing/overview", params=params)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["rooms"]) == 1
    assert body["summary"]["total_rooms"] == 2
    assert body["summary"]["assignable_rooms"] == 2
    assert body["summary"]["unoccupied_rooms"] == 2
    assert body["summary"]["total_capacity"] == 3
    assert body["summary"]["single_rooms"] == 1
    assert body["summary"]["double_rooms"] == 1
    assert datetime.fromisoformat(body["as_of"]).tzinfo is not None

    occupied = db_client.get(
        "/api/v1/housing/overview",
        params={"occupancy": "occupied"},
    )
    male = db_client.get(
        "/api/v1/housing/overview",
        params={"current_gender": "male"},
    )
    female = db_client.get(
        "/api/v1/housing/overview",
        params={"current_gender": "female"},
    )
    unspecified = db_client.get(
        "/api/v1/housing/overview",
        params={"current_gender": "unspecified", "page_size": 100},
    )
    assert occupied.json()["total"] == 0
    assert male.json()["total"] == 0
    assert female.json()["total"] == 0
    assert unspecified.json()["total"] == 188
    assert Decimal(body["summary"]["occupancy_rate"]) == Decimal("0.00")


def test_admin_crud_and_single_verify_are_private_and_audited_once(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    headers = {"X-CSRF-Token": csrf}

    created_building = db_client.post(
        "/api/v1/housing/buildings",
        headers={**headers, "X-Request-ID": "housing-building-create"},
        json={"code": "T7", "name": "Task 7 测试楼"},
    )
    assert created_building.status_code == 201
    building_id = created_building.json()["id"]

    updated_building = db_client.patch(
        f"/api/v1/housing/buildings/{building_id}",
        headers={**headers, "X-Request-ID": "housing-building-update"},
        json={"notes": "目录核对"},
    )
    assert updated_building.status_code == 200

    created_floor = db_client.post(
        "/api/v1/housing/floors",
        headers={**headers, "X-Request-ID": "housing-floor-create"},
        json={"building_id": building_id, "number": 2, "name": "二楼"},
    )
    assert created_floor.status_code == 201
    floor_id = created_floor.json()["id"]

    updated_floor = db_client.patch(
        f"/api/v1/housing/floors/{floor_id}",
        headers={**headers, "X-Request-ID": "housing-floor-update"},
        json={"notes": "楼层核对"},
    )
    assert updated_floor.status_code == 200

    created_room = db_client.post(
        "/api/v1/housing/rooms",
        headers={**headers, "X-Request-ID": "housing-room-create"},
        json={
            "floor_id": floor_id,
            "room_number": "T7-201",
            "room_type": "single",
            "capacity": 1,
        },
    )
    assert created_room.status_code == 201
    room_id = created_room.json()["id"]

    updated_room = db_client.patch(
        f"/api/v1/housing/rooms/{room_id}",
        headers={**headers, "X-Request-ID": "housing-room-update"},
        json={"version": 1, "notes": "房间核对"},
    )
    assert updated_room.status_code == 200
    assert updated_room.json()["version"] == 2

    before_verify = db_client.get(f"/api/v1/housing/rooms/{room_id}")
    assert before_verify.status_code == 200
    assert before_verify.json()["allowed_actions"] == {
        "edit": True,
        "delete": True,
        "verify": True,
        "create_stay": False,
        "report_issue": False,
        "view_detail": False,
        "manage_care": True,
        "complete_cleaning": False,
        "manage_photos": True,
    }
    verified = db_client.post(
        f"/api/v1/housing/rooms/{room_id}/verify",
        headers={**headers, "X-Request-ID": "housing-room-verify"},
        json={"version": 2},
    )
    assert verified.status_code == 200
    assert verified.json()["verification_status"] == "verified"

    after_verify = db_client.get(f"/api/v1/housing/rooms/{room_id}")
    assert after_verify.json()["allowed_actions"]["verify"] is False

    deleted_room = db_client.delete(
        f"/api/v1/housing/rooms/{room_id}",
        headers={**headers, "X-Request-ID": "housing-room-delete"},
    )
    deleted_floor = db_client.delete(
        f"/api/v1/housing/floors/{floor_id}",
        headers={**headers, "X-Request-ID": "housing-floor-delete"},
    )
    deleted_building = db_client.delete(
        f"/api/v1/housing/buildings/{building_id}",
        headers={**headers, "X-Request-ID": "housing-building-delete"},
    )
    assert deleted_room.status_code == 204
    assert deleted_floor.status_code == 204
    assert deleted_building.status_code == 204

    responses = (
        created_building,
        updated_building,
        created_floor,
        updated_floor,
        created_room,
        updated_room,
        before_verify,
        verified,
        after_verify,
        deleted_room,
        deleted_floor,
        deleted_building,
    )
    for response in responses:
        _assert_private(response)

    request_ids = {
        "housing-building-create",
        "housing-building-update",
        "housing-floor-create",
        "housing-floor-update",
        "housing-room-create",
        "housing-room-update",
        "housing-room-verify",
        "housing-room-delete",
        "housing-floor-delete",
        "housing-building-delete",
    }
    events = list(
        db.scalars(select(AuditEvent).where(AuditEvent.request_id.in_(request_ids)))
    )
    assert len(events) == len(request_ids)
    assert {event.request_id for event in events} == request_ids
    assert all(event.outcome == "allowed" for event in events)


def test_exact_global_create_and_batch_verify_authorization_and_static_route(
    db_client: TestClient,
    db: Session,
    platform_users,
    scoped_housing_accounts,
) -> None:
    manager_csrf = sign_in(db_client, platform_users, "guardian")
    denied_create = db_client.post(
        "/api/v1/housing/buildings",
        headers={
            "X-CSRF-Token": manager_csrf,
            "X-Request-ID": "housing-scoped-building-create",
        },
        json={"code": "NO", "name": "不得创建"},
    )
    assert denied_create.status_code == 403
    assert denied_create.json()["code"] == "permission_denied"
    _assert_private(denied_create)

    batch_rooms = [
        room
        for room in _FLOOR_ROOMS
        if room.id not in {_SINGLE_ROOM.id, _DOUBLE_ROOM.id}
        and room.room_type != "functional"
    ][:2]
    payload = {
        "rooms": [
            {"room_id": str(room.id), "version": 1} for room in batch_rooms
        ]
    }
    denied_batch = db_client.post(
        "/api/v1/housing/rooms/batch-verify",
        headers={
            "X-CSRF-Token": sign_in(db_client, platform_users, "customer_service"),
            "X-Request-ID": "housing-batch-denied",
        },
        json=payload,
    )
    assert denied_batch.status_code == 403
    assert denied_batch.json()["code"] == "permission_denied"

    allowed_batch = db_client.post(
        "/api/v1/housing/rooms/batch-verify",
        headers={
            "X-CSRF-Token": sign_in(db_client, platform_users, "admin"),
            "X-Request-ID": "housing-batch-allowed",
        },
        json=payload,
    )
    assert allowed_batch.status_code == 200
    assert [item["id"] for item in allowed_batch.json()] == [
        str(room.id) for room in batch_rooms
    ]
    assert all(
        item["verification_status"] == "verified"
        for item in allowed_batch.json()
    )
    with pytest.raises(HousingInvalidRoomConfiguration):
        HousingInventoryService(db).verify_rooms(
            [
                RoomVersionInput(room_id=room.id, version=2)
                for room in batch_rooms
            ],
            actor_user_id=platform_users["accounts"]["admin"].id,
        )

    for response in (denied_batch, allowed_batch):
        _assert_private(response)
    events = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.request_id.in_(
                    [
                        "housing-scoped-building-create",
                        "housing-batch-denied",
                        "housing-batch-allowed",
                    ]
                )
            )
        )
    )
    assert len(events) == 3
    by_request = {event.request_id: event for event in events}
    assert by_request["housing-scoped-building-create"].outcome == "denied"
    assert by_request["housing-batch-denied"].outcome == "denied"
    batch_allowed = by_request["housing-batch-allowed"]
    assert batch_allowed.outcome == "allowed"
    assert batch_allowed.resource_id is None
    assert batch_allowed.event_metadata == {
        "scope_kind": "floor",
        "scope_id": str(_FLOOR.id),
        "result_count": 2,
    }


def test_locked_room_authorization_blocks_scope_move_after_precheck(
    db_client: TestClient,
    db: Session,
    platform_users,
    scoped_housing_accounts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_authorize = housing_router._authorize_room_mutation
    target_floor = next(
        floor for floor in FLOOR_SEEDS if floor.building_id == _OTHER_BUILDING.id
    )

    def authorize_then_move(*args, **kwargs):
        room = original_authorize(*args, **kwargs)
        room.building_id = target_floor.building_id
        room.floor_id = target_floor.id
        db.commit()
        return room

    monkeypatch.setattr(
        housing_router,
        "_authorize_room_mutation",
        authorize_then_move,
    )
    response = db_client.delete(
        f"/api/v1/housing/rooms/{_SINGLE_ROOM.id}",
        headers={
            "X-CSRF-Token": sign_in(db_client, platform_users, "guardian"),
            "X-Request-ID": "housing-room-scope-race",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "housing_resource_not_found"
    _assert_private(response)
    moved = db.get(HousingRoom, _SINGLE_ROOM.id)
    assert moved is not None
    assert moved.floor_id == target_floor.id
    events = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.request_id == "housing-room-scope-race"
            )
        )
    )
    assert len(events) == 1
    assert events[0].outcome == "denied"
    assert events[0].event_metadata == {
        "room_id": str(_SINGLE_ROOM.id),
        "error_code": "housing_resource_not_found",
    }


def test_verify_rechecks_pending_status_under_lock(
    db_client: TestClient,
    db: Session,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_verify = HousingInventoryService.verify_room

    def concurrently_verified(
        self,
        room_id,
        *,
        expected_version,
        actor_user_id,
        authorize_room=None,
    ):
        room = self.db.get(HousingRoom, room_id)
        assert room is not None
        room.verification_status = "verified"
        room.version = expected_version
        self.db.flush()
        return original_verify(
            self,
            room_id,
            expected_version=expected_version,
            actor_user_id=actor_user_id,
            authorize_room=authorize_room,
        )

    monkeypatch.setattr(
        HousingInventoryService,
        "verify_room",
        concurrently_verified,
    )
    response = db_client.post(
        f"/api/v1/housing/rooms/{_SINGLE_ROOM.id}/verify",
        headers={
            "X-CSRF-Token": sign_in(db_client, platform_users, "admin"),
            "X-Request-ID": "housing-room-verify-race",
        },
        json={"version": 2},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "housing_invalid_room_configuration"
    _assert_private(response)
    db.expire_all()
    room = db.get(HousingRoom, _SINGLE_ROOM.id)
    assert room is not None
    assert room.verification_status == "pending_verification"
    assert room.version == 1
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.request_id == "housing-room-verify-race"
        )
    )
    assert event is not None
    assert event.outcome == "failed"


def test_housing_audit_failure_rolls_back_business_mutation(
    db_client: TestClient,
    db: Session,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_audit(*args, **kwargs) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated housing audit failure")

    monkeypatch.setattr(housing_api_support.AuditService, "append", reject_audit)
    response = db_client.post(
        "/api/v1/housing/buildings",
        headers={
            "X-CSRF-Token": sign_in(db_client, platform_users, "admin"),
            "X-Request-ID": "housing-audit-rollback",
        },
        json={"code": "AUDITFAIL", "name": "审计失败回滚楼"},
    )

    assert response.status_code == 500
    assert response.json()["code"] == "housing_audit_failed"
    _assert_private(response)
    assert (
        db.scalar(
            select(HousingBuilding.id).where(
                HousingBuilding.code == "AUDITFAIL"
            )
        )
        is None
    )


def test_locked_batch_authorization_prioritizes_any_unreadable_room(
    db_client: TestClient,
    db: Session,
    platform_users,
    scoped_housing_accounts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardian = platform_users["accounts"]["guardian"]
    db.add(
        UserPermissionOverride(
            user_id=guardian.id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="allow",
            scope_kind="building",
            scope_id=_OTHER_BUILDING.id,
        )
    )
    db.commit()
    source_rooms = [
        room for room in _FLOOR_ROOMS if room.room_type != "functional"
    ][:2]
    readable_floor = next(
        floor for floor in FLOOR_SEEDS if floor.building_id == _OTHER_BUILDING.id
    )
    unreadable_building = INITIAL_BUILDINGS[2]
    unreadable_floor = next(
        floor
        for floor in FLOOR_SEEDS
        if floor.building_id == unreadable_building.id
    )
    original_verify = HousingInventoryService.verify_rooms

    def move_to_mixed_scopes(self, inputs, **kwargs):
        first = self.db.get(HousingRoom, source_rooms[0].id)
        second = self.db.get(HousingRoom, source_rooms[1].id)
        assert first is not None and second is not None
        first.building_id = readable_floor.building_id
        first.floor_id = readable_floor.id
        second.building_id = unreadable_floor.building_id
        second.floor_id = unreadable_floor.id
        self.db.flush()
        return original_verify(self, inputs, **kwargs)

    monkeypatch.setattr(
        HousingInventoryService,
        "verify_rooms",
        move_to_mixed_scopes,
    )
    response = db_client.post(
        "/api/v1/housing/rooms/batch-verify",
        headers={
            "X-CSRF-Token": sign_in(db_client, platform_users, "guardian"),
            "X-Request-ID": "housing-batch-mixed-scope-race",
        },
        json={
            "rooms": [
                {"room_id": str(room.id), "version": 1}
                for room in source_rooms
            ]
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "housing_resource_not_found"
    _assert_private(response)
    event_row = db.scalar(
        select(AuditEvent).where(
            AuditEvent.request_id == "housing-batch-mixed-scope-race"
        )
    )
    assert event_row is not None
    assert event_row.outcome == "denied"
    assert event_row.event_metadata["error_code"] == "housing_resource_not_found"


def test_locked_target_floor_authorization_precedes_active_validation(
    db_client: TestClient,
    db: Session,
    platform_users,
    scoped_housing_accounts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id = platform_users["accounts"]["admin"].id
    target = HousingInventoryService(db).create_floor(
        FloorCreate(
            building_id=_BUILDING.id,
            number=99,
            name="并发目标层",
        ),
        actor_user_id=actor_id,
    )
    original_update = HousingInventoryService.update_room

    def move_and_deactivate_target(self, room_id, input, **kwargs):
        floor = self.db.get(HousingFloor, target.id)
        assert floor is not None
        floor.building_id = _OTHER_BUILDING.id
        floor.is_active = False
        self.db.flush()
        return original_update(self, room_id, input, **kwargs)

    monkeypatch.setattr(
        HousingInventoryService,
        "update_room",
        move_and_deactivate_target,
    )
    response = db_client.patch(
        f"/api/v1/housing/rooms/{_SINGLE_ROOM.id}",
        headers={
            "X-CSRF-Token": sign_in(db_client, platform_users, "guardian"),
            "X-Request-ID": "housing-target-floor-race",
        },
        json={"version": 1, "floor_id": str(target.id)},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "housing_resource_not_found"
    _assert_private(response)
    event_row = db.scalar(
        select(AuditEvent).where(
            AuditEvent.request_id == "housing-target-floor-race"
        )
    )
    assert event_row is not None
    assert event_row.outcome == "denied"


def test_batch_room_query_count_is_constant_for_one_and_five_hundred(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    existing = list(
        db.scalars(select(HousingRoom).order_by(HousingRoom.id))
    )
    for room in existing:
        room.verification_status = "pending_verification"
        room.version = 1
    extras = [
        HousingRoom(
            id=uuid4(),
            building_id=_BUILDING.id,
            floor_id=_FLOOR.id,
            room_number=f"LOAD-{index:03d}",
            normalized_key=f"LOAD-{index:03d}",
            room_type="single",
            capacity=1,
            verification_status="pending_verification",
            service_status="available",
            version=1,
        )
        for index in range(313)
    ]
    db.add_all(extras)
    db.commit()
    all_rooms = [*existing, *extras]
    assert len(all_rooms) >= 501
    all_room_ids = [room.id for room in all_rooms]
    csrf = sign_in(db_client, platform_users, "admin")

    def counted_batch(
        room_ids: list[UUID],
        request_id: str,
    ) -> tuple[int, object]:
        housing_selects = 0

        def capture(conn, cursor, statement, parameters, context, executemany):
            del conn, cursor, parameters, context, executemany
            nonlocal housing_selects
            if statement.lstrip().startswith("SELECT") and "housing_rooms" in statement:
                housing_selects += 1

        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = db_client.post(
                "/api/v1/housing/rooms/batch-verify",
                headers={
                    "X-CSRF-Token": csrf,
                    "X-Request-ID": request_id,
                },
                    json={
                        "rooms": [
                            {"room_id": str(room_id), "version": 1}
                            for room_id in room_ids
                        ]
                    },
                )
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        return housing_selects, response

    one_count, one_response = counted_batch(
        [all_room_ids[0]],
        "housing-batch-query-one",
    )
    five_hundred_count, five_hundred_response = counted_batch(
        all_room_ids[1:501],
        "housing-batch-query-five-hundred",
    )

    assert one_response.status_code == 200
    assert five_hundred_response.status_code == 200
    assert one_count == five_hundred_count == 3


def test_overview_and_catalog_do_not_repeat_catalog_queries(
    db_client: TestClient,
    db: Session,
    platform_users,
    scoped_housing_accounts,
) -> None:
    engine = db.get_bind()

    def counted_get(path: str) -> tuple[dict[str, int], object]:
        counts = {"buildings": 0, "floors": 0, "rooms": 0}

        def capture(conn, cursor, statement, parameters, context, executemany):
            del conn, cursor, parameters, context, executemany
            normalized = " ".join(statement.split())
            if not normalized.startswith("SELECT"):
                return
            for table, key in (
                ("housing_buildings", "buildings"),
                ("housing_floors", "floors"),
                ("housing_rooms", "rooms"),
            ):
                if f"FROM {table}" in normalized:
                    counts[key] += 1

        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = db_client.get(path)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        return counts, response

    sign_in(db_client, platform_users, "admin")
    overview_counts, overview_response = counted_get("/api/v1/housing/overview")
    assert overview_response.status_code == 200
    assert overview_counts == {"buildings": 1, "floors": 1, "rooms": 1}

    db.add(
        HousingBuilding(
            code="EMPTY",
            name="无楼层测试楼",
            display_order=999,
            is_active=True,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "customer_service")
    catalog_counts, catalog_response = counted_get("/api/v1/housing/catalog")
    assert catalog_response.status_code == 200
    assert catalog_counts == {"buildings": 1, "floors": 1, "rooms": 0}
