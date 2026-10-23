from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from threading import Event
from time import monotonic, sleep

import pytest
from PIL import Image
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount
from app.housing.errors import HousingStaleVersion
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingRoomPhoto
from app.housing.room_photo_images import prepare_room_photo
from app.housing.room_photo_service import HousingRoomPhotoService
from app.identity.models import Person, StaffProfile
from tests.db.test_housing_issue_service import _room, _staff_account


def _prepared():
    output = BytesIO()
    Image.new("RGB", (20, 10), "blue").save(output, format="PNG")
    return prepare_room_photo(output.getvalue(), "image/png")


@pytest.fixture
def committed_photo_room(postgres_engine):
    with Session(postgres_engine) as db:
        person_id, account_id = _staff_account(db, name="照片并发测试管理员")
        room = _room(db)
        room_id, floor_id, building_id = room.id, room.floor_id, room.building_id
        db.commit()
    try:
        yield account_id, room_id
    finally:
        with Session(postgres_engine) as db, db.begin():
            database = db.scalar(text("SELECT current_database()"))
            assert database.endswith("_test") or database == "erp_e2e"
            db.execute(delete(HousingRoomPhoto).where(HousingRoomPhoto.room_id == room_id))
            db.execute(delete(HousingRoom).where(HousingRoom.id == room_id))
            db.execute(delete(HousingFloor).where(HousingFloor.id == floor_id))
            db.execute(delete(HousingBuilding).where(HousingBuilding.id == building_id))
            db.execute(
                delete(AccountLoginIdentifier).where(
                    AccountLoginIdentifier.account_id == account_id
                )
            )
            db.execute(delete(StaffProfile).where(StaffProfile.person_id == person_id))
            db.execute(delete(UserAccount).where(UserAccount.id == account_id))
            db.execute(delete(Person).where(Person.id == person_id))


def test_same_slot_concurrent_writes_have_one_winner(postgres_engine, committed_photo_room):
    actor_id, room_id = committed_photo_room
    prepared = _prepared()
    first_locked, contender_waiting, release_first = Event(), Event(), Event()
    backend_pids = {}

    def run(first):
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '6s'"))
            backend_pids[first] = db.scalar(text("SELECT pg_backend_pid()"))
            connection = db.connection()

            def observe(_connection, _cursor, statement, _params, _ctx, _many):
                if "FROM housing_rooms" in statement and "FOR UPDATE" in statement:
                    if first:
                        first_locked.set()
                        assert release_first.wait(timeout=5)
                    else:
                        contender_waiting.set()

            event_name = "after_cursor_execute" if first else "before_cursor_execute"
            event.listen(connection, event_name, observe)
            try:
                photo = HousingRoomPhotoService(db).put(
                    room_id,
                    1,
                    prepared,
                    version=1,
                    actor_user_id=actor_id,
                    occurred_at=datetime.now(UTC),
                    authorize_room=lambda room: None,
                )
                return photo.id
            except HousingStaleVersion:
                return "housing_stale_version"
            finally:
                event.remove(connection, event_name, observe)

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(run, True)
        try:
            assert first_locked.wait(timeout=3)
            two = pool.submit(run, False)
            assert contender_waiting.wait(timeout=3)
            blocked = False
            with Session(postgres_engine) as observer:
                deadline = monotonic() + 2
                while monotonic() < deadline:
                    blockers = observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": backend_pids[False]}
                    )
                    if backend_pids[True] in blockers:
                        blocked = True
                        break
                    sleep(0.01)
            assert blocked, "second connection never blocked on the shared room row"
            assert not one.done() and not two.done()
        finally:
            release_first.set()
        outcomes = [one.result(timeout=8), two.result(timeout=8)]
    assert outcomes.count("housing_stale_version") == 1
    with Session(postgres_engine) as db:
        photos = list(
            db.scalars(select(HousingRoomPhoto).where(HousingRoomPhoto.room_id == room_id))
        )
        assert len(photos) == 1
        assert photos[0].slot == 1
        assert db.get(HousingRoom, room_id).version == 2


def test_db_itself_rejects_fourth_slot_and_duplicate_slot(db):
    _, actor_id = _staff_account(db, name="照片槽位测试管理员")
    room_id = _room(db).id
    prepared = _prepared()
    for slot in (1, 2, 3):
        HousingRoomPhotoService(db).put(
            room_id,
            slot,
            prepared,
            version=slot,
            actor_user_id=actor_id,
            occurred_at=datetime.now(UTC),
            authorize_room=lambda room: None,
        )
    for slot, constraint in [
        (4, "ck_housing_room_photos_slot"),
        (3, "uq_housing_room_photos_room_slot"),
    ]:
        with pytest.raises(IntegrityError) as error, db.begin_nested():
            db.add(
                HousingRoomPhoto(
                    room_id=room_id,
                    slot=slot,
                    content=prepared.content,
                    thumbnail_content=prepared.thumbnail_content,
                    mime_type=prepared.mime_type,
                    thumbnail_mime_type=prepared.thumbnail_mime_type,
                    byte_size=prepared.byte_size,
                    thumbnail_byte_size=prepared.thumbnail_byte_size,
                    sha256=prepared.sha256,
                    uploaded_by_user_id=actor_id,
                )
            )
            db.flush()
        assert error.value.orig.diag.constraint_name == constraint


def test_room_epoch_prevents_delete_recreate_aba(db):
    _, actor_id = _staff_account(db, name="照片版本测试管理员")
    room_id = _room(db).id
    service = HousingRoomPhotoService(db)
    kwargs = {
        "actor_user_id": actor_id,
        "occurred_at": datetime.now(UTC),
        "authorize_room": lambda room: None,
    }
    first = service.put(room_id, 1, _prepared(), version=1, **kwargs)
    old_id = first.id
    service.delete(room_id, old_id, version=2, **kwargs)
    second = service.put(room_id, 1, _prepared(), version=3, **kwargs)
    assert second.id != old_id
    with pytest.raises(HousingStaleVersion):
        service.put(room_id, 1, _prepared(), version=1, **kwargs)
    assert db.get(HousingRoom, room_id).version == 4
    assert db.get(HousingRoomPhoto, second.id) is not None
