from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.housing.inventory_service import HousingInventoryService, HousingOverviewFilters
from app.housing.models import HousingRoom, HousingRoomIssue, HousingStay
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.schemas import BuildingCreate, FloorCreate, RoomCreate
from app.identity.models import Person, StaffProfile, StudentProfile
from app.teaching.models import Cohort


class _OverviewAccess:
    def can_manage_room_care(self, room: HousingRoom) -> bool:
        return False

    access_revision = "0" * 64

    def __init__(self, visible_room_ids: frozenset[UUID] | None = None) -> None:
        self._visible_room_ids = visible_room_ids

    def can_manage_room(self, room: HousingRoom) -> bool:
        del room
        return False

    def filter_rooms(self, rooms: list[HousingRoom]) -> list[HousingRoom]:
        if self._visible_room_ids is None:
            return rooms
        return [room for room in rooms if room.id in self._visible_room_ids]


def _actor(db: Session) -> UUID:
    person_id = uuid4()
    account_id = uuid4()
    db.add(
        Person(
            id=person_id,
            full_name="入住投影操作人",
            identity_ciphertext="occupancy-actor-ciphertext",
            identity_digest=f"occupancy-actor-{person_id.hex}",
            sex="female",
            date_of_birth=datetime(1990, 1, 2, tzinfo=UTC).date(),
        )
    )
    db.flush()
    db.add(
        UserAccount(
            id=account_id,
            person_id=person_id,
            login_phone_digest=f"occupancy-phone-{account_id.hex}",
            email_normalized=f"occupancy-{account_id.hex}@example.test",
            password_hash="occupancy-password-hash",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
    )
    db.flush()
    return account_id


def _person(db: Session, *, name: str, sex: str, person_type: str) -> UUID:
    person_id = uuid4()
    db.add(
        Person(
            id=person_id,
            full_name=name,
            identity_ciphertext=f"ciphertext-{person_id.hex}",
            identity_digest=f"digest-{person_id.hex}",
            sex=sex,
            date_of_birth=datetime(2000, 1, 2, tzinfo=UTC).date(),
        )
    )
    if person_type == "student":
        db.add(
            StudentProfile(
                person_id=person_id,
                student_number=f"S-{person_id.hex[:12]}",
                status="active",
                game_account_limit=3,
            )
        )
    elif person_type == "coach":
        db.add(StaffProfile(person_id=person_id, staff_type="coach"))
    db.flush()
    return person_id


def _room(db: Session, *, actor_id: UUID, number: str) -> HousingRoom:
    service = HousingInventoryService(db)
    building = service.create_building(
        BuildingCreate(code=f"P{number}", name=f"投影楼{number}"),
        actor_user_id=actor_id,
    )
    floor = service.create_floor(
        FloorCreate(building_id=building.id, number=2, name="二楼"),
        actor_user_id=actor_id,
    )
    room = service.create_room(
        RoomCreate(
            floor_id=floor.id,
            room_number=f"{number}-201",
            room_type="double",
            capacity=2,
        ),
        actor_user_id=actor_id,
    )
    return service.verify_room(
        room.id,
        expected_version=room.version,
        actor_user_id=actor_id,
    )


def _stay(
    db: Session,
    *,
    room: HousingRoom,
    person_id: UUID,
    actor_id: UUID,
    status: str,
    planned_in: datetime,
    planned_out: datetime,
    cohort_id: UUID | None = None,
) -> HousingStay:
    current = status in {"checked_in", "checkout_pending", "checked_out"}
    checked_out = status == "checked_out"
    cancelled = status == "cancelled"
    stay = HousingStay(
        id=uuid4(),
        person_id=person_id,
        room_id=room.id,
        stay_purpose="camp" if cohort_id is not None else "permanent",
        duration_mode="fixed_term",
        cohort_id=cohort_id,
        planned_check_in_at=planned_in,
        planned_check_out_at=planned_out,
        actual_check_in_at=planned_in if current else None,
        actual_check_out_at=planned_out if checked_out else None,
        status=status,
        room_name_snapshot=room.room_number,
        floor_name_snapshot="二楼",
        building_name_snapshot="投影楼",
        cancellation_reason="已取消" if cancelled else None,
        created_by_user_id=actor_id,
        checked_in_by_user_id=actor_id if current else None,
        checked_out_by_user_id=actor_id if checked_out else None,
        cancelled_by_user_id=actor_id if cancelled else None,
        checkout_requested_actual_at=planned_out - timedelta(hours=1)
        if status == "checkout_pending"
        else None,
        checkout_requested_at=planned_out - timedelta(hours=1)
        if status == "checkout_pending"
        else None,
        checkout_requested_by_user_id=actor_id if status == "checkout_pending" else None,
        version=1,
    )
    db.add(stay)
    db.flush()
    return stay


def test_room_projections_drive_current_future_issue_and_filters(db: Session) -> None:
    actor_id = _actor(db)
    as_of = datetime(2026, 8, 4, 12, tzinfo=UTC)
    occupied = _room(db, actor_id=actor_id, number="A")
    empty = _room(db, actor_id=actor_id, number="B")
    first = _person(db, name="当前男生甲", sex="male", person_type="student")
    second = _person(db, name="当前女生乙", sex="female", person_type="student")
    future = _person(db, name="未来教练", sex="female", person_type="coach")
    historical = _person(db, name="历史人员", sex="female", person_type="other")
    cohort = Cohort(
        name="入住投影营",
        starts_at=as_of - timedelta(days=1),
        ends_at=as_of + timedelta(days=10),
        default_check_in_at=as_of - timedelta(days=1),
        default_check_out_at=as_of + timedelta(days=10),
        status="active",
        created_by_user_id=actor_id,
    )
    db.add(cohort)
    db.flush()
    _stay(
        db,
        room=occupied,
        person_id=first,
        actor_id=actor_id,
        status="checked_in",
        planned_in=as_of - timedelta(days=2),
        planned_out=as_of + timedelta(days=2),
    )
    _stay(
        db,
        room=occupied,
        person_id=second,
        actor_id=actor_id,
        status="checkout_pending",
        planned_in=as_of - timedelta(days=1),
        planned_out=as_of + timedelta(days=1),
    )
    _stay(
        db,
        room=occupied,
        person_id=future,
        actor_id=actor_id,
        status="planned",
        planned_in=as_of + timedelta(days=3),
        planned_out=as_of + timedelta(days=5),
        cohort_id=cohort.id,
    )
    _stay(
        db,
        room=occupied,
        person_id=historical,
        actor_id=actor_id,
        status="checked_out",
        planned_in=as_of - timedelta(days=5),
        planned_out=as_of - timedelta(days=3),
    )
    _stay(
        db,
        room=occupied,
        person_id=_person(db, name="取消人员", sex="female", person_type="other"),
        actor_id=actor_id,
        status="cancelled",
        planned_in=as_of + timedelta(days=6),
        planned_out=as_of + timedelta(days=8),
    )
    db.add(
        HousingRoomIssue(
            room_id=occupied.id,
            description="门锁损坏",
            previous_service_status="available",
            reported_at=as_of,
            reported_by_user_id=actor_id,
            status="open",
        )
    )
    occupied.service_status = "problem"
    db.flush()

    projections = HousingOccupancyService(db).room_projections(
        [occupied.id, empty.id], as_of
    )
    projection = projections[occupied.id]
    assert len(projection.current_stays) == 2
    assert [stay.sex for stay in projection.current_stays] == ["male", "female"]
    assert [stay.person_name for stay in projection.current_stays] == [
        "当前男生甲",
        "当前女生乙",
    ]
    assert projection.current_gender == projection.current_stays[0].sex == "male"
    assert projection.future_reservation_count == 1
    assert [stay.person_name for stay in projection.future_stays] == ["未来教练"]
    assert projection.open_issue is not None
    assert projections[empty.id].current_stays == ()
    assert projections[empty.id].current_gender is None

    filter_access = _OverviewAccess(frozenset({occupied.id, empty.id}))
    occupied_filtered = HousingInventoryService(db).overview(
        filter_access,
        HousingOverviewFilters(occupancy="occupied"),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    unoccupied_filtered = HousingInventoryService(db).overview(
        filter_access,
        HousingOverviewFilters(occupancy="unoccupied"),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    assignable_filtered = HousingInventoryService(db).overview(
        filter_access,
        HousingOverviewFilters(assignability="assignable"),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    unassignable_filtered = HousingInventoryService(db).overview(
        filter_access,
        HousingOverviewFilters(assignability="unassignable"),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    gender_filter_access = _OverviewAccess(frozenset({occupied.id, empty.id}))
    male_filtered = HousingInventoryService(db).overview(
        gender_filter_access,
        HousingOverviewFilters(current_gender="male"),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    unspecified_filtered = HousingInventoryService(db).overview(
        gender_filter_access,
        HousingOverviewFilters(current_gender="unspecified"),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    assert [item.id for item in occupied_filtered.rooms] == [occupied.id]
    assert empty.id in {item.id for item in unoccupied_filtered.rooms}
    assert occupied.id not in {item.id for item in unoccupied_filtered.rooms}
    assert empty.id in {item.id for item in assignable_filtered.rooms}
    assert occupied.id not in {item.id for item in assignable_filtered.rooms}
    assert occupied.id in {item.id for item in unassignable_filtered.rooms}
    assert empty.id not in {item.id for item in unassignable_filtered.rooms}
    assert [item.id for item in male_filtered.rooms] == [occupied.id]
    assert [item.id for item in unspecified_filtered.rooms] == [empty.id]

    overview = HousingInventoryService(db).overview(
        _OverviewAccess(),
        HousingOverviewFilters(building_id=occupied.building_id),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    card = next(card for card in overview.rooms if card.id == occupied.id)
    assert card.current_occupants == 2
    assert card.remaining_capacity == 0
    assert card.current_gender == "male"
    assert card.occupancy_status == "occupied"
    assert card.future_reservation_count == 1
    assert card.has_future_reservation is True
    assert card.open_issue_summary is not None
    assert card.is_assignable is False
    assert overview.summary.occupied_rooms == 1
    assert overview.summary.current_occupants == 2
    assert overview.summary.total_capacity == 0
    assert overview.summary.occupancy_rate == Decimal("0.00")

    occupied.service_status = "available"
    occupied.room_type = "custom"
    occupied.capacity = 3
    db.flush()
    partial = HousingInventoryService(db).overview(
        _OverviewAccess(),
        HousingOverviewFilters(building_id=occupied.building_id),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    partial_card = next(card for card in partial.rooms if card.id == occupied.id)
    assert partial_card.occupancy_status == "occupied"
    assert partial_card.is_assignable is True
    partial_occupied = HousingInventoryService(db).overview(
        _OverviewAccess(),
        HousingOverviewFilters(building_id=occupied.building_id, occupancy="occupied"),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    partial_assignable = HousingInventoryService(db).overview(
        _OverviewAccess(),
        HousingOverviewFilters(
            building_id=occupied.building_id, assignability="assignable"
        ),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    assert [item.id for item in partial_occupied.rooms] == [occupied.id]
    assert [item.id for item in partial_assignable.rooms] == [occupied.id]

    cohort_filtered = HousingInventoryService(db).overview(
        _OverviewAccess(),
        HousingOverviewFilters(cohort_id=cohort.id),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    coach_filtered = HousingInventoryService(db).overview(
        _OverviewAccess(),
        HousingOverviewFilters(person_type="coach"),
        page=1,
        page_size=20,
        as_of=as_of,
    )
    assert [card.id for card in cohort_filtered.rooms] == [occupied.id]
    assert [card.id for card in coach_filtered.rooms] == [occupied.id]
