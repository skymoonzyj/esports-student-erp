from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from io import StringIO
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from tests.api.test_housing import sign_in
from tests.api.test_housing_room_status_export import snapshot_client as _snapshot_client
from tests.api.test_housing_room_status_export import snapshot_context as _snapshot_context
from tests.db.test_housing_occupancy_projection import _stay

snapshot_client = _snapshot_client
snapshot_context = _snapshot_context


@pytest.fixture
def mixed_rooms(db, platform_users):
    now = datetime.now(UTC)
    building = HousingBuilding(id=uuid4(), code="MIX", name="统计测试楼")
    other = HousingBuilding(id=uuid4(), code="MIX2", name="统计另一楼")
    db.add_all([building, other])
    db.flush()
    floors = [
        HousingFloor(id=uuid4(), building_id=b.id, number=1, name="一楼") for b in (building, other)
    ]
    db.add_all(floors)
    db.flush()
    definitions = [
        ("partial", "double", 2, "available", "verified", "ready"),
        ("full", "double", 2, "available", "verified", "ready"),
        ("empty", "single", 1, "available", "verified", "ready"),
        ("pending", "single", 1, "available", "pending_verification", "ready"),
        ("dirty", "triple", 3, "available", "verified", "pending"),
        ("problem", "single", 1, "problem", "verified", "ready"),
        ("maintenance", "double", 2, "maintenance", "verified", "ready"),
        ("disabled", "triple", 3, "disabled", "verified", "ready"),
        ("functional", "functional", 0, "available", "verified", "ready"),
        ("custom", "custom", 4, "available", "verified", "ready"),
    ]
    rooms = {}
    for key, room_type, capacity, status, verification, cleaning in definitions:
        room = HousingRoom(
            id=uuid4(),
            building_id=building.id,
            floor_id=floors[0].id,
            room_number=f"MIX-{key}",
            normalized_key=f"MIX-{key}",
            room_type=room_type,
            capacity=capacity,
            service_status=status,
            verification_status=verification,
            cleaning_status=cleaning,
            cleaning_pending_at=now if cleaning == "pending" else None,
        )
        rooms[key] = room
        db.add(room)
    db.add(
        HousingRoom(
            id=uuid4(),
            building_id=other.id,
            floor_id=floors[1].id,
            room_number="MIX-other",
            normalized_key="MIX-other",
            room_type="single",
            capacity=1,
        )
    )
    db.flush()
    for room_key, person_key, status in [
        ("partial", "own_student", "checked_in"),
        ("full", "other_student", "checked_in"),
        ("full", "adult_without_account", "checkout_pending"),
        ("problem", "self_student", "checked_in"),
    ]:
        _stay(
            db,
            room=rooms[room_key],
            person_id=platform_users["people"][person_key].id,
            actor_id=platform_users["accounts"]["admin"].id,
            status=status,
            planned_in=now - timedelta(days=2),
            planned_out=now + timedelta(days=2),
        )
    db.commit()
    return building, other, floors, rooms


def test_mixed_summary_uses_eligible_beds_and_keeps_anomalous_residents(
    db_client,
    platform_users,
    mixed_rooms,
):
    building, _, _, _ = mixed_rooms
    sign_in(db_client, platform_users, "admin")
    body = db_client.get(
        "/api/v1/housing/overview", params={"building_id": str(building.id)}
    ).json()
    assert body["summary"] == {
        "total_rooms": 10,
        "occupied_rooms": 3,
        "unoccupied_rooms": 5,
        "assignable_rooms": 3,
        "unassignable_rooms": 7,
        "full_rooms": 2,
        "problem_rooms": 3,
        "pending_cleaning_rooms": 1,
        "daily_cleaning_rooms": 0,
        "weekly_cleaning_rooms": 0,
        "on_demand_cleaning_rooms": 0,
        "pending_verification_rooms": 1,
        "total_capacity": 13,
        "current_occupants": 4,
        "remaining_capacity": 10,
        "occupancy_rate": "23.08",
        "single_rooms": 2,
        "double_rooms": 2,
        "triple_rooms": 1,
        "custom_rooms": 1,
        "functional_rooms": 1,
    }
    for status in ("problem", "maintenance", "disabled"):
        zero = db_client.get(
            "/api/v1/housing/overview",
            params={
                "building_id": str(building.id),
                "service_status": status,
            },
        ).json()["summary"]
        assert zero["total_capacity"] == zero["remaining_capacity"] == 0
        assert zero["occupancy_rate"] == "0.00"
    assert (
        db_client.get(
            "/api/v1/housing/overview",
            params={
                "building_id": str(building.id),
                "service_status": "problem",
            },
        ).json()["summary"]["current_occupants"]
        == 1
    )


@pytest.mark.parametrize(
    "quick_filter,want",
    [
        ("occupied", ["full", "partial", "problem"]),
        (
            "vacant",
            ["custom", "dirty", "empty", "functional", "pending"],
        ),
        ("assignable", ["custom", "empty", "partial"]),
        ("full", ["full", "problem"]),
        ("problem", ["disabled", "maintenance", "problem"]),
        ("pending_verification", ["pending"]),
        ("pending_cleaning", ["dirty"]),
        ("usable", ["custom", "dirty", "empty", "full", "partial", "pending"]),
        ("remaining", ["custom", "dirty", "empty", "partial", "pending"]),
        ("single", ["empty", "pending"]),
        ("double", ["full", "partial"]),
        ("triple", ["dirty"]),
        ("custom", ["custom"]),
        ("functional", ["functional"]),
    ],
)
def test_shortcuts_filter_list_and_pagination_but_keep_base_summary(
    db_client,
    platform_users,
    mixed_rooms,
    quick_filter,
    want,
):
    building, other, _, _ = mixed_rooms
    sign_in(db_client, platform_users, "admin")
    base = {"building_id": str(building.id), "page_size": 1}
    summary = db_client.get("/api/v1/housing/overview", params=base).json()["summary"]
    names = []
    for page in range(1, len(want) + 1):
        response = db_client.get(
            "/api/v1/housing/overview",
            params={
                **base,
                "quick_filter": quick_filter,
                "page": page,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"] == summary
        assert body["total"] == len(want)
        names.extend(room["room_number"].removeprefix("MIX-") for room in body["rooms"])
    assert names == want
    assert (
        db_client.get(
            "/api/v1/housing/overview",
            params={
                "building_id": str(other.id),
                "quick_filter": "full",
            },
        ).json()["summary"]["total_rooms"]
        == 1
    )


def test_full_filter_is_not_legacy_broad_unassignable(db_client, platform_users, mixed_rooms):
    building, _, _, _ = mixed_rooms
    sign_in(db_client, platform_users, "admin")
    for assignability, want in [("full", 2), ("unassignable", 7), ("assignable", 3)]:
        body = db_client.get(
            "/api/v1/housing/overview",
            params={
                "building_id": str(building.id),
                "assignability": assignability,
            },
        ).json()
        assert body["total"] == body["summary"]["total_rooms"] == want


@pytest.mark.parametrize("service_status", ["problem", "maintenance", "disabled"])
def test_vacant_summary_and_shortcut_exclude_out_of_service_empty_rooms(
    db_client, db, platform_users, mixed_rooms, service_status,
):
    building, _, _, rooms = mixed_rooms
    rooms["empty"].service_status = service_status
    db.commit()
    sign_in(db_client, platform_users, "admin")
    base = {"building_id": str(building.id)}
    response = db_client.get("/api/v1/housing/overview", params=base)
    assert response.status_code == 200, response.text
    summary = response.json()["summary"]
    assert summary["total_rooms"] == 10
    assert summary["occupied_rooms"] == 3
    assert summary["current_occupants"] == 4
    assert summary["problem_rooms"] == 4
    assert summary["unoccupied_rooms"] == 4

    vacant = db_client.get(
        "/api/v1/housing/overview", params={**base, "quick_filter": "vacant"},
    ).json()
    assert vacant["summary"] == summary
    assert vacant["total"] == 4
    assert {room["room_number"] for room in vacant["rooms"]} == {
        "MIX-custom", "MIX-dirty", "MIX-functional", "MIX-pending",
    }
    # The separate legacy 未入住 condition is still a physical-occupancy filter.
    legacy = db_client.get(
        "/api/v1/housing/overview", params={**base, "occupancy": "unoccupied"},
    ).json()
    assert legacy["total"] == 7
    assert legacy["summary"]["unoccupied_rooms"] == 4
    excluded = db_client.get(
        "/api/v1/housing/overview",
        params={**base, "service_status": service_status, "quick_filter": "vacant"},
    ).json()
    assert excluded["total"] == excluded["summary"]["unoccupied_rooms"] == 0


@pytest.mark.parametrize("service_status", ["available", "problem", "maintenance", "disabled"])
@pytest.mark.parametrize("export_kind", ["snapshot", "history"])
def test_vacant_exports_exclude_out_of_service_rooms_without_changing_legacy_exports(
    snapshot_client, service_status, export_kind,
):
    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        room = db.scalar(select(HousingRoom).where(HousingRoom.floor_id == context.floor_id))
        stay = db.scalar(select(HousingStay).where(HousingStay.room_id == room.id))
        room.service_status = service_status
        stay.status = "checked_out"
        stay.actual_check_out_at = stay.actual_check_in_at + timedelta(days=1)
        stay.checked_out_by_user_id = context.actor_id
        db.commit()
    path, scope = (
        ("room-status/exports", {"building_ids": [str(context.building_id)]})
        if export_kind == "snapshot"
        else ("exports", {"building_id": str(context.building_id)})
    )
    payload = {"purpose": "空房范围核对", "format": "csv", **scope}
    for filters in ({}, {"occupancy": "unoccupied"}):
        legacy = client.post(f"/api/v1/housing/{path}", json={**payload, **filters})
        assert legacy.status_code == 200, legacy.text
        assert len(list(csv.DictReader(StringIO(legacy.content.decode("utf-8-sig"))))) == 1
    vacant = client.post(
        f"/api/v1/housing/{path}", json={**payload, "quick_filter": "vacant"},
    )
    if service_status != "available" and export_kind == "snapshot":
        assert vacant.status_code == 422, vacant.text
        assert vacant.json()["code"] == "housing_export_empty"
    else:
        assert vacant.status_code == 200, vacant.text
        rows = list(csv.DictReader(StringIO(vacant.content.decode("utf-8-sig"))))
        assert len(rows) == (1 if service_status == "available" else 0)


def test_shortcuts_never_include_denied_buildings(db_client, db, platform_users, mixed_rooms):
    building, _, _, _ = mixed_rooms
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="deny",
            scope_kind="building",
            scope_id=building.id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "admin")
    body = db_client.get(
        "/api/v1/housing/overview",
        params={
            "query": "MIX-",
            "quick_filter": "occupied",
        },
    ).json()
    assert body["total"] == 0 and body["rooms"] == []
    assert body["summary"]["total_rooms"] == 1
    assert body["summary"]["current_occupants"] == 0


@pytest.mark.parametrize("path", ["overview", "exports", "room-status/exports"])
def test_invalid_shortcut_is_rejected(db_client, platform_users, path):
    csrf = sign_in(db_client, platform_users, "admin")
    if path == "overview":
        response = db_client.get(f"/api/v1/housing/{path}", params={"quick_filter": "unknown"})
    else:
        response = db_client.post(
            f"/api/v1/housing/{path}",
            headers={"X-CSRF-Token": csrf},
            json={"purpose": "测试筛选", "quick_filter": "unknown"},
        )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "filters,want",
    [
        ({}, 3),
        ({"assignability": "unassignable"}, 3),
        ({"assignability": "full"}, 1),
        ({"quick_filter": "full"}, 1),
        ({"quick_filter": "vacant"}, 2),
        ({"quick_filter": "pending_verification"}, 1),
    ],
)
def test_current_export_filters_preserve_legacy_unassignable(snapshot_client, filters, want):
    client, context, _ = snapshot_client
    ids = [uuid4(), uuid4()]
    with Session(context.engine) as db:
        occupied = db.scalar(select(HousingRoom).where(HousingRoom.floor_id == context.floor_id))
        occupied.room_type, occupied.capacity = "single", 1
        for room_id, room_type, capacity, verification in [
            (ids[0], "single", 1, "pending_verification"),
            (ids[1], "functional", 0, "verified"),
        ]:
            db.add(
                HousingRoom(
                    id=room_id,
                    building_id=context.building_id,
                    floor_id=context.floor_id,
                    room_number=str(room_id)[:8],
                    normalized_key=room_id.hex,
                    room_type=room_type,
                    capacity=capacity,
                    verification_status=verification,
                )
            )
        db.commit()
    try:
        response = client.post(
            "/api/v1/housing/room-status/exports",
            json={
                "purpose": "快捷筛选导出",
                "format": "csv",
                "building_ids": [str(context.building_id)],
                **filters,
            },
        )
        assert response.status_code == 200, response.text
        assert len(list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))) == want
    finally:
        with Session(context.engine) as db:
            db.execute(delete(HousingRoom).where(HousingRoom.id.in_(ids)))
            db.commit()


@pytest.mark.parametrize(
    "filters,want",
    [
        ({}, 1),
        ({"assignability": "unassignable"}, 1),
        ({"assignability": "full"}, 0),
        ({"quick_filter": "full"}, 0),
        ({"quick_filter": "occupied"}, 1),
        ({"quick_filter": "vacant"}, 0),
        ({"quick_filter": "pending_verification"}, 1),
        ({"cleaning_statuses": ["pending"]}, 0),
        ({"daily_cleaning_enabled": False}, 1),
        ({"daily_cleaning_enabled": True}, 0),
    ],
)
def test_occupancy_export_filters_keep_old_broad_unassignable(snapshot_client, filters, want):
    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        room = db.scalar(select(HousingRoom).where(HousingRoom.floor_id == context.floor_id))
        room.room_type, room.capacity, room.verification_status = (
            "double",
            2,
            "pending_verification",
        )
        db.commit()
    response = client.post(
        "/api/v1/housing/exports",
        json={
            "purpose": "快捷筛选导出",
            "format": "csv",
            "building_id": str(context.building_id),
            **filters,
        },
    )
    assert response.status_code == 200, response.text
    assert len(list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))) == want
