from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.housing import models
from app.housing.schemas import HousingAllowedActions, RoomCreate, RoomResponse, RoomUpdate
from tests.db.test_housing_history_void_migration import cleanup, seed
from tests.db.test_migrations import _alembic_config

HEAD = "0071_housing_room_care"
PREVIOUS = "0070_housing_history_void"


def require_revision(engine):
    config = _alembic_config(engine)
    assert ScriptDirectory.from_config(config).get_heads() == [HEAD], (
        "room care migration is missing"
    )
    return config


@pytest.fixture
def care_data(postgres_engine):
    require_revision(postgres_engine)
    data = seed(postgres_engine)
    try:
        yield data
    finally:
        cleanup(postgres_engine, data)


def insert_photo(connection, *, room, actor, slot=1, **changes):
    values = {
        "id": uuid4(),
        "room_id": room,
        "slot": slot,
        "content": b"original",
        "thumbnail_content": b"thumb",
        "mime_type": "image/jpeg",
        "thumbnail_mime_type": "image/webp",
        "byte_size": 8,
        "thumbnail_byte_size": 5,
        "sha256": "a" * 64,
        "uploaded_by_user_id": actor,
    }
    values.update(changes)
    connection.execute(
        text(
            "INSERT INTO housing_room_photos ("
            + ",".join(values)
            + ") VALUES ("
            + ",".join(":" + key for key in values)
            + ")"
        ),
        values,
    )
    return values["id"]


def test_upgrade_preserves_existing_room_and_stays(postgres_engine, care_data):
    config = require_revision(postgres_engine)
    person, _, stays, room, *_ = care_data
    command.downgrade(config, PREVIOUS)
    queries = {}
    before = {}
    for table, identifier in (
        ("housing_rooms", room),
        ("people", person),
        ("housing_stays", stays[0]),
        ("user_accounts", care_data[1]),
    ):
        columns = ",".join(
            f'"{column["name"]}"' for column in inspect(postgres_engine).get_columns(table)
        )
        queries[table] = (text(f"SELECT {columns} FROM {table} WHERE id=:id"), identifier)
    # Both a completed stay and an active reservation must survive unchanged.
    queries["all_stays"] = (
        text("SELECT * FROM housing_stays WHERE person_id=:id ORDER BY id"),
        person,
    )
    try:
        with postgres_engine.connect() as conn:
            for table, (query, identifier) in queries.items():
                before[table] = conn.execute(query, {"id": identifier}).all()
        command.upgrade(config, HEAD)
        with postgres_engine.connect() as conn:
            for table, (query, identifier) in queries.items():
                assert conn.execute(query, {"id": identifier}).all() == before[table]
        with Session(postgres_engine) as db:
            upgraded = db.get(models.HousingRoom, room)
            assert upgraded.daily_cleaning_enabled is False
            assert upgraded.cleaning_status == "ready"
            assert upgraded.cleaning_pending_at is upgraded.cleaning_completed_at is None
            assert upgraded.cleaning_completed_by_user_id is None
            assert db.scalar(select(models.HousingRoomPhoto.id)) is None
    finally:
        command.upgrade(config, HEAD)


def test_room_photo_slot_constraints(postgres_engine, care_data):
    _, actor, _, room, *_ = care_data
    with postgres_engine.begin() as conn:
        for slot in (1, 2, 3):
            insert_photo(conn, room=room, actor=actor, slot=slot)
    with postgres_engine.connect() as conn:
        assert conn.execute(
            text("SELECT slot FROM housing_room_photos WHERE room_id=:id ORDER BY slot"),
            {"id": room},
        ).scalars().all() == [1, 2, 3]
    for slot in (0, 4, 1):
        with pytest.raises(IntegrityError), postgres_engine.begin() as conn:
            insert_photo(conn, room=room, actor=actor, slot=slot)


@pytest.mark.parametrize(
    "changes",
    [
        {"byte_size": 0, "content": b""},
        {"thumbnail_byte_size": 0, "thumbnail_content": b""},
        {"content": b"wrong"},
        {"thumbnail_content": b"wrong-length"},
        {"mime_type": "image/svg+xml"},
        {"thumbnail_mime_type": "text/html"},
        {"sha256": "invalid"},
    ],
)
def test_photo_content_metadata_constraints(postgres_engine, care_data, changes):
    _, actor, _, room, *_ = care_data
    with pytest.raises(IntegrityError), postgres_engine.begin() as conn:
        insert_photo(conn, room=room, actor=actor, **changes)


@pytest.mark.parametrize("variant", ["original", "thumbnail"])
def test_photo_byte_cap_checks_actual_content(postgres_engine, care_data, variant):
    _, actor, _, room, *_ = care_data
    content_key = "content" if variant == "original" else "thumbnail_content"
    size_key = "byte_size" if variant == "original" else "thumbnail_byte_size"
    changes = {content_key: b"x" * 10485760, size_key: 10485760}
    with postgres_engine.begin() as conn:
        insert_photo(conn, room=room, actor=actor, **changes)
    changes = {content_key: b"x" * 10485761, size_key: 10485761}
    with pytest.raises(IntegrityError), postgres_engine.begin() as conn:
        insert_photo(conn, room=room, actor=actor, slot=2, **changes)


def test_photo_blobs_are_independently_deferred(postgres_engine, care_data):
    _, actor, _, room, *_ = care_data
    with postgres_engine.begin() as conn:
        photo_id = insert_photo(conn, room=room, actor=actor)
    with Session(postgres_engine) as db:
        photo = db.get(models.HousingRoomPhoto, photo_id)
        assert {"content", "thumbnail_content"} <= inspect(photo).unloaded
        assert photo.content == b"original"
        assert "thumbnail_content" in inspect(photo).unloaded
        assert photo.thumbnail_content == b"thumb"


@pytest.mark.parametrize("fact", ["photo", "daily", "pending", "completed"])
def test_downgrade_rejects_existing_care_facts_or_photos(postgres_engine, care_data, fact):
    config = require_revision(postgres_engine)
    _, actor, _, room, *_ = care_data
    with postgres_engine.begin() as conn:
        if fact == "photo":
            insert_photo(conn, room=room, actor=actor)
        elif fact == "daily":
            conn.execute(
                text("UPDATE housing_rooms SET daily_cleaning_enabled=true WHERE id=:id"),
                {"id": room},
            )
        elif fact == "pending":
            conn.execute(
                text(
                    "UPDATE housing_rooms SET cleaning_status='pending',cleaning_pending_at=now() WHERE id=:id"
                ),
                {"id": room},
            )
        else:
            conn.execute(
                text(
                    "UPDATE housing_rooms SET cleaning_pending_at=now(),cleaning_completed_at=now(),cleaning_completed_by_user_id=:actor WHERE id=:id"
                ),
                {"id": room, "actor": actor},
            )
    with pytest.raises(
        RuntimeError, match="cannot downgrade room care with existing care facts or photos"
    ):
        command.downgrade(config, PREVIOUS)
    with postgres_engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        assert "housing_room_photos" in inspect(conn).get_table_names()
        room_data = (
            conn.execute(text("SELECT * FROM housing_rooms WHERE id=:id"), {"id": room})
            .mappings()
            .one()
        )
        if fact == "photo":
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM housing_room_photos WHERE room_id=:id"), {"id": room}
                )
                == 1
            )
        elif fact == "daily":
            assert room_data["daily_cleaning_enabled"] is True
        elif fact == "pending":
            assert room_data["cleaning_status"] == "pending"
            assert room_data["cleaning_pending_at"] is not None
        else:
            assert room_data["cleaning_completed_at"] is not None
            assert room_data["cleaning_completed_by_user_id"] == actor


@pytest.mark.parametrize(
    "assignment",
    [
        "cleaning_status='unknown'",
        "cleaning_status='pending'",
        "cleaning_pending_at=now()",
        "cleaning_completed_at=now()",
        "cleaning_completed_by_user_id=:actor",
    ],
)
def test_cleaning_fact_constraints(postgres_engine, care_data, assignment):
    _, actor, _, room, *_ = care_data
    with pytest.raises(IntegrityError), postgres_engine.begin() as conn:
        conn.execute(
            text(f"UPDATE housing_rooms SET {assignment} WHERE id=:id"),
            {"id": room, "actor": actor},
        )


def test_room_delete_cascades_photos_but_stays_still_restrict_deletion(postgres_engine, care_data):
    _, actor, stays, room, *_ = care_data
    with postgres_engine.begin() as conn:
        insert_photo(conn, room=room, actor=actor)
    with pytest.raises(IntegrityError), postgres_engine.begin() as conn:
        conn.execute(text("DELETE FROM housing_rooms WHERE id=:id"), {"id": room})
    with postgres_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM housing_stays WHERE id IN (:a,:b)"), {"a": stays[0], "b": stays[1]}
        )
        conn.execute(text("DELETE FROM housing_rooms WHERE id=:id"), {"id": room})
        assert (
            conn.scalar(
                text("SELECT count(*) FROM housing_room_photos WHERE room_id=:id"), {"id": room}
            )
            == 0
        )


def test_room_care_contract_preserves_old_payload_defaults_and_update_omission():
    assert (
        RoomCreate(
            floor_id=uuid4(), room_number="101", room_type="single", capacity=1
        ).daily_cleaning_enabled
        is False
    )
    assert (
        RoomCreate(
            floor_id=uuid4(),
            room_number="101",
            room_type="single",
            capacity=1,
            daily_cleaning_enabled=True,
        ).daily_cleaning_enabled
        is True
    )
    assert "daily_cleaning_enabled" not in RoomUpdate(
        version=1, notes="unchanged daily option"
    ).model_dump(exclude_unset=True)
    assert (
        RoomUpdate(version=1, daily_cleaning_enabled=False).model_dump(exclude_unset=True)[
            "daily_cleaning_enabled"
        ]
        is False
    )
    now = datetime.now(UTC)
    response = RoomResponse(
        id=uuid4(),
        building_id=uuid4(),
        floor_id=uuid4(),
        room_number="101",
        room_type="single",
        capacity=1,
        area_square_meters=None,
        verification_status="verified",
        service_status="available",
        notes=None,
        version=1,
        created_at=now,
        updated_at=now,
    )
    assert response.daily_cleaning_enabled is False
    assert response.cleaning_status == "ready"
    assert response.cleaning_pending_at is response.cleaning_completed_at is None
    assert response.photo_count == 0
    actions = HousingAllowedActions(edit=True, delete=False, verify=False)
    assert actions.manage_care is actions.complete_cleaning is actions.manage_photos is False
