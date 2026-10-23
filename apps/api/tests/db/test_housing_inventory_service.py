from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.housing.errors import (
    HousingCatalogHasChildren,
    HousingDuplicateCatalogEntry,
    HousingInvalidRoomConfiguration,
    HousingIssueWorkflowRequired,
    HousingParentInactive,
    HousingRoomHasActiveStays,
    HousingStaleVersion,
)
from app.housing.inventory_service import HousingInventoryService
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.occupancy_service import RoomOccupancyProjection
from app.housing.schemas import (
    BuildingCreate,
    BuildingUpdate,
    FloorCreate,
    FloorUpdate,
    RoomCreate,
    RoomIssueSummary,
    RoomUpdate,
    RoomVersionInput,
    StaySummary,
)
from app.housing.seed_inventory import FLOOR_SEEDS, INITIAL_BUILDINGS, INITIAL_ROOMS
from app.identity.models import Person


@pytest.fixture
def housing_actor_id(db: Session) -> UUID:
    person_id = uuid4()
    account_id = uuid4()
    db.add(
        Person(
            id=person_id,
            full_name="房态目录测试操作人",
            identity_ciphertext="housing-test-ciphertext",
            identity_digest=f"housing-{person_id}",
            sex="female",
            date_of_birth=date(1990, 1, 2),
        )
    )
    db.flush()
    db.add(
        UserAccount(
            id=account_id,
            person_id=person_id,
            login_phone_digest=f"housing-phone-{account_id}",
            email_normalized=f"housing-{account_id}@example.test",
            password_hash="housing-test-password-hash",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
    )
    db.flush()
    db.commit()
    return account_id


def _create_building_and_floor(
    service: HousingInventoryService,
    *,
    actor_id: UUID,
    code: str,
    floor_number: int = 2,
) -> tuple[HousingBuilding, HousingFloor]:
    building = service.create_building(
        BuildingCreate(code=code, name=f"{code} 测试楼"),
        actor_user_id=actor_id,
    )
    floor = service.create_floor(
        FloorCreate(
            building_id=building.id,
            number=floor_number,
            name=f"{floor_number} 楼",
        ),
        actor_user_id=actor_id,
    )
    return building, floor


class _RoomCardAccess:
    def can_manage_room(self, room: HousingRoom) -> bool:
        del room
        return False


def test_room_card_projects_pending_transfers_only_for_an_open_issue() -> None:
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    building_id = uuid4()
    floor_id = uuid4()
    building = HousingBuilding(
        id=building_id,
        code="ISSUECARD",
        name="问题房卡片测试楼",
        display_order=0,
        is_active=True,
    )
    floor = HousingFloor(
        id=floor_id,
        building_id=building_id,
        number=2,
        name="二楼",
        display_order=0,
        is_active=True,
    )
    room = HousingRoom(
        id=uuid4(),
        building_id=building_id,
        floor_id=floor_id,
        room_number="ISSUECARD-201",
        normalized_key="ISSUECARD-201",
        room_type="double",
        capacity=2,
        area_square_meters=None,
        verification_status="verified",
        service_status="problem",
        notes=None,
        version=2,
        created_at=now,
        updated_at=now,
    )
    occupants = (
        StaySummary.model_construct(sex="female"),
        StaySummary.model_construct(sex="female"),
    )
    open_projection = RoomOccupancyProjection(
        current_stays=occupants,
        future_reservation_count=0,
        open_issue=RoomIssueSummary.model_construct(),
    )
    resolved_projection = RoomOccupancyProjection(
        current_stays=occupants,
        future_reservation_count=0,
        open_issue=None,
    )

    open_card = HousingInventoryService._room_card(
        room,
        building,
        floor,
        _RoomCardAccess(),
        open_projection,
    )
    resolved_card = HousingInventoryService._room_card(
        room,
        building,
        floor,
        _RoomCardAccess(),
        resolved_projection,
    )

    assert open_card.pending_transfer_count == open_card.current_occupants == 2
    assert resolved_card.pending_transfer_count == 0


def test_catalog_creates_normalized_building_floor_and_every_room_type(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    building = service.create_building(
        BuildingCreate(code=" ｔ ", name=" Ｔ 测试楼 ", notes=" 新楼 "),
        actor_user_id=actor_id,
    )
    floor = service.create_floor(
        FloorCreate(building_id=building.id, number=2, name=" 二楼 "),
        actor_user_id=actor_id,
    )
    configurations = [
        ("single", 1),
        ("double", 2),
        ("triple", 3),
        ("custom", 4),
        ("functional", 0),
    ]
    rooms = [
        service.create_room(
            RoomCreate(
                floor_id=floor.id,
                room_number=f" ｔ - ２０{index + 1} ",
                room_type=room_type,
                capacity=capacity,
                area_square_meters=Decimal("28.50"),
            ),
            actor_user_id=actor_id,
        )
        for index, (room_type, capacity) in enumerate(configurations)
    ]

    assert building.code == "T"
    assert building.name == "T 测试楼"
    assert floor.name == "二楼"
    assert [room.room_number for room in rooms] == [
        "T-201",
        "T-202",
        "T-203",
        "T-204",
        "T-205",
    ]
    assert [room.normalized_key for room in rooms] == [room.room_number for room in rooms]
    assert [(room.room_type, room.capacity) for room in rooms] == configurations
    assert all(room.verification_status == "pending_verification" for room in rooms)
    assert all(room.version == 1 for room in rooms)


def test_catalog_sorts_letter_buildings_before_numeric_buildings_naturally(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    service = HousingInventoryService(db)
    for code, display_order in (("10", 1), ("B", 99_999), ("2", 50_000)):
        building = service.create_building(
            BuildingCreate(
                code=code,
                name=f"{code} 自然排序测试楼",
                display_order=display_order,
            ),
            actor_user_id=housing_actor_id,
        )
        floor = service.create_floor(
            FloorCreate(
                building_id=building.id,
                number=2,
                name="二楼",
            ),
            actor_user_id=housing_actor_id,
        )
        service.create_room(
            RoomCreate(
                floor_id=floor.id,
                room_number=f"{code}-201",
                room_type="single",
                capacity=1,
            ),
            actor_user_id=housing_actor_id,
        )

    buildings, _, rooms = service._catalog_rows()
    assert [building.code for building in buildings] == ["A", "B", "2", "4", "5", "9", "10"]

    building_codes = {building.id: building.code for building in buildings}
    room_building_order = list(
        dict.fromkeys(building_codes[room.building_id] for room in rooms)
    )
    assert room_building_order == ["A", "B", "2", "4", "5", "9", "10"]


def test_room_normalization_rejects_same_building_duplicate_across_floors(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    building, first_floor = _create_building_and_floor(
        service,
        actor_id=actor_id,
        code="DUP",
    )
    second_floor = service.create_floor(
        FloorCreate(building_id=building.id, number=3, name="三楼"),
        actor_user_id=actor_id,
    )
    first = service.create_room(
        RoomCreate(
            floor_id=first_floor.id,
            room_number=" ｄｕｐ - ３０１ ",
            room_type="single",
            capacity=1,
        ),
        actor_user_id=actor_id,
    )

    with pytest.raises(HousingDuplicateCatalogEntry):
        service.create_room(
            RoomCreate(
                floor_id=second_floor.id,
                room_number="dup-301",
                room_type="single",
                capacity=1,
            ),
            actor_user_id=actor_id,
        )

    assert first.normalized_key == "DUP-301"
    assert db.get(HousingRoom, first.id) is not None


def test_inactive_parents_reject_new_children(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    inactive_building = service.create_building(
        BuildingCreate(code="OFF", name="停用楼栋"),
        actor_user_id=actor_id,
    )
    service.update_building(
        inactive_building.id,
        BuildingUpdate(is_active=False),
        actor_user_id=actor_id,
    )
    with pytest.raises(HousingParentInactive):
        service.create_floor(
            FloorCreate(building_id=inactive_building.id, number=2, name="二楼"),
            actor_user_id=actor_id,
        )

    _, floor = _create_building_and_floor(service, actor_id=actor_id, code="FLOOROFF")
    service.update_floor(
        floor.id,
        FloorUpdate(is_active=False),
        actor_user_id=actor_id,
    )
    with pytest.raises(HousingParentInactive):
        service.create_room(
            RoomCreate(
                floor_id=floor.id,
                room_number="FLOOROFF-201",
                room_type="single",
                capacity=1,
            ),
            actor_user_id=actor_id,
        )

    parent, active_floor = _create_building_and_floor(
        service,
        actor_id=actor_id,
        code="PARENTOFF",
    )
    service.update_building(
        parent.id,
        BuildingUpdate(is_active=False),
        actor_user_id=actor_id,
    )
    with pytest.raises(HousingParentInactive):
        service.create_room(
            RoomCreate(
                floor_id=active_floor.id,
                room_number="PARENTOFF-201",
                room_type="single",
                capacity=1,
            ),
            actor_user_id=actor_id,
        )


def test_partial_room_update_enforces_merged_type_capacity(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    seed = next(item for item in INITIAL_ROOMS if item.room_type == "single")
    room = db.get(HousingRoom, seed.id)
    assert room is not None
    original = (room.room_type, room.capacity, room.version)

    with pytest.raises(HousingInvalidRoomConfiguration):
        HousingInventoryService(db).update_room(
            room.id,
            RoomUpdate(version=room.version, room_type="double"),
            actor_user_id=housing_actor_id,
        )

    db.expire_all()
    preserved = db.get(HousingRoom, seed.id)
    assert preserved is not None
    assert (preserved.room_type, preserved.capacity, preserved.version) == original


def test_structural_edit_resets_verification_but_notes_only_edit_keeps_it(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    functional = [item for item in INITIAL_ROOMS if item.room_type == "functional"][:2]
    structural = db.get(HousingRoom, functional[0].id)
    notes_only = db.get(HousingRoom, functional[1].id)
    assert structural is not None and notes_only is not None
    assert structural.verification_status == notes_only.verification_status == "verified"

    changed = service.update_room(
        structural.id,
        RoomUpdate(
            version=structural.version,
            room_number=f"{structural.room_number}-X",
        ),
        actor_user_id=actor_id,
    )
    annotated = service.update_room(
        notes_only.id,
        RoomUpdate(version=notes_only.version, notes="管理员复核备注"),
        actor_user_id=actor_id,
    )

    assert changed.verification_status == "pending_verification"
    assert changed.version == 2
    assert annotated.verification_status == "verified"
    assert annotated.notes == "管理员复核备注"
    assert annotated.version == 2


def test_catalog_editing_cannot_set_problem_status(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    seed = next(item for item in INITIAL_ROOMS if item.room_type == "single")
    room = db.get(HousingRoom, seed.id)
    assert room is not None
    unsafe = RoomUpdate.model_construct(
        version=room.version,
        service_status="problem",
    )

    with pytest.raises(HousingIssueWorkflowRequired):
        HousingInventoryService(db).update_room(
            room.id,
            unsafe,
            actor_user_id=housing_actor_id,
        )


def test_stale_update_and_verify_leave_room_unchanged(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    room_seed = next(item for item in INITIAL_ROOMS if item.room_type == "double")
    room = db.get(HousingRoom, room_seed.id)
    assert room is not None
    original = (room.notes, room.verification_status, room.version)

    with pytest.raises(HousingStaleVersion):
        service.update_room(
            room.id,
            RoomUpdate(version=room.version + 1, notes="过期修改"),
            actor_user_id=actor_id,
        )
    with pytest.raises(HousingStaleVersion):
        service.verify_room(
            room.id,
            expected_version=room.version + 1,
            actor_user_id=actor_id,
        )

    db.expire_all()
    preserved = db.get(HousingRoom, room_seed.id)
    assert preserved is not None
    assert (preserved.notes, preserved.verification_status, preserved.version) == original


def test_batch_verify_locks_sorted_ids_returns_request_order_and_is_atomic(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    seeds = [item for item in INITIAL_ROOMS if item.room_type != "functional"][:4]
    success_inputs = [
        RoomVersionInput(room_id=seeds[1].id, version=1),
        RoomVersionInput(room_id=seeds[0].id, version=1),
    ]
    statements: list[str] = []
    connection = db.connection()

    def capture_lock_sql(conn, cursor, statement, parameters, context, executemany):
        del conn, cursor, parameters, context, executemany
        if "housing_rooms" in statement and "FOR UPDATE" in statement:
            statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture_lock_sql)
    try:
        verified = service.verify_rooms(success_inputs, actor_user_id=actor_id)
    finally:
        event.remove(connection, "before_cursor_execute", capture_lock_sql)

    assert [room.id for room in verified] == [item.room_id for item in success_inputs]
    assert all(room.verification_status == "verified" and room.version == 2 for room in verified)
    assert any("ORDER BY housing_rooms.id" in statement for statement in statements)

    atomic_inputs = [
        RoomVersionInput(room_id=seeds[2].id, version=1),
        RoomVersionInput(room_id=seeds[3].id, version=999),
    ]
    with pytest.raises(HousingStaleVersion):
        service.verify_rooms(atomic_inputs, actor_user_id=actor_id)

    db.expire_all()
    untouched = [db.get(HousingRoom, item.room_id) for item in atomic_inputs]
    assert all(room is not None for room in untouched)
    assert all(
        room.verification_status == "pending_verification" and room.version == 1
        for room in untouched
        if room is not None
    )


def test_catalog_delete_rejects_children_then_succeeds_in_dependency_order(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    with pytest.raises(HousingCatalogHasChildren):
        service.delete_building(INITIAL_BUILDINGS[0].id)
    with pytest.raises(HousingCatalogHasChildren):
        service.delete_floor(FLOOR_SEEDS[0].id)

    building, floor = _create_building_and_floor(
        service,
        actor_id=actor_id,
        code="DELETE",
    )
    room = service.create_room(
        RoomCreate(
            floor_id=floor.id,
            room_number="DELETE-201",
            room_type="single",
            capacity=1,
        ),
        actor_user_id=actor_id,
    )

    service.delete_room(room.id)
    service.delete_floor(floor.id)
    service.delete_building(building.id)

    assert db.get(HousingRoom, room.id) is None
    assert db.get(HousingFloor, floor.id) is None
    assert db.get(HousingBuilding, building.id) is None


def test_delete_room_with_historical_stays_raises_typed_catalog_conflict(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    _, floor = _create_building_and_floor(service, actor_id=actor_id, code="HISTORY")
    room = service.create_room(
        RoomCreate(
            floor_id=floor.id,
            room_number="HISTORY-201",
            room_type="single",
            capacity=1,
        ),
        actor_user_id=actor_id,
    )
    resident_id = uuid4()
    db.add(
        Person(
            id=resident_id,
            full_name="已取消历史住户",
            identity_ciphertext=f"history-cipher-{resident_id.hex}",
            identity_digest=f"history-digest-{resident_id.hex}",
            sex="female",
            date_of_birth=date(2000, 1, 2),
        )
    )
    db.flush()
    now = datetime.now(UTC)
    historical_stay = HousingStay(
        id=uuid4(),
        person_id=resident_id,
        room_id=room.id,
        stay_purpose="permanent",
        duration_mode="fixed_term",
        planned_check_in_at=now - timedelta(days=3),
        planned_check_out_at=now - timedelta(days=2),
        status="cancelled",
        room_name_snapshot=room.room_number,
        floor_name_snapshot="2 楼",
        building_name_snapshot="HISTORY 测试楼",
        created_by_user_id=actor_id,
        cancelled_by_user_id=actor_id,
        cancellation_reason="历史取消",
        version=1,
    )
    db.add(historical_stay)
    db.flush()
    db.commit()

    with pytest.raises(HousingCatalogHasChildren):
        service.delete_room(room.id)

    assert db.get(HousingRoom, room.id) is not None
    assert db.get(HousingStay, historical_stay.id) is not None


def test_room_mutations_guard_current_and_future_stays_but_keep_snapshots(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    building, floor = _create_building_and_floor(service, actor_id=actor_id, code="GUARD")
    target_floor = service.create_floor(
        FloorCreate(building_id=building.id, number=3, name="三楼"),
        actor_user_id=actor_id,
    )
    room = service.create_room(
        RoomCreate(floor_id=floor.id, room_number="GUARD-201", room_type="double", capacity=2),
        actor_user_id=actor_id,
    )
    room = service.verify_room(room.id, expected_version=room.version, actor_user_id=actor_id)
    now = datetime.now(UTC)
    residents = []
    for index in range(2):
        person_id = uuid4()
        residents.append(person_id)
        db.add(
            Person(
                id=person_id,
                full_name=f"结构保护住户{index}",
                identity_ciphertext=f"guard-cipher-{person_id.hex}",
                identity_digest=f"guard-digest-{person_id.hex}",
                sex="male",
                date_of_birth=date(2000, 1, 2),
            )
        )
    db.flush()
    for index, person_id in enumerate(residents):
        planned_check_in_at = (
            now - timedelta(days=2) if index == 0 else now + timedelta(days=1)
        )
        checked_in = index == 0
        db.add(
            HousingStay(
                id=uuid4(),
                person_id=person_id,
                room_id=room.id,
                stay_purpose="permanent",
                duration_mode="fixed_term",
                planned_check_in_at=planned_check_in_at,
                planned_check_out_at=now + timedelta(days=4),
                actual_check_in_at=planned_check_in_at if checked_in else None,
                status="checked_in" if checked_in else "planned",
                room_name_snapshot="GUARD-201",
                floor_name_snapshot="二楼",
                building_name_snapshot="GUARD 测试楼",
                created_by_user_id=actor_id,
                checked_in_by_user_id=actor_id if checked_in else None,
                version=1,
            )
        )
    db.flush()
    snapshots = tuple(
        db.scalars(
            select(HousingStay.room_name_snapshot).where(HousingStay.room_id == room.id)
        )
    )
    db.commit()

    for update in (
        RoomUpdate(version=room.version, room_type="custom", capacity=1),
        RoomUpdate(version=room.version, room_type="functional", capacity=0),
        RoomUpdate(version=room.version, floor_id=target_floor.id),
        RoomUpdate(version=room.version, service_status="maintenance"),
        RoomUpdate(version=room.version, service_status="disabled"),
    ):
        with pytest.raises(HousingRoomHasActiveStays):
            service.update_room(room.id, update, actor_user_id=actor_id)
        db.expire_all()
        room = db.get(HousingRoom, room.id)
        assert room is not None
    with pytest.raises(HousingRoomHasActiveStays):
        service.delete_room(room.id)

    renamed = service.update_room(
        room.id,
        RoomUpdate(version=room.version, room_number="GUARD-202"),
        actor_user_id=actor_id,
    )
    annotated = service.update_room(
        renamed.id,
        RoomUpdate(version=renamed.version, notes="安全备注"),
        actor_user_id=actor_id,
    )
    assert annotated.notes == "安全备注"
    assert tuple(
        db.scalars(
            select(HousingStay.room_name_snapshot).where(HousingStay.room_id == room.id)
        )
    ) == snapshots


def test_legacy_functional_room_with_active_stays_allows_safe_updates(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    _, floor = _create_building_and_floor(service, actor_id=actor_id, code="LEGACYFUNC")
    room = service.create_room(
        RoomCreate(
            floor_id=floor.id,
            room_number="LEGACYFUNC-201",
            room_type="functional",
            capacity=0,
        ),
        actor_user_id=actor_id,
    )
    room = service.verify_room(room.id, expected_version=room.version, actor_user_id=actor_id)
    now = datetime.now(UTC)
    resident_id = uuid4()
    db.add(
        Person(
            id=resident_id,
            full_name="遗留功能间住户",
            identity_ciphertext=f"legacy-functional-cipher-{resident_id.hex}",
            identity_digest=f"legacy-functional-digest-{resident_id.hex}",
            sex="male",
            date_of_birth=date(2000, 1, 2),
        )
    )
    db.flush()
    db.add(
        HousingStay(
            id=uuid4(),
            person_id=resident_id,
            room_id=room.id,
            stay_purpose="permanent",
            duration_mode="fixed_term",
            planned_check_in_at=now - timedelta(days=1),
            planned_check_out_at=now + timedelta(days=1),
            actual_check_in_at=now - timedelta(days=1),
            status="checked_in",
            room_name_snapshot=room.room_number,
            floor_name_snapshot="二楼",
            building_name_snapshot="LEGACYFUNC 测试楼",
            created_by_user_id=actor_id,
            checked_in_by_user_id=actor_id,
            version=1,
        )
    )
    db.flush()
    snapshot = db.scalar(
        select(HousingStay.room_name_snapshot).where(HousingStay.room_id == room.id)
    )
    assert snapshot == "LEGACYFUNC-201"

    annotated = service.update_room(
        room.id,
        RoomUpdate(version=room.version, notes="遗留数据备注"),
        actor_user_id=actor_id,
    )
    renamed = service.update_room(
        annotated.id,
        RoomUpdate(version=annotated.version, room_number="LEGACYFUNC-202"),
        actor_user_id=actor_id,
    )

    assert renamed.notes == "遗留数据备注"
    assert renamed.room_number == "LEGACYFUNC-202"
    assert db.scalar(
        select(HousingStay.room_name_snapshot).where(HousingStay.room_id == room.id)
    ) == snapshot


def test_capacity_guard_uses_half_open_peak_intervals(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    _, floor = _create_building_and_floor(service, actor_id=actor_id, code="PEAK")
    room = service.create_room(
        RoomCreate(floor_id=floor.id, room_number="PEAK-201", room_type="double", capacity=2),
        actor_user_id=actor_id,
    )
    room = service.verify_room(room.id, expected_version=room.version, actor_user_id=actor_id)
    start = datetime.now(UTC) + timedelta(days=1)
    for index, (check_in, check_out) in enumerate(
        ((start, start + timedelta(days=1)), (start + timedelta(days=1), start + timedelta(days=2)))
    ):
        person_id = uuid4()
        db.add(
            Person(
                id=person_id,
                full_name=f"半开区间住户{index}",
                identity_ciphertext=f"peak-cipher-{person_id.hex}",
                identity_digest=f"peak-digest-{person_id.hex}",
                sex="female",
                date_of_birth=date(2000, 1, 2),
            )
        )
    db.flush()
    for index, (check_in, check_out) in enumerate(
        ((start, start + timedelta(days=1)), (start + timedelta(days=1), start + timedelta(days=2)))
    ):
        person_id = db.scalar(
            select(Person.id)
            .where(Person.full_name == f"半开区间住户{index}")
        )
        assert person_id is not None
        db.add(
            HousingStay(
                id=uuid4(),
                person_id=person_id,
                room_id=room.id,
                stay_purpose="permanent",
                duration_mode="fixed_term",
                planned_check_in_at=check_in,
                planned_check_out_at=check_out,
                status="planned",
                room_name_snapshot="PEAK-201",
                floor_name_snapshot="二楼",
                building_name_snapshot="PEAK 测试楼",
                created_by_user_id=actor_id,
                version=1,
            )
        )
    db.flush()

    changed = service.update_room(
        room.id,
        RoomUpdate(version=room.version, room_type="custom", capacity=1),
        actor_user_id=actor_id,
    )
    assert changed.capacity == 1


def test_capacity_guard_counts_current_stays_after_planned_interval_ends(
    db: Session,
    housing_actor_id: UUID,
) -> None:
    actor_id = housing_actor_id
    service = HousingInventoryService(db)
    _, floor = _create_building_and_floor(service, actor_id=actor_id, code="LIVEPEAK")
    room = service.create_room(
        RoomCreate(
            floor_id=floor.id,
            room_number="LIVEPEAK-201",
            room_type="double",
            capacity=2,
        ),
        actor_user_id=actor_id,
    )
    room = service.verify_room(room.id, expected_version=room.version, actor_user_id=actor_id)
    now = datetime.now(UTC)
    for index, status in enumerate(("checked_in", "checkout_pending")):
        person_id = uuid4()
        db.add(
            Person(
                id=person_id,
                full_name=f"仍在住住户{index}",
                identity_ciphertext=f"live-peak-cipher-{person_id.hex}",
                identity_digest=f"live-peak-digest-{person_id.hex}",
                sex="female",
                date_of_birth=date(2000, 1, 2),
            )
        )
        db.flush()
        planned_check_in_at = now - timedelta(days=4 - index * 2)
        stay = HousingStay(
            id=uuid4(),
            person_id=person_id,
            room_id=room.id,
            stay_purpose="permanent",
            duration_mode="fixed_term",
            planned_check_in_at=planned_check_in_at,
            planned_check_out_at=planned_check_in_at + timedelta(days=1),
            actual_check_in_at=planned_check_in_at,
            status=status,
            room_name_snapshot=room.room_number,
            floor_name_snapshot="2 楼",
            building_name_snapshot="LIVEPEAK 测试楼",
            created_by_user_id=actor_id,
            checked_in_by_user_id=actor_id,
            version=1,
        )
        if status == "checkout_pending":
            stay.checkout_requested_actual_at = now - timedelta(days=1)
            stay.checkout_requested_at = now - timedelta(days=1)
            stay.checkout_requested_by_user_id = actor_id
        db.add(stay)
    db.flush()

    with pytest.raises(HousingRoomHasActiveStays):
        service.update_room(
            room.id,
            RoomUpdate(version=room.version, room_type="single", capacity=1),
            actor_user_id=actor_id,
        )
