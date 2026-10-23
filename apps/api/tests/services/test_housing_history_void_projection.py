from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.housing.errors import HousingCatalogHasChildren
from app.housing.inventory_service import HousingInventoryService
from app.housing.models import HousingRoom, HousingStay
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.reporting_projection import reporting_candidate_statement
from app.identity.models import Person
from tests.invited_housing_helpers import NOW, account, person, stay


def mark_void(db, row, actor):
    row.voided_at = NOW
    row.voided_by_user_id = actor.id
    row.void_reason_ciphertext = "opaque-encrypted-test-reason"
    row.version += 1
    db.flush()


def test_voided_history_is_excluded_before_reporting_candidate_selection(db):
    actor, resident = account(db), person(db)
    history = stay(
        db,
        actor,
        resident,
        status="checked_out",
        start=NOW - timedelta(days=3),
        actual_end=NOW - timedelta(days=1),
    )
    room = db.get(HousingRoom, history.room_id)
    query = reporting_candidate_statement(
        floor_ids=[room.floor_id],
        range_start=NOW - timedelta(days=4),
        range_end=NOW,
    )
    assert [row[0].id for row in db.execute(query)] == [history.id]
    mark_void(db, history, actor)
    assert [row[0].id for row in db.execute(query)] == []


def test_voided_history_does_not_block_transfer_capacity_lock_candidates(db):
    actor, resident = account(db), person(db)
    history = stay(
        db,
        actor,
        resident,
        status="checked_out",
        start=NOW - timedelta(days=3),
        actual_end=NOW - timedelta(days=1),
    )
    mark_void(db, history, actor)
    candidates = HousingOccupancyService(db).lock_transfer_stays(
        person_id=resident.id,
        room_ids=[history.room_id],
        target_room_id=history.room_id,
        target_start=NOW - timedelta(days=2),
        target_end=NOW,
    )
    assert history.id not in {row.id for row in candidates}


def test_voided_history_is_excluded_from_sensitive_occupancy_export(db):
    from app.housing.access import HousingAccessService
    from app.housing.occupancy_export_service import HousingOccupancyExportService
    from app.housing.schemas import HousingOccupancyExportRequest
    from app.security.encryption import EnvelopeCipher, LookupDigest

    actor, resident = account(db, role="super_admin"), person(db)
    cipher = EnvelopeCipher(bytes(range(32)))
    resident.date_of_birth = date(1949, 12, 31)
    resident.identity_ciphertext = cipher.encrypt(
        "11010519491231002X", field_name="identity_number"
    )
    resident.phone_ciphertext = cipher.encrypt("13800138000", field_name="phone")
    history = stay(
        db,
        actor,
        resident,
        status="checked_out",
        start=NOW - timedelta(days=3),
        actual_end=NOW - timedelta(days=1),
    )
    room = db.get(HousingRoom, history.room_id)
    service = HousingOccupancyExportService(
        db,
        cipher=cipher,
        lookup=LookupDigest(bytes(range(32))),
        access=HousingAccessService(db, actor.id, NOW),
    )
    request = HousingOccupancyExportRequest(purpose="历史导出测试", floor_id=room.floor_id)
    assert len(service.rows(request, as_of=NOW)) == 1
    mark_void(db, history, actor)
    assert service.rows(request, as_of=NOW) == []


def test_voided_interval_does_not_reduce_advisory_transfer_capacity(db):
    from app.housing.access import HousingAccessService

    actor, resident = account(db, role="super_admin"), person(db)
    source = stay(db, actor, resident, status="checked_in", start=NOW - timedelta(days=5))
    history = stay(
        db,
        actor,
        person(db),
        status="checked_out",
        start=NOW - timedelta(days=4),
        actual_end=NOW - timedelta(days=1),
    )
    target = db.get(HousingRoom, history.room_id)
    target.verification_status = "verified"
    target.service_status = "available"
    db.flush()
    occurred_at = NOW - timedelta(days=2)
    service = HousingOccupancyService(db)
    access = HousingAccessService(db, actor.id, occurred_at)
    before = service.list_transfer_options(source.id, occurred_at=occurred_at, access=access)
    assert (
        next(item for item in before.items if item.room_id == target.id).advisory_compatible
        is False
    )
    mark_void(db, history, actor)
    after = service.list_transfer_options(source.id, occurred_at=occurred_at, access=access)
    option = next(item for item in after.items if item.room_id == target.id)
    assert option.advisory_compatible is True
    assert option.current_occupants == 0
    assert option.capacity == 1


def test_void_history_still_blocks_room_and_person_deletion(db):
    actor, resident = account(db), person(db)
    history = stay(
        db,
        actor,
        resident,
        status="checked_out",
        start=NOW - timedelta(days=3),
        actual_end=NOW - timedelta(days=1),
    )
    mark_void(db, history, actor)
    db.commit()
    with pytest.raises(HousingCatalogHasChildren):
        HousingInventoryService(db).delete_room(history.room_id)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(delete(Person).where(Person.id == resident.id))
        db.flush()
    assert db.scalar(select(HousingStay.id).where(HousingStay.id == history.id)) == history.id
