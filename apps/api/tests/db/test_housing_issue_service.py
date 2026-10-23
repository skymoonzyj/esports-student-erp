from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, delete, event, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.housing.errors import (
    HousingCatalogHasChildren,
    HousingIssueWorkflowRequired,
    HousingOpenIssueExists,
    HousingRoomUnavailable,
    HousingServiceError,
    HousingStaleVersion,
)
from app.housing.inventory_service import HousingInventoryService, HousingOverviewFilters
from app.housing.issue_service import HousingIssueService
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingRoomIssue,
    HousingStay,
)
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.schemas import RoomIssueCreate, RoomIssueResolve, RoomUpdate, StayCreate
from app.identity.models import Person, StaffProfile
from tests.account_identifiers import add_test_primary_login_identifier

NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)


class _OverviewAccess:
    def can_manage_room_care(self, room: HousingRoom) -> bool:
        return False

    access_revision = "0" * 64

    def __init__(self, room_id: UUID) -> None:
        self.room_id = room_id

    def can_manage_room(self, room: HousingRoom) -> bool:
        del room
        return False

    def filter_rooms(self, rooms: list[HousingRoom]) -> list[HousingRoom]:
        return [room for room in rooms if room.id == self.room_id]


@dataclass(frozen=True)
class _ReportResult:
    outcome: str
    session_reusable: bool


def _issue_service(db: Session) -> HousingIssueService:
    return HousingIssueService(db)


def _staff_account(
    db: Session,
    *,
    name: str,
    sex: str = "female",
) -> tuple[UUID, UUID]:
    person_id = uuid4()
    account_id = uuid4()
    db.add(
        Person(
            id=person_id,
            full_name=name,
            identity_ciphertext=f"issue-identity-cipher-{person_id.hex}",
            identity_digest=f"issue-identity-digest-{person_id.hex}",
            phone_ciphertext=f"issue-phone-cipher-{person_id.hex}",
            phone_digest=f"issue-phone-digest-{person_id.hex}",
            email=None,
            email_ciphertext=f"issue-email-cipher-{person_id.hex}",
            email_digest=f"issue-email-digest-{person_id.hex}",
            sex=sex,
            date_of_birth=date(1990, 1, 1),
        )
    )
    db.flush()
    db.add(StaffProfile(person_id=person_id, staff_type="life_teacher"))
    account = UserAccount(
        id=account_id,
        person_id=person_id,
        login_phone_digest=f"issue-login-{account_id.hex}",
        email_normalized=f"{account_id.hex}@example.test",
        password_hash="issue-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    add_test_primary_login_identifier(db, account)
    return person_id, account_id


def _room(
    db: Session,
    *,
    service_status: str = "available",
    capacity: int = 3,
) -> HousingRoom:
    suffix = uuid4().hex[:10].upper()
    building = HousingBuilding(
        id=uuid4(),
        code=f"I{suffix[:8]}",
        name=f"问题房测试楼{suffix}",
        display_order=0,
        is_active=True,
    )
    floor = HousingFloor(
        id=uuid4(),
        building_id=building.id,
        number=2,
        name=f"问题房二楼{suffix}",
        display_order=0,
        is_active=True,
    )
    room = HousingRoom(
        id=uuid4(),
        building_id=building.id,
        floor_id=floor.id,
        room_number=f"{suffix}-201",
        normalized_key=f"{suffix}-201",
        room_type="custom",
        capacity=capacity,
        verification_status="verified",
        service_status=service_status,
        version=1,
    )
    db.add_all([building, floor, room])
    db.flush()
    return room


def _current_stay(
    db: Session,
    *,
    room: HousingRoom,
    person_id: UUID,
    actor_id: UUID,
    status: str,
    offset: int,
) -> HousingStay:
    checked_in_at = NOW - timedelta(days=offset + 1)
    stay = HousingStay(
        id=uuid4(),
        person_id=person_id,
        room_id=room.id,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=checked_in_at,
        actual_check_in_at=checked_in_at,
        status=status,
        room_name_snapshot=room.room_number,
        floor_name_snapshot="问题房二楼",
        building_name_snapshot="问题房测试楼",
        created_by_user_id=actor_id,
        checked_in_by_user_id=actor_id,
        checkout_requested_actual_at=NOW - timedelta(hours=2)
        if status == "checkout_pending"
        else None,
        checkout_requested_at=NOW - timedelta(hours=1)
        if status == "checkout_pending"
        else None,
        checkout_requested_by_user_id=actor_id
        if status == "checkout_pending"
        else None,
        version=1,
    )
    db.add(stay)
    db.flush()
    return stay


def _stay_input(person_id: UUID, starts_at: datetime) -> StayCreate:
    return StayCreate(
        person_id=person_id,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=starts_at,
    )


def _allow(room: HousingRoom) -> None:
    del room


def _stay_state(db: Session, stay_ids: tuple[UUID, ...]) -> list[tuple[object, ...]]:
    return list(
        db.execute(
            select(
                HousingStay.id,
                HousingStay.room_id,
                HousingStay.status,
                HousingStay.actual_check_in_at,
                HousingStay.actual_check_out_at,
                HousingStay.version,
            )
            .where(HousingStay.id.in_(stay_ids))
            .order_by(HousingStay.id)
        ).tuples()
    )


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (RoomIssueCreate, {"room_version": 1, "description": " \t "}),
        (RoomIssueResolve, {"room_version": 1, "resolution_note": " \n "}),
    ],
)
def test_issue_text_fields_require_nonblank_values(
    schema: type[RoomIssueCreate | RoomIssueResolve],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_report_locks_then_reauthorizes_and_stale_version_is_atomic(db: Session) -> None:
    _, actor_id = _staff_account(db, name="过期问题上报人")
    room = _room(db)
    db.commit()
    observed: list[str] = []
    connection = db.connection()

    def capture_room_lock(conn, cursor, statement, parameters, context, executemany):
        del conn, cursor, parameters, context, executemany
        if "FROM housing_rooms" in statement and "FOR UPDATE" in statement:
            observed.append("lock")

    def authorize(locked_room: HousingRoom) -> None:
        assert locked_room.id == room.id
        assert locked_room.service_status == "available"
        assert locked_room.version == 1
        observed.append("authorize")

    event.listen(connection, "before_cursor_execute", capture_room_lock)
    try:
        with pytest.raises(HousingStaleVersion):
            _issue_service(db).report(
                room.id,
                RoomIssueCreate(room_version=2, description="空调故障"),
                actor_user_id=actor_id,
                occurred_at=NOW,
                authorize_room=authorize,
            )
    finally:
        event.remove(connection, "before_cursor_execute", capture_room_lock)

    assert observed == ["lock", "authorize"]
    db.expire_all()
    preserved = db.get(HousingRoom, room.id)
    assert preserved is not None
    assert (preserved.service_status, preserved.version) == ("available", 1)
    assert db.scalar(
        select(func.count(HousingRoomIssue.id)).where(HousingRoomIssue.room_id == room.id)
    ) == 0
    assert db.scalar(select(literal(1))) == 1


def test_report_and_resolve_preserve_residents_block_allocation_and_project_transfers(
    db: Session,
) -> None:
    _, actor_id = _staff_account(db, name="问题房操作人")
    first_person_id, _ = _staff_account(db, name="问题房在住人甲")
    second_person_id, _ = _staff_account(db, name="问题房在住人乙")
    direct_person_id, _ = _staff_account(db, name="问题房直接入住候选人")
    future_person_id, _ = _staff_account(db, name="问题房预约候选人")
    room = _room(db)
    first = _current_stay(
        db,
        room=room,
        person_id=first_person_id,
        actor_id=actor_id,
        status="checked_in",
        offset=1,
    )
    second = _current_stay(
        db,
        room=room,
        person_id=second_person_id,
        actor_id=actor_id,
        status="checkout_pending",
        offset=2,
    )
    stay_ids = (first.id, second.id)
    db.commit()
    before_stays = _stay_state(db, stay_ids)
    audit_count = db.scalar(select(func.count(AuditEvent.id)))
    authorized: list[tuple[str, int]] = []
    service = _issue_service(db)

    issue = service.report(
        room.id,
        RoomIssueCreate(room_version=1, description=" 门锁损坏 "),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=lambda locked_room: authorized.append(
            (locked_room.service_status, locked_room.version)
        ),
    )

    assert issue.room_id == room.id
    assert issue.description == "门锁损坏"
    assert issue.previous_service_status == "available"
    assert issue.reported_at == NOW
    assert issue.reported_by_user_id == actor_id
    assert issue.status == "open"
    assert issue.resolved_at is None
    assert issue.resolved_by_user_id is None
    assert issue.resolution_note is None
    db.expire_all()
    problem_room = db.get(HousingRoom, room.id)
    assert problem_room is not None
    assert (problem_room.service_status, problem_room.version) == ("problem", 2)
    assert _stay_state(db, stay_ids) == before_stays

    occupancy = HousingOccupancyService(db)
    with pytest.raises(HousingRoomUnavailable):
        occupancy.create_stay(
            room.id,
            _stay_input(direct_person_id, NOW - timedelta(minutes=1)),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )
    with pytest.raises(HousingRoomUnavailable):
        occupancy.create_stay(
            room.id,
            _stay_input(future_person_id, NOW + timedelta(days=1)),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )
    assert db.scalar(
        select(func.count(HousingStay.id)).where(
            HousingStay.person_id.in_((direct_person_id, future_person_id))
        )
    ) == 0

    overview = HousingInventoryService(db).overview(
        _OverviewAccess(room.id),
        HousingOverviewFilters(building_id=problem_room.building_id),
        page=1,
        page_size=20,
        as_of=NOW,
    )
    assert len(overview.rooms) == 1
    problem_card = overview.rooms[0]
    assert problem_card.current_occupants == 2
    assert problem_card.pending_transfer_count == problem_card.current_occupants
    assert problem_card.open_issue_summary is not None
    assert problem_card.open_issue_summary.id == issue.id

    resolved = service.resolve(
        issue.id,
        RoomIssueResolve(room_version=2, resolution_note=" 已完成维修 "),
        actor_user_id=actor_id,
        occurred_at=NOW + timedelta(hours=2),
        authorize_room=lambda locked_room: authorized.append(
            (locked_room.service_status, locked_room.version)
        ),
    )

    assert resolved.id == issue.id
    assert resolved.status == "resolved"
    assert resolved.resolved_at == NOW + timedelta(hours=2)
    assert resolved.resolved_by_user_id == actor_id
    assert resolved.resolution_note == "已完成维修"
    assert authorized == [("available", 1), ("problem", 2)]
    db.expire_all()
    restored_room = db.get(HousingRoom, room.id)
    assert restored_room is not None
    assert (restored_room.service_status, restored_room.version) == ("available", 3)
    assert _stay_state(db, stay_ids) == before_stays
    assert db.scalar(select(func.count(AuditEvent.id))) == audit_count

    restored_overview = HousingInventoryService(db).overview(
        _OverviewAccess(room.id),
        HousingOverviewFilters(building_id=restored_room.building_id),
        page=1,
        page_size=20,
        as_of=NOW + timedelta(hours=3),
    )
    restored_card = restored_overview.rooms[0]
    assert restored_card.current_occupants == 2
    assert restored_card.pending_transfer_count == 0
    assert restored_card.open_issue_summary is None


@pytest.mark.parametrize("service_status", ["available", "maintenance", "disabled"])
def test_open_issue_blocks_inventory_service_status_bypass(
    db: Session,
    service_status: str,
) -> None:
    _, actor_id = _staff_account(db, name=f"问题房状态旁路-{service_status}")
    room = _room(db)
    issue = _issue_service(db).report(
        room.id,
        RoomIssueCreate(room_version=1, description="未解决的房间问题"),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )

    with pytest.raises(HousingIssueWorkflowRequired):
        HousingInventoryService(db).update_room(
            room.id,
            RoomUpdate(version=2, service_status=service_status),
            actor_user_id=actor_id,
            authorize_room=_allow,
        )

    db.expire_all()
    preserved_room = db.get(HousingRoom, room.id)
    preserved_issue = db.get(HousingRoomIssue, issue.id)
    assert preserved_room is not None and preserved_issue is not None
    assert (preserved_room.service_status, preserved_room.version) == ("problem", 2)
    assert preserved_issue.status == "open"
    assert db.scalar(select(literal(1))) == 1


def test_open_issue_still_allows_safe_room_annotation(db: Session) -> None:
    _, actor_id = _staff_account(db, name="问题房安全备注操作人")
    room = _room(db)
    issue = _issue_service(db).report(
        room.id,
        RoomIssueCreate(room_version=1, description="等待处理的问题"),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )

    annotated = HousingInventoryService(db).update_room(
        room.id,
        RoomUpdate(version=2, notes="已安排维修人员"),
        actor_user_id=actor_id,
        authorize_room=_allow,
    )

    assert (annotated.service_status, annotated.version, annotated.notes) == (
        "problem",
        3,
        "已安排维修人员",
    )
    db.expire_all()
    preserved_issue = db.get(HousingRoomIssue, issue.id)
    assert preserved_issue is not None
    assert preserved_issue.status == "open"


@pytest.mark.parametrize("issue_status", ["open", "resolved"])
def test_delete_room_with_issue_history_raises_typed_catalog_conflict_and_rolls_back(
    db: Session,
    issue_status: str,
) -> None:
    _, actor_id = _staff_account(db, name=f"删除问题历史-{issue_status}")
    room = _room(db)
    room.notes = "应保留的安全备注"
    issue_service = _issue_service(db)
    issue = issue_service.report(
        room.id,
        RoomIssueCreate(room_version=1, description="需要保留的问题历史"),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )
    if issue_status == "resolved":
        issue = issue_service.resolve(
            issue.id,
            RoomIssueResolve(room_version=2, resolution_note="问题已处理"),
            actor_user_id=actor_id,
            occurred_at=NOW + timedelta(hours=1),
            authorize_room=_allow,
        )
    room_id = room.id
    issue_id = issue.id
    db.commit()

    persisted_room = db.get(HousingRoom, room_id)
    persisted_issue = db.get(HousingRoomIssue, issue_id)
    assert persisted_room is not None and persisted_issue is not None
    room_state = (
        persisted_room.service_status,
        persisted_room.version,
        persisted_room.notes,
    )
    issue_state = (
        persisted_issue.status,
        persisted_issue.resolved_at,
        persisted_issue.resolved_by_user_id,
        persisted_issue.resolution_note,
    )
    db.commit()

    with pytest.raises(HousingCatalogHasChildren):
        HousingInventoryService(db).delete_room(room_id, authorize_room=_allow)

    db.expire_all()
    preserved_room = db.get(HousingRoom, room_id)
    preserved_issue = db.get(HousingRoomIssue, issue_id)
    assert preserved_room is not None and preserved_issue is not None
    assert (
        preserved_room.service_status,
        preserved_room.version,
        preserved_room.notes,
    ) == room_state
    assert (
        preserved_issue.status,
        preserved_issue.resolved_at,
        preserved_issue.resolved_by_user_id,
        preserved_issue.resolution_note,
    ) == issue_state
    assert db.scalar(select(literal(1))) == 1


def test_one_open_issue_per_room_and_unique_index_backstop_are_typed(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, actor_id = _staff_account(db, name="重复问题上报人")
    room = _room(db)
    service = _issue_service(db)
    first = service.report(
        room.id,
        RoomIssueCreate(room_version=1, description="首次问题"),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )

    with pytest.raises(HousingOpenIssueExists):
        service.report(
            room.id,
            RoomIssueCreate(room_version=2, description="重复问题"),
            actor_user_id=actor_id,
            occurred_at=NOW + timedelta(minutes=1),
            authorize_room=_allow,
        )

    monkeypatch.setattr(service, "_locked_open_issue", lambda room_id: None)
    with pytest.raises(HousingOpenIssueExists):
        service.report(
            room.id,
            RoomIssueCreate(room_version=2, description="并发回退问题"),
            actor_user_id=actor_id,
            occurred_at=NOW + timedelta(minutes=2),
            authorize_room=_allow,
        )

    db.expire_all()
    preserved_room = db.get(HousingRoom, room.id)
    assert preserved_room is not None
    assert (preserved_room.service_status, preserved_room.version) == ("problem", 2)
    issues = list(
        db.scalars(
            select(HousingRoomIssue)
            .where(HousingRoomIssue.room_id == room.id)
            .order_by(HousingRoomIssue.reported_at)
        )
    )
    assert [issue.id for issue in issues] == [first.id]
    assert db.scalar(select(literal(1))) == 1


def test_resolve_restores_exact_previous_status_and_rejects_invalid_times(db: Session) -> None:
    _, actor_id = _staff_account(db, name="问题恢复操作人")
    room = _room(db, service_status="maintenance")
    service = _issue_service(db)

    with pytest.raises(HousingServiceError):
        service.report(
            room.id,
            RoomIssueCreate(room_version=1, description="无时区问题"),
            actor_user_id=actor_id,
            occurred_at=NOW.replace(tzinfo=None),
            authorize_room=_allow,
        )
    issue = service.report(
        room.id,
        RoomIssueCreate(room_version=1, description="维修状态下发现问题"),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )

    for occurred_at in (NOW - timedelta(seconds=1), NOW.replace(tzinfo=None)):
        with pytest.raises(HousingServiceError):
            service.resolve(
                issue.id,
                RoomIssueResolve(room_version=2, resolution_note="不合法解决时间"),
                actor_user_id=actor_id,
                occurred_at=occurred_at,
                authorize_room=_allow,
            )
    with pytest.raises(HousingStaleVersion):
        service.resolve(
            issue.id,
            RoomIssueResolve(room_version=999, resolution_note="过期解决请求"),
            actor_user_id=actor_id,
            occurred_at=NOW + timedelta(minutes=1),
            authorize_room=_allow,
        )

    db.expire_all()
    open_issue = db.get(HousingRoomIssue, issue.id)
    problem_room = db.get(HousingRoom, room.id)
    assert open_issue is not None and problem_room is not None
    assert open_issue.status == "open"
    assert (problem_room.service_status, problem_room.version) == ("problem", 2)

    service.resolve(
        issue.id,
        RoomIssueResolve(room_version=2, resolution_note="恢复原状态"),
        actor_user_id=actor_id,
        occurred_at=NOW + timedelta(minutes=2),
        authorize_room=_allow,
    )
    db.expire_all()
    restored = db.get(HousingRoom, room.id)
    assert restored is not None
    assert (restored.service_status, restored.version) == ("maintenance", 3)


def test_unrelated_integrity_error_is_reraised_and_fully_rolled_back(db: Session) -> None:
    room = _room(db)
    db.commit()

    with pytest.raises(IntegrityError) as error:
        _issue_service(db).report(
            room.id,
            RoomIssueCreate(room_version=1, description="无效上报人"),
            actor_user_id=uuid4(),
            occurred_at=NOW,
            authorize_room=_allow,
        )

    assert getattr(error.value.orig.diag, "constraint_name", None) != (
        "uq_housing_room_issues_open_room"
    )
    db.expire_all()
    preserved = db.get(HousingRoom, room.id)
    assert preserved is not None
    assert (preserved.service_status, preserved.version) == ("available", 1)
    assert db.scalar(
        select(func.count(HousingRoomIssue.id)).where(HousingRoomIssue.room_id == room.id)
    ) == 0
    assert db.scalar(select(literal(1))) == 1


def _report_worker(
    engine: Engine,
    barrier: Barrier,
    *,
    room_id: UUID,
    actor_id: UUID,
) -> _ReportResult:
    with Session(engine, autoflush=False) as worker:
        barrier.wait(timeout=10)
        try:
            _issue_service(worker).report(
                room_id,
                RoomIssueCreate(room_version=1, description="并发发现问题"),
                actor_user_id=actor_id,
                occurred_at=NOW,
                authorize_room=_allow,
            )
        except HousingServiceError as exc:
            outcome = exc.code
        else:
            outcome = "committed"
        return _ReportResult(
            outcome=outcome,
            session_reusable=worker.scalar(select(literal(1))) == 1,
        )


def _cleanup_concurrent_report(
    engine: Engine,
    *,
    room_id: UUID,
    actor_person_id: UUID,
    actor_id: UUID,
) -> None:
    with Session(engine) as cleanup, cleanup.begin():
        room_row = cleanup.execute(
            select(HousingRoom.floor_id, HousingRoom.building_id).where(
                HousingRoom.id == room_id
            )
        ).one_or_none()
        cleanup.execute(
            delete(HousingRoomIssue).where(HousingRoomIssue.room_id == room_id)
        )
        cleanup.execute(delete(HousingRoom).where(HousingRoom.id == room_id))
        if room_row is not None:
            cleanup.execute(delete(HousingFloor).where(HousingFloor.id == room_row.floor_id))
            cleanup.execute(
                delete(HousingBuilding).where(HousingBuilding.id == room_row.building_id)
            )
        cleanup.execute(delete(UserAccount).where(UserAccount.id == actor_id))
        cleanup.execute(delete(StaffProfile).where(StaffProfile.person_id == actor_person_id))
        cleanup.execute(delete(Person).where(Person.id == actor_person_id))


def test_concurrent_reports_have_one_typed_loser_and_reusable_session(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    with Session(postgres_engine) as setup, setup.begin():
        actor_person_id, actor_id = _staff_account(setup, name="并发问题上报人")
        room = _room(setup)
        room_id = room.id

    request.addfinalizer(
        partial(
            _cleanup_concurrent_report,
            postgres_engine,
            room_id=room_id,
            actor_person_id=actor_person_id,
            actor_id=actor_id,
        )
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: _report_worker(
                    postgres_engine,
                    barrier,
                    room_id=room_id,
                    actor_id=actor_id,
                ),
                range(2),
            )
        )

    assert [result.outcome for result in results].count("committed") == 1
    assert [result.outcome for result in results].count(
        HousingOpenIssueExists.code
    ) == 1
    assert all(result.session_reusable for result in results)
    with Session(postgres_engine) as verification:
        assert verification.scalar(
            select(func.count(HousingRoomIssue.id)).where(
                HousingRoomIssue.room_id == room_id,
                HousingRoomIssue.status == "open",
            )
        ) == 1
        persisted_room = verification.get(HousingRoom, room_id)
        assert persisted_room is not None
        assert (persisted_room.service_status, persisted_room.version) == ("problem", 2)
