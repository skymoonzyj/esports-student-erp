from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import delete, event, text
from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount
from app.housing.errors import HousingServiceError
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.schemas import CheckoutConfirmation, ConfirmCheckInRequest, StayCreate, StayUpdate
from app.identity.models import Person, StaffProfile
from tests.db.test_housing_transfer_checkout import NOW, _account_person, _allow, _room, _stay


@pytest.mark.parametrize("edit", ["date", "note"])
def test_pending_cleaning_allows_reservation_edits_but_not_subsequent_checkin(db, edit):
    person, actor = _account_person(db, name="待保洁预约编辑")
    room = _room(db)
    service = HousingOccupancyService(db)
    scheduled_at = NOW + timedelta(days=1)
    stay = service.create_stay(
        room.id,
        StayCreate(person_id=person.id, stay_purpose="permanent", duration_mode="long_term",
                   planned_check_in_at=scheduled_at, notes="original"),
        actor_user_id=actor, occurred_at=NOW, authorize_room=_allow,
    )
    room.cleaning_status = "pending"
    room.cleaning_pending_at = NOW
    db.commit()
    edited_at = scheduled_at + timedelta(days=1) if edit == "date" else scheduled_at
    edited_notes = "edited note" if edit == "note" else "original"
    updated = service.update_reservation(
        stay.id, StayUpdate(version=1, planned_check_in_at=edited_at, notes=edited_notes),
        actor_user_id=actor, occurred_at=NOW, authorize_room=_allow,
    )
    assert updated.planned_check_in_at == edited_at
    assert updated.notes == edited_notes
    assert updated.version == 2
    db.expire_all()
    unchanged_room = db.get(HousingRoom, room.id)
    assert unchanged_room.cleaning_status == "pending"
    assert unchanged_room.cleaning_pending_at == NOW
    assert unchanged_room.version == 1
    with pytest.raises(HousingServiceError) as error:
        service.confirm_check_in(
            stay.id, ConfirmCheckInRequest(version=2, actual_check_in_at=edited_at),
            actor_user_id=actor, occurred_at=edited_at, authorize_room=_allow,
        )
    assert error.value.code == "housing_room_pending_cleaning"
    db.expire_all()
    assert db.get(HousingStay, stay.id).status == "planned"
    assert db.get(HousingStay, stay.id).version == 2


@pytest.mark.parametrize("invalid_state,expected_code", [
    ("parent", "housing_parent_inactive"),
    ("service", "housing_room_unavailable"),
    ("verification", "housing_room_not_verified"),
    ("conflict", "housing_capacity_conflict"),
])
def test_pending_reservation_edit_keeps_existing_room_validations(db, invalid_state, expected_code):
    person, actor = _account_person(db, name="待保洁编辑原有校验")
    room = _room(db)
    service = HousingOccupancyService(db)
    scheduled_at = NOW + timedelta(days=1)
    stay = service.create_stay(
        room.id,
        StayCreate(person_id=person.id, stay_purpose="permanent", duration_mode="fixed_term",
                   planned_check_in_at=scheduled_at,
                   planned_check_out_at=scheduled_at + timedelta(days=1)),
        actor_user_id=actor, occurred_at=NOW, authorize_room=_allow,
    )
    if invalid_state == "conflict":
        other, _ = _account_person(db, name="预约编辑容量冲突")
        scheduled_at += timedelta(days=2)
        service.create_stay(
            room.id,
            StayCreate(person_id=other.id, stay_purpose="permanent", duration_mode="fixed_term",
                       planned_check_in_at=scheduled_at,
                       planned_check_out_at=scheduled_at + timedelta(days=1)),
            actor_user_id=actor, occurred_at=NOW, authorize_room=_allow,
        )
    room.cleaning_status = "pending"
    room.cleaning_pending_at = NOW
    if invalid_state == "parent":
        db.get(HousingFloor, room.floor_id).is_active = False
    elif invalid_state == "service":
        room.service_status = "maintenance"
    elif invalid_state == "verification":
        room.verification_status = "pending_verification"
    db.commit()
    with pytest.raises(HousingServiceError) as error:
        service.update_reservation(
            stay.id, StayUpdate(version=1, planned_check_in_at=scheduled_at,
                                planned_check_out_at=scheduled_at + timedelta(days=1), notes="new"),
            actor_user_id=actor, occurred_at=NOW, authorize_room=_allow,
        )
    assert error.value.code == expected_code


@pytest.fixture
def committed_cleaning_room(postgres_engine):
    with Session(postgres_engine) as db:
        person, actor = _account_person(db, name="保洁并发人员")
        room = _room(db, capacity=3)
        room.cleaning_status = "pending"
        room.cleaning_pending_at = NOW - timedelta(hours=1)
        stay = _stay(db, person_id=person.id, actor_id=actor, room=room,
                     status="checkout_pending", request_fields=True)
        arrival, arrival_actor = _account_person(db, name="保洁并发待入住")
        planned = _stay(db, person_id=arrival.id, actor_id=actor, room=room)
        planned.status = "planned"
        planned.actual_check_in_at = None
        planned.checked_in_by_user_id = None
        planned_id, arrival_id = planned.id, arrival.id
        ids = actor, room.id, stay.id, person.id, room.floor_id, room.building_id
        db.commit()
    try:
        yield *ids[:3], planned_id
    finally:
        with Session(postgres_engine) as db, db.begin():
            assert db.scalar(text("SELECT current_database()")).endswith("_test")
            db.execute(delete(HousingStay).where(HousingStay.room_id == ids[1]))
            db.execute(delete(HousingRoom).where(HousingRoom.id == ids[1]))
            db.execute(delete(HousingFloor).where(HousingFloor.id == ids[4]))
            db.execute(delete(HousingBuilding).where(HousingBuilding.id == ids[5]))
            db.execute(delete(AccountLoginIdentifier).where(AccountLoginIdentifier.account_id.in_([actor, arrival_actor])))
            db.execute(delete(StaffProfile).where(StaffProfile.person_id.in_([ids[3], arrival_id])))
            db.execute(delete(UserAccount).where(UserAccount.id.in_([actor, arrival_actor])))
            db.execute(delete(Person).where(Person.id.in_([ids[3], arrival_id])))


@pytest.mark.parametrize("first_action,second_action", [
    ("complete", "checkout"), ("checkout", "complete"),
    ("confirm", "checkout"), ("checkout", "confirm"),
])
def test_two_connections_serialize_completion_and_departure(
    postgres_engine, committed_cleaning_room, first_action, second_action
):
    from app.housing.cleaning_service import HousingCleaningService

    actor, room_id, stay_id, planned_id = committed_cleaning_room
    if "confirm" in (first_action, second_action):
        with Session(postgres_engine) as db, db.begin():
            room = db.get(HousingRoom, room_id)
            room.cleaning_status = "ready"
            room.cleaning_pending_at = None
    first_locked, contender_waiting, release = Event(), Event(), Event()
    pids = {}

    def run(action, first):
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '6s'"))
            pids[first] = db.scalar(text("SELECT pg_backend_pid()"))
            connection = db.connection()

            def observe(_conn, _cursor, statement, _params, _ctx, _many):
                if "FROM housing_rooms" in statement and "FOR UPDATE" in statement:
                    if first:
                        first_locked.set()
                        assert release.wait(5)
                    else:
                        contender_waiting.set()

            event_name = "after_cursor_execute" if first else "before_cursor_execute"
            event.listen(connection, event_name, observe)
            try:
                if action == "complete":
                    HousingCleaningService(db).complete(
                        room_id, version=1, actor_user_id=actor, occurred_at=NOW,
                        authorize_room=_allow,
                    )
                elif action == "checkout":
                    HousingOccupancyService(db).confirm_checkout(
                        stay_id, CheckoutConfirmation(version=1), actor_user_id=actor,
                        occurred_at=NOW + timedelta(minutes=1), authorize_room=_allow,
                    )
                else:
                    HousingOccupancyService(db).confirm_check_in(
                        planned_id, ConfirmCheckInRequest(version=1, actual_check_in_at=NOW),
                        actor_user_id=actor, occurred_at=NOW, authorize_room=_allow,
                    )
                db.commit()
                return "ok"
            except HousingServiceError as exc:
                db.rollback()
                return exc.code
            finally:
                event.remove(connection, event_name, observe)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, first_action, True)
        try:
            assert first_locked.wait(3)
            second = pool.submit(run, second_action, False)
            assert contender_waiting.wait(3)
            blocked = False
            with Session(postgres_engine) as observer:
                deadline = monotonic() + 2
                while monotonic() < deadline:
                    if pids[True] in observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": pids[False]}
                    ):
                        blocked = True
                        break
                    sleep(0.01)
            assert blocked
            assert not first.done() and not second.done()
        finally:
            release.set()
        outcomes = first.result(8), second.result(8)
    expected = "ok" if first_action != "checkout" else (
        "housing_stale_version" if second_action == "complete" else "housing_room_pending_cleaning"
    )
    assert outcomes == ("ok", expected)
    with Session(postgres_engine) as db:
        assert db.get(HousingRoom, room_id).cleaning_status == "pending"
        assert db.get(HousingStay, stay_id).status == "checked_out"
        if "confirm" in (first_action, second_action):
            assert db.get(HousingStay, planned_id).status == (
                "checked_in" if first_action == "confirm" else "planned"
            )


def test_second_checkout_invalidates_stale_cleaning_completion(db):
    from app.housing.cleaning_service import HousingCleaningService

    room = _room(db, capacity=3)
    stays = []
    for index in range(3):
        person, actor = _account_person(db, name=f"保洁多人房{index}")
        stays.append(_stay(db, person_id=person.id, actor_id=actor, room=room,
                           status="checkout_pending" if index < 2 else "checked_in",
                           request_fields=index < 2))
    service = HousingOccupancyService(db)
    for index in range(2):
        service.confirm_checkout(stays[index].id, CheckoutConfirmation(version=1),
                                 actor_user_id=actor, occurred_at=NOW + timedelta(minutes=index),
                                 authorize_room=_allow)
        if index == 0:
            seen_version = room.version
    assert room.version > seen_version
    with pytest.raises(HousingServiceError) as error:
        HousingCleaningService(db).complete(room.id, version=seen_version,
            actor_user_id=actor, occurred_at=NOW + timedelta(minutes=2), authorize_room=_allow)
    assert error.value.code == "housing_stale_version"
    db.expire_all()
    assert db.get(HousingRoom, room.id).cleaning_status == "pending"
    assert db.get(HousingStay, stays[2].id).status == "checked_in"
