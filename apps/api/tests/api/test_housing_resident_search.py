from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.identity.models import Person
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from tests.api.test_housing import sign_in
from tests.db.test_housing_occupancy_projection import _person, _room, _stay


@pytest.fixture
def resident_search(db, platform_users):
    actor_id = platform_users["accounts"]["admin"].id
    now = datetime.now(UTC)
    rooms = {key: _room(db, actor_id=actor_id, number=key) for key in ("R1", "R2", "R3")}
    residents = {}
    for key, name, nickname, room_key, status in (
        ("current", "林晨", "星河Ａce%_９", "R1", "checked_in"),
        ("pending", "陈明", None, "R1", "checkout_pending"),
        ("other", "林晨", "远山", "R2", "checked_in"),
        ("future", "预约姓名", "预约昵称", "R3", "planned"),
        ("past", "退房姓名", "退房昵称", "R3", "checked_out"),
        ("cancelled", "取消姓名", "取消昵称", "R3", "cancelled"),
        ("voided", "作废姓名", "作废昵称", "R3", "checked_out"),
    ):
        person_id = _person(db, name=name, sex="male", person_type="student")
        person = db.get(Person, person_id)
        person.nickname = nickname
        residents[key] = person
        future = status in {"planned", "cancelled"}
        past = status == "checked_out"
        stay = _stay(
            db,
            room=rooms[room_key],
            person_id=person_id,
            actor_id=actor_id,
            status=status,
            planned_in=now + timedelta(days=2) if future else now - timedelta(days=5),
            planned_out=now - timedelta(days=2) if past else now + timedelta(days=5),
        )
        if key == "voided":
            stay.voided_at = now
            stay.voided_by_user_id = actor_id
            stay.void_reason_ciphertext = "test-void-reason"
    db.commit()
    return rooms, residents


def _overview(client, **params):
    response = client.get("/api/v1/housing/overview", params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, private"
    return response.json()


@pytest.mark.parametrize(
    "query,expected",
    [
        ("林晨", {"R1", "R2"}),
        ("晨", {"R1", "R2"}),
        ("陈明", {"R1"}),
        ("明", {"R1"}),
        ("星河Ａce%_９", {"R1"}),
        ("  星河ace%_9  ", {"R1"}),
        ("ＡＣＥ", {"R1"}),
        ("%_", {"R1"}),
        ("远", {"R2"}),
        ("R3-201", {"R3"}),
        ("投影楼R3", {"R3"}),
    ],
)
def test_room_search_matches_current_names_nicknames_and_existing_room_fields(
    db_client,
    platform_users,
    resident_search,
    query,
    expected,
):
    rooms, _ = resident_search
    sign_in(db_client, platform_users, "admin")
    body = _overview(db_client, query=query)
    assert {room["id"] for room in body["rooms"]} == {str(rooms[key].id) for key in expected}
    assert body["total"] == body["summary"]["total_rooms"] == len(expected)
    # Nicknames stay internal to the batched search projection, not new response fields.
    assert all(
        "nickname" not in occupant for room in body["rooms"] for occupant in room["occupants"]
    )


@pytest.mark.parametrize(
    "query",
    [
        "预约姓名",
        "预约昵称",
        "退房姓名",
        "退房昵称",
        "取消姓名",
        "取消昵称",
        "作废姓名",
        "作废昵称",
        "不存在的人",
        "%不存在_",
    ],
)
def test_room_search_excludes_noncurrent_people(db_client, platform_users, resident_search, query):
    sign_in(db_client, platform_users, "admin")
    body = _overview(db_client, query=query)
    assert body["rooms"] == []
    assert (
        body["total"] == body["summary"]["total_rooms"] == body["summary"]["current_occupants"] == 0
    )


def test_resident_search_precedes_pagination_counts_rooms_once_and_keeps_sidebar_scope(
    db_client,
    db,
    platform_users,
    resident_search,
):
    rooms, people = resident_search
    people["pending"].full_name = "林晨"
    db.commit()
    sign_in(db_client, platform_users, "admin")
    first = _overview(db_client, query="林晨", page_size=1)
    second = _overview(db_client, query="林晨", page_size=1, page=2)
    assert first["total"] == second["total"] == 2
    assert first["summary"] == second["summary"]
    assert first["summary"]["total_rooms"] == 2
    assert first["summary"]["current_occupants"] == 3
    assert {first["rooms"][0]["id"], second["rooms"][0]["id"]} == {
        str(rooms[key].id) for key in ("R1", "R2")
    }
    filters = {
        "query": "林晨",
        "building_id": str(rooms["R1"].building_id),
        "floor_id": str(rooms["R1"].floor_id),
    }
    base = _overview(db_client, **filters)
    assert [room["id"] for room in base["rooms"]] == [str(rooms["R1"].id)]
    shortcut = _overview(db_client, **filters, quick_filter="assignable")
    assert shortcut["total"] == 0  # R1 is full, but the sidebar summary is unchanged.
    assert shortcut["summary"] == base["summary"]
    assert _overview(db_client, **{**filters, "floor_id": str(rooms["R2"].floor_id)})["total"] == 0


def test_resident_search_does_not_reveal_names_or_counts_in_denied_rooms(
    db_client,
    db,
    platform_users,
    resident_search,
):
    rooms, _ = resident_search
    account_id = platform_users["accounts"]["customer_service"].id
    db.add(
        UserPermissionOverride(
            user_id=account_id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=account_id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="deny",
            scope_kind="floor",
            scope_id=rooms["R2"].floor_id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "customer_service")
    shared = _overview(db_client, query="林晨")
    assert [room["id"] for room in shared["rooms"]] == [str(rooms["R1"].id)]
    assert shared["total"] == shared["summary"]["total_rooms"] == 1
    assert shared["summary"]["current_occupants"] == 2
    private = _overview(db_client, query="远山")
    assert private["rooms"] == []
    assert private["total"] == private["summary"]["total_rooms"] == 0
