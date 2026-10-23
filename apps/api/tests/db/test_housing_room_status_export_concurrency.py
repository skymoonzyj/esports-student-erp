from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount
from app.core.errors import ProblemError
from app.housing.models import HousingRoom, HousingStay, HousingTransfer
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.room_status_export_schemas import HousingRoomStatusExportRequest
from app.housing.room_status_export_service import HousingRoomStatusExportService
from app.housing.schemas import TransferStayRequest
from app.identity.models import Person, StudentProfile
from app.security.encryption import LookupDigest
from tests.db.test_housing_reporting_concurrency import (
    _cleanup_context,
    _identity_for,
    _seed_context,
    concurrent_context,
)

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
snapshot_context = concurrent_context


def _rows(context, **filters):
    with Session(context.engine) as db:
        return HousingRoomStatusExportService(
            db,
            cipher=context.cipher,
            lookup=LookupDigest(b"c" * 32),
            actor_user_id=context.actor_id,
        ).rows(
            HousingRoomStatusExportRequest(
                purpose="虚拟快照核对",
                building_ids=filters.pop("building_ids", [context.building_id]),
                **filters,
            ),
            as_of=NOW,
        )


def _add_empty_room(context):
    with Session(context.engine) as db:
        room = HousingRoom(
            id=uuid4(),
            building_id=context.building_id,
            floor_id=context.floor_id,
            room_number="虚拟空房",
            normalized_key="虚拟空房",
            room_type="double",
            capacity=2,
            verification_status="verified",
            service_status="available",
            daily_cleaning_enabled=True,
        )
        db.add(room)
        db.commit()
        return room.id


def test_snapshot_includes_empty_rooms_and_only_current_people(snapshot_context):
    context = snapshot_context
    _add_empty_room(context)
    with Session(context.engine) as db:
        stay = db.scalar(select(HousingStay).where(HousingStay.person_id == context.resident_id))
        stay.status = "checkout_pending"
        db.get(Person, context.resident_id).full_name = "虚拟住客甲"
        db.get(Person, context.guardian_id).full_name = "虚拟住客乙"
        actor_person_id = db.get(UserAccount, context.actor_id).person_id
        db.get(Person, actor_person_id).full_name = "虚拟历史住客"
        historical_args = dict(  # noqa: C408
            person_id=actor_person_id,
            room_id=stay.room_id,
            stay_purpose="permanent",
            duration_mode="long_term",
            room_name_snapshot=stay.room_name_snapshot,
            floor_name_snapshot=stay.floor_name_snapshot,
            building_name_snapshot=stay.building_name_snapshot,
            created_by_user_id=context.actor_id,
        )
        for voided in (False, True):
            db.add(
                HousingStay(
                    id=uuid4(),
                    **historical_args,
                    planned_check_in_at=NOW - timedelta(days=100),
                    actual_check_in_at=NOW - timedelta(days=100),
                    actual_check_out_at=NOW - timedelta(days=99),
                    status="checked_out",
                    checked_in_by_user_id=context.actor_id,
                    checked_out_by_user_id=context.actor_id,
                    voided_at=NOW if voided else None,
                    voided_by_user_id=context.actor_id if voided else None,
                    void_reason_ciphertext="isolated-test-void" if voided else None,
                )
            )
        db.add(
            HousingStay(
                id=uuid4(),
                **historical_args,
                planned_check_in_at=NOW + timedelta(days=2),
                status="planned",
            )
        )
        db.add(
            HousingStay(
                id=uuid4(),
                person_id=context.guardian_id,
                room_id=stay.room_id,
                stay_purpose="permanent",
                duration_mode="long_term",
                planned_check_in_at=NOW - timedelta(days=2),
                actual_check_in_at=NOW - timedelta(days=2),
                status="checked_in",
                room_name_snapshot=stay.room_name_snapshot,
                floor_name_snapshot=stay.floor_name_snapshot,
                building_name_snapshot=stay.building_name_snapshot,
                created_by_user_id=context.actor_id,
                checked_in_by_user_id=context.actor_id,
            )
        )
        db.commit()
    try:
        rows = _rows(context)
        empty_rows = [row for row in rows if row.room_number == "虚拟空房"]
        occupied_rows = [row for row in rows if row.room_number != "虚拟空房"]
        assert len(empty_rows) == 1 and empty_rows[0].person_name is None
        assert len(occupied_rows) == 2
        assert {row.person_name for row in occupied_rows} == {"虚拟住客甲", "虚拟住客乙"}
        assert all(row.current_occupants == 2 for row in occupied_rows)
        assert len(_rows(context, daily_cleaning_enabled=True)) == 1
        assert len(_rows(context, daily_cleaning_enabled=False)) == 2
    finally:
        # Only the additional synthetic guardian stay; fixture owns its original stay.
        with Session(context.engine) as db:
            # Synthetic histories include immutable void facts; use the fixture cleanup
            # for those after removing our additional person-owned current stay.
            db.execute(delete(HousingStay).where(HousingStay.person_id == context.guardian_id))
            from sqlalchemy import text

            db.execute(
                text(
                    "ALTER TABLE housing_stays DISABLE TRIGGER trg_housing_stays_void_fact_immutable"
                )
            )
            db.execute(delete(HousingStay).where(HousingStay.person_id == actor_person_id))
            db.execute(
                text(
                    "ALTER TABLE housing_stays ENABLE TRIGGER trg_housing_stays_void_fact_immutable"
                )
            )
            db.commit()


def test_multi_building_or_and_dimensions_export_all_pages(snapshot_context):
    context = snapshot_context
    second = _seed_context(context.engine)
    try:
        with Session(context.engine) as db:
            for index in range(25):
                db.add(
                    HousingRoom(
                        id=uuid4(),
                        building_id=context.building_id,
                        floor_id=context.floor_id,
                        room_number=f"分页虚拟{index:03}",
                        normalized_key=f"分页虚拟{index:03}",
                        room_type="double",
                        capacity=2,
                        verification_status="verified",
                        service_status="available",
                        daily_cleaning_enabled=index != 0,
                    )
                )
            other = db.scalar(select(HousingRoom).where(HousingRoom.floor_id == second.floor_id))
            other.room_type, other.capacity, other.daily_cleaning_enabled = "double", 2, True
            db.commit()
        filters = dict(  # noqa: C408
            building_ids=[context.building_id, second.building_id],
            floor_ids=[context.floor_id, second.floor_id],
            room_types=["double"],
        )
        rows = _rows(context, **filters)
        assert len(rows) == 26
        assert len({row.building_name for row in rows}) == 2
        assert len(_rows(context, **filters, daily_cleaning_enabled=True)) == 25
        assert (
            len(_rows(context, **filters, daily_cleaning_enabled=True, occupancy="unoccupied"))
            == 24
        )
    finally:
        _cleanup_context(second)


def test_snapshot_remains_consistent_during_transfer_and_person_edit(snapshot_context, monkeypatch):
    context = snapshot_context
    target_id = _add_empty_room(context)
    projection_read, mutation_done = Event(), Event()
    original = HousingOccupancyService.room_projections

    def paused_projection(self, room_ids, as_of):
        result = original(self, room_ids, as_of)
        projection_read.set()
        assert mutation_done.wait(15), "concurrent mutation did not commit"
        return result

    monkeypatch.setattr(HousingOccupancyService, "room_projections", paused_projection)
    with Session(context.engine) as db:
        resident = db.get(Person, context.resident_id)
        original_name = resident.full_name
        # Reporting's synthetic fixture predates the current verified-email stay
        # policy. Make only our disposable resident transfer-ready, without mocks.
        resident.email_ciphertext = context.cipher.encrypt(
            "snapshot-resident@example.test",
            field_name="email",
        )
        resident.email_digest = uuid4().hex + uuid4().hex
        resident.date_of_birth = date(1990, 1, 2)
        resident.identity_ciphertext = context.cipher.encrypt(
            _identity_for(resident.date_of_birth, male=True),
            field_name="identity_number",
        )
        db.get(StudentProfile, context.resident_id).status = "active"
        db.add(
            AccountLoginIdentifier(
                account_id=context.resident_account_id,
                kind="email",
                identifier_digest=uuid4().hex + uuid4().hex,
                verified_at=NOW,
                verification_source="isolated_snapshot_test",
                is_primary=False,
            )
        )
        db.commit()

    def mutate():
        assert projection_read.wait(15), "snapshot was never established"
        try:
            with Session(context.engine) as db:
                stay = db.scalar(
                    select(HousingStay).where(
                        HousingStay.person_id == context.resident_id,
                        HousingStay.status == "checked_in",
                    )
                )
                HousingOccupancyService(db).transfer(
                    stay.id,
                    TransferStayRequest(
                        version=stay.version,
                        target_room_id=target_id,
                        transferred_at=NOW,
                        reason="虚拟双连接迁房验证",
                    ),
                    actor_user_id=context.actor_id,
                    occurred_at=NOW,
                    authorize_source=lambda room: None,
                    authorize_target=lambda room: None,
                )
                db.get(Person, context.resident_id).full_name = "并发改名后的虚拟住客"
                db.commit()
        finally:
            mutation_done.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            mutation = pool.submit(mutate)
            rows = _rows(context)
            mutation.result(timeout=20)
    finally:
        with Session(context.engine) as db:
            db.execute(
                delete(HousingTransfer).where(HousingTransfer.person_id == context.resident_id)
            )
            db.commit()
    occupied = [row for row in rows if row.person_name is not None]
    assert len(occupied) == 1
    assert occupied[0].person_name == original_name
    assert occupied[0].room_number != "虚拟空房"
    assert occupied[0].current_occupants == 1
    assert next(row for row in rows if row.room_number == "虚拟空房").current_occupants == 0


@pytest.mark.parametrize("limit_name", ["MAX_EXPORT_ROOMS", "MAX_EXPORT_ROWS"])
def test_snapshot_limits_reject_instead_of_truncating(snapshot_context, monkeypatch, limit_name):
    import app.housing.room_status_export_service as module

    _add_empty_room(snapshot_context)
    monkeypatch.setattr(module, limit_name, 1)
    with pytest.raises(ProblemError) as exc:
        _rows(snapshot_context)
    assert exc.value.code == "housing_export_limit_exceeded"
