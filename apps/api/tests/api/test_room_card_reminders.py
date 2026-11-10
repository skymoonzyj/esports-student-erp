from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, update

from app.core.time import business_date_at
from app.housing.models import HousingRoom, HousingStay
from app.housing.room_card_models import HousingRoomCardRecord
from app.housing.seed_inventory import INITIAL_ROOMS
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS

from .test_housing_room_cards import card_stay as _card_stay
from .test_housing_room_cards import change_admin_role, login


def test_room_card_reminders_require_authentication(client):
    response = client.get("/api/v1/portal/room-card-reminders")
    assert response.status_code == 401


def test_reminders_boundary_self_privacy_and_no_account_people(db_client, db, platform_users):
    room = db.get(HousingRoom, INITIAL_ROOMS[0].id)
    now = datetime.now(UTC)
    today = business_date_at(now)
    rows = []
    for index, (person_key, offset) in enumerate(
        [
            ("self_student", 7),
            ("adult_without_account", -1),
            ("other_student", 8),
        ]
    ):
        stay = HousingStay(
            person_id=platform_users["people"][person_key].id,
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
        db.flush()
        db.add(
            HousingRoomCardRecord(
                stay_id=stay.id,
                issued_on=today - timedelta(days=30),
                expires_on=today + timedelta(days=offset),
                version=1,
                updated_by_user_id=platform_users["accounts"]["admin"].id,
            )
        )
        rows.append(stay)
    db.commit()
    login(db_client, platform_users)
    response = db_client.get("/api/v1/portal/room-card-reminders?page_size=1")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 2 and len(data["items"]) == 1
    assert data["items"][0]["days_remaining"] == -1
    assert data["items"][0]["person_id"] == str(
        platform_users["people"]["adult_without_account"].id
    )
    assert set(data["items"][0]) == {
        "stay_id",
        "person_id",
        "person_name",
        "nickname",
        "room_number",
        "building_name",
        "issued_on",
        "expires_on",
        "days_remaining",
    }
    assert data["next_refresh_at"].endswith("T00:00:00+08:00")
    login(db_client, platform_users, "self_student")
    own = db_client.get("/api/v1/portal/room-card-reminders").json()
    assert own["total"] == 1 and own["items"][0]["stay_id"] == str(rows[0].id)
    assert own["items"][0]["days_remaining"] == 7
    login(db_client, platform_users, "guardian")
    assert db_client.get("/api/v1/portal/room-card-reminders").json()["total"] == 0
    actor_id = platform_users["accounts"]["admin"].id
    rows[0].status = "checked_out"
    rows[0].actual_check_out_at = now
    rows[0].checked_out_by_user_id = actor_id
    db.commit()
    login(db_client, platform_users, "self_student")
    assert db_client.get("/api/v1/portal/room-card-reminders").json()["total"] == 0
    change_admin_role(db, platform_users, "property")
    login(db_client, platform_users)
    assert db_client.get("/api/v1/portal/room-card-reminders").json()["total"] == 0
    change_admin_role(db, platform_users, "administration")
    login(db_client, platform_users)
    assert db_client.get("/api/v1/portal/room-card-reminders").json()["total"] == 1
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="deny",
            scope_kind="floor",
            scope_id=room.floor_id,
        )
    )
    db.commit()
    denied = db_client.get("/api/v1/portal/room-card-reminders").json()
    assert denied["total"] == 0 and denied["items"] == []


@pytest.fixture
def reminder_stay(db, platform_users):
    return _card_stay.__wrapped__(db, platform_users)


@pytest.mark.parametrize("scenario", ["planned", "future", "checked_out", "void", "deleted_person"])
def test_reminders_exclude_ineffective_stays(
    db_client, db, platform_users, reminder_stay, scenario
):
    actor_id = platform_users["accounts"]["admin"].id
    person = db.get(Person, platform_users["people"]["self_student"].id)
    now = datetime.now(UTC)
    today = business_date_at(now)
    db.add(
        HousingRoomCardRecord(
            stay_id=reminder_stay.id,
            issued_on=today,
            expires_on=today,
            version=1,
            updated_by_user_id=actor_id,
        )
    )
    if scenario == "planned":
        reminder_stay.status = "planned"
        reminder_stay.actual_check_in_at = None
        reminder_stay.checked_in_by_user_id = None
    elif scenario == "future":
        reminder_stay.planned_check_in_at = now + timedelta(days=1)
        reminder_stay.actual_check_in_at = now + timedelta(days=1)
    elif scenario == "deleted_person":
        person.deleted_at = now
    else:
        reminder_stay.status = "checked_out"
        reminder_stay.actual_check_out_at = now
        reminder_stay.checked_out_by_user_id = actor_id
        if scenario == "void":
            reminder_stay.voided_at = now
            reminder_stay.voided_by_user_id = actor_id
            reminder_stay.void_reason_ciphertext = "synthetic-void-reason"
    db.commit()
    login(db_client, platform_users)
    response = db_client.get("/api/v1/portal/room-card-reminders")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0 and response.json()["items"] == []


def test_reminder_count_and_page_share_one_snapshot_during_card_change(
    db_client, db, platform_users, reminder_stay
):
    today = business_date_at(datetime.now(UTC))
    stay_id = reminder_stay.id
    db.add(
        HousingRoomCardRecord(
            stay_id=stay_id,
            issued_on=today,
            expires_on=today,
            version=1,
            updated_by_user_id=platform_users["accounts"]["admin"].id,
        )
    )
    db.commit()
    login(db_client, platform_users)
    changed = False

    def change_card_after_count(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and "count(*)" in statement.lower() and "housing_room_cards" in statement:
            changed = True
            connection.execute(
                update(HousingRoomCardRecord)
                .where(HousingRoomCardRecord.stay_id == stay_id)
                .values(expires_on=today + timedelta(days=8))
            )

    bind = db.get_bind()
    event.listen(bind, "after_cursor_execute", change_card_after_count)
    try:
        response = db_client.get("/api/v1/portal/room-card-reminders")
    finally:
        event.remove(bind, "after_cursor_execute", change_card_after_count)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == len(data["items"])
