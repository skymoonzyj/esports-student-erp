from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from queue import Queue
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, event, func, literal, select, text
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.housing.access import HousingAccessService
from app.housing.errors import (
    HousingCapacityConflict,
    HousingCheckoutConfirmationRequired,
    HousingGenderConflict,
    HousingInvalidStayTransition,
    HousingResourceNotFound,
    HousingRoomUnavailable,
    HousingServiceError,
    HousingStaleVersion,
)
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingStay,
    HousingTransfer,
)
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.schemas import (
    CheckoutConfirmation,
    CheckoutRequest,
    TransferStayRequest,
)
from app.identity.models import Person, StaffProfile
from app.rbac.models import Permission, UserPermissionOverride
from app.teaching.models import Cohort

NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)


def _account_person(
    db: Session,
    *,
    name: str,
    sex: str = "male",
) -> tuple[Person, UUID]:
    person = Person(
        id=uuid4(),
        full_name=name,
        identity_ciphertext=f"checkout-identity-{uuid4().hex}",
        identity_digest=f"checkout-identity-digest-{uuid4().hex}",
        phone_ciphertext=f"checkout-phone-{uuid4().hex}",
        phone_digest=f"checkout-phone-digest-{uuid4().hex}",
        email=f"{uuid4().hex}@checkout.example.test",
        sex=sex,
        date_of_birth=date(1990, 1, 1),
    )
    account = UserAccount(
        id=uuid4(),
        person_id=person.id,
        login_phone_digest=f"checkout-login-{uuid4().hex}",
        email_normalized=f"{uuid4().hex}@checkout-login.example.test",
        password_hash="checkout-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(person)
    db.flush()
    db.add_all(
        [
            StaffProfile(person_id=person.id, staff_type="life_teacher"),
            account,
        ]
    )
    db.flush()
    return person, account.id


def _room(
    db: Session,
    *,
    capacity: int = 1,
    room_type: str = "custom",
    service_status: str = "available",
    verification_status: str = "verified",
) -> HousingRoom:
    suffix = uuid4().hex[:10].upper()
    building = HousingBuilding(
        id=uuid4(),
        code=f"C{suffix[:8]}",
        name=f"退房测试楼{suffix}",
        display_order=0,
        is_active=True,
    )
    floor = HousingFloor(
        id=uuid4(),
        building_id=building.id,
        number=2,
        name=f"退房二楼{suffix}",
        display_order=0,
        is_active=True,
    )
    room = HousingRoom(
        id=uuid4(),
        building_id=building.id,
        floor_id=floor.id,
        room_number=f"{suffix}-201",
        normalized_key=f"{suffix}-201",
        room_type=room_type,
        capacity=capacity,
        verification_status=verification_status,
        service_status=service_status,
        version=1,
    )
    db.add_all([building, floor, room])
    db.flush()
    return room


def _stay(
    db: Session,
    *,
    person_id: UUID,
    room: HousingRoom,
    actor_id: UUID,
    status: str = "checked_in",
    request_fields: bool = False,
    version: int = 1,
    duration_mode: str = "long_term",
    planned_check_out_at: datetime | None = None,
    stay_purpose: str = "permanent",
    cohort_id: UUID | None = None,
    visit_reason: str | None = None,
) -> HousingStay:
    checked_in_at = NOW - timedelta(days=2)
    stay = HousingStay(
        id=uuid4(),
        person_id=person_id,
        room_id=room.id,
        stay_purpose=stay_purpose,
        duration_mode=duration_mode,
        cohort_id=cohort_id,
        visit_reason=visit_reason,
        planned_check_in_at=checked_in_at,
        planned_check_out_at=planned_check_out_at,
        actual_check_in_at=checked_in_at,
        status=status,
        room_name_snapshot=room.room_number,
        floor_name_snapshot="退房二楼",
        building_name_snapshot="退房测试楼",
        checkout_requested_actual_at=(NOW - timedelta(hours=1))
        if request_fields
        else None,
        checkout_requested_at=(NOW - timedelta(minutes=30)) if request_fields else None,
        checkout_requested_by_user_id=actor_id if request_fields else None,
        created_by_user_id=actor_id,
        checked_in_by_user_id=actor_id,
        version=version,
    )
    db.add(stay)
    db.flush()
    return stay


def _allow(room: HousingRoom) -> None:
    del room


def _install_destination_stay_failure(db: Session, *, target_room_id: UUID) -> None:
    marker = uuid4().hex
    function_name = f"fail_housing_destination_{marker}"
    trigger_name = f"fail_housing_destination_{marker}"
    db.execute(
        text(
            f"""
            CREATE FUNCTION pg_temp.{function_name}() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'forced destination failure'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'test_transfer_destination_flush';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    db.execute(
        text(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE INSERT ON housing_stays
            FOR EACH ROW
            WHEN (NEW.room_id = '{target_room_id}'::uuid)
            EXECUTE FUNCTION pg_temp.{function_name}()
            """
        )
    )


def _install_transfer_history_failure(db: Session, *, source_stay_id: UUID) -> None:
    marker = uuid4().hex
    function_name = f"fail_housing_transfer_{marker}"
    trigger_name = f"fail_housing_transfer_{marker}"
    db.execute(
        text(
            f"""
            CREATE FUNCTION pg_temp.{function_name}() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'forced transfer history failure'
                    USING ERRCODE = '40001';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    db.execute(
        text(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE INSERT ON housing_transfers
            FOR EACH ROW
            WHEN (NEW.from_stay_id = '{source_stay_id}'::uuid)
            EXECUTE FUNCTION pg_temp.{function_name}()
            """
        )
    )


@dataclass(frozen=True)
class _TransferWorkerResult:
    outcome: str
    session_reusable: bool
    lock_order: tuple[str, ...]
    ordered_room_lock: bool


def _transfer_worker(
    engine: Engine,
    *,
    source_stay_id: UUID,
    target_room_id: UUID,
    actor_id: UUID,
    backend_pids: Queue[int],
    room_lock_attempted: Event | None = None,
    room_lock_acquired: Event | None = None,
    release_room_lock: Event | None = None,
) -> _TransferWorkerResult:
    lock_order: list[str] = []
    room_lock_statements: list[str] = []
    paused_after_room_lock = False
    with Session(engine, autoflush=False) as worker:
        connection = worker.connection()
        backend_pid = int(worker.scalar(text("SELECT pg_backend_pid()")) or 0)
        assert backend_pid > 0
        backend_pids.put(backend_pid)

        def capture_locks(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            normalized = " ".join(statement.lower().split())
            if " for update" not in normalized:
                return
            if " from people" in normalized:
                lock_order.append("people")
            elif " from housing_rooms" in normalized:
                lock_order.append("rooms")
                room_lock_statements.append(normalized)
                if room_lock_attempted is not None:
                    room_lock_attempted.set()
            elif " from housing_stays" in normalized:
                lock_order.append("stays")

        def pause_with_room_lock_held(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            nonlocal paused_after_room_lock
            normalized = " ".join(statement.lower().split())
            if (
                paused_after_room_lock
                or " from housing_rooms" not in normalized
                or " for update" not in normalized
            ):
                return
            paused_after_room_lock = True
            if room_lock_acquired is not None:
                room_lock_acquired.set()
            if release_room_lock is not None and not release_room_lock.wait(timeout=10):
                raise TimeoutError("transfer room-lock release was not signalled")

        event.listen(connection, "before_cursor_execute", capture_locks)
        event.listen(connection, "after_cursor_execute", pause_with_room_lock_held)
        try:
            try:
                HousingOccupancyService(worker).transfer(
                    source_stay_id,
                    TransferStayRequest(
                        version=1,
                        target_room_id=target_room_id,
                        transferred_at=NOW - timedelta(minutes=30),
                        reason="并发目标房调房",
                    ),
                    actor_user_id=actor_id,
                    occurred_at=NOW,
                    authorize_source=_allow,
                    authorize_target=_allow,
                )
            except HousingServiceError as exc:
                outcome = exc.code
            else:
                outcome = "committed"
            session_reusable = worker.scalar(select(literal(1))) == 1
        finally:
            event.remove(connection, "before_cursor_execute", capture_locks)
            event.remove(connection, "after_cursor_execute", pause_with_room_lock_held)
    return _TransferWorkerResult(
        outcome=outcome,
        session_reusable=session_reusable,
        lock_order=tuple(lock_order),
        ordered_room_lock=any(
            "order by housing_rooms.id" in statement
            for statement in room_lock_statements
        ),
    )


def _wait_for_postgres_blocker(
    engine: Engine,
    *,
    blocked_pid: int,
    blocker_pid: int,
    description: str,
) -> None:
    deadline = monotonic() + 5
    blocking_pids: list[int] = []
    while monotonic() < deadline:
        with engine.connect() as observer:
            blocking_pids = list(
                observer.scalar(
                    text("SELECT pg_blocking_pids(:blocked_pid)"),
                    {"blocked_pid": blocked_pid},
                )
                or []
            )
        if blocker_pid in blocking_pids:
            return
        sleep(0.02)
    pytest.fail(
        f"{description}: backend {blocked_pid} was not blocked by {blocker_pid}; "
        f"observed blockers={blocking_pids}"
    )


def _cleanup_transfer_race(
    engine: Engine,
    *,
    person_ids: tuple[UUID, ...],
    room_ids: tuple[UUID, ...],
    floor_ids: tuple[UUID, ...],
    building_ids: tuple[UUID, ...],
) -> None:
    with Session(engine) as cleanup, cleanup.begin():
        cleanup.execute(
            delete(HousingTransfer).where(HousingTransfer.person_id.in_(person_ids))
        )
        cleanup.execute(delete(HousingStay).where(HousingStay.person_id.in_(person_ids)))
        cleanup.execute(delete(HousingRoom).where(HousingRoom.id.in_(room_ids)))
        cleanup.execute(delete(HousingFloor).where(HousingFloor.id.in_(floor_ids)))
        cleanup.execute(
            delete(HousingBuilding).where(HousingBuilding.id.in_(building_ids))
        )
        cleanup.execute(delete(UserAccount).where(UserAccount.person_id.in_(person_ids)))
        cleanup.execute(delete(StaffProfile).where(StaffProfile.person_id.in_(person_ids)))
        cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))


def test_checkout_request_keeps_capacity_until_confirmation_releases_it(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="退房操作人")
    resident, _ = _account_person(db, name="待退房住户")
    room = _room(db)
    stay = _stay(db, person_id=resident.id, room=room, actor_id=actor_id)
    service = HousingOccupancyService(db)
    proposed_at = NOW - timedelta(hours=1)

    requested = service.request_checkout(
        stay.id,
        CheckoutRequest(version=stay.version, actual_check_out_at=proposed_at),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )

    assert requested.status == "checkout_pending"
    assert requested.checkout_requested_actual_at == proposed_at
    assert requested.checkout_requested_at == NOW
    assert requested.checkout_requested_by_user_id == actor_id
    assert requested.actual_check_out_at is None
    assert requested.checked_out_by_user_id is None
    assert requested.version == 2
    projection = service.room_projections([room.id], as_of=NOW)[room.id]
    assert [item.id for item in projection.current_stays] == [stay.id]

    checked_out = service.confirm_checkout(
        stay.id,
        CheckoutConfirmation(version=requested.version),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )

    assert checked_out.status == "checked_out"
    assert checked_out.actual_check_out_at == proposed_at
    assert checked_out.checked_out_by_user_id == actor_id
    assert checked_out.checkout_requested_actual_at == proposed_at
    assert checked_out.version == 3
    released = service.room_projections([room.id], as_of=NOW)[room.id]
    assert released.current_stays == ()
    assert released.current_gender is None
    with pytest.raises(HousingInvalidStayTransition):
        service.confirm_checkout(
            stay.id,
            CheckoutConfirmation(version=checked_out.version),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )
    db.expire_all()
    preserved = db.get(HousingStay, stay.id)
    assert preserved is not None
    assert (preserved.status, preserved.version) == ("checked_out", 3)


def test_system_pending_reminder_can_enter_first_manual_step_once(db: Session) -> None:
    _, actor_id = _account_person(db, name="系统提醒处理人")
    resident, _ = _account_person(db, name="系统提醒住户")
    room = _room(db)
    stay = _stay(
        db,
        person_id=resident.id,
        room=room,
        actor_id=actor_id,
        status="checkout_pending",
        request_fields=False,
        version=2,
    )
    service = HousingOccupancyService(db)

    requested = service.request_checkout(
        stay.id,
        CheckoutRequest(
            version=stay.version,
            actual_check_out_at=NOW - timedelta(minutes=15),
        ),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )

    assert requested.status == "checkout_pending"
    assert requested.checkout_requested_by_user_id == actor_id
    assert requested.version == 3
    with pytest.raises(HousingInvalidStayTransition):
        service.request_checkout(
            stay.id,
            CheckoutRequest(
                version=requested.version,
                actual_check_out_at=NOW - timedelta(minutes=10),
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, stay.id)
    assert preserved is not None
    assert preserved.version == 3
    assert preserved.checkout_requested_actual_at == NOW - timedelta(minutes=15)


def test_checkout_confirmation_requires_manual_request_and_current_version(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="二次确认操作人")
    resident, _ = _account_person(db, name="二次确认住户")
    room = _room(db)
    system_pending = _stay(
        db,
        person_id=resident.id,
        room=room,
        actor_id=actor_id,
        status="checkout_pending",
        request_fields=False,
        version=2,
    )
    db.commit()
    service = HousingOccupancyService(db)

    with pytest.raises(HousingCheckoutConfirmationRequired):
        service.confirm_checkout(
            system_pending.id,
            CheckoutConfirmation(version=system_pending.version),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )

    requested = service.request_checkout(
        system_pending.id,
        CheckoutRequest(
            version=system_pending.version,
            actual_check_out_at=NOW - timedelta(minutes=15),
        ),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )
    with pytest.raises(HousingStaleVersion):
        service.confirm_checkout(
            requested.id,
            CheckoutConfirmation(version=requested.version - 1),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, requested.id)
    assert preserved is not None
    assert (preserved.status, preserved.version, preserved.actual_check_out_at) == (
        "checkout_pending",
        3,
        None,
    )


@pytest.mark.parametrize(
    "proposed_at",
    [
        NOW + timedelta(seconds=1),
        NOW - timedelta(days=2),
        NOW - timedelta(days=2, seconds=1),
    ],
)
def test_checkout_request_rejects_future_or_nonpositive_actual_period_atomically(
    db: Session,
    proposed_at: datetime,
) -> None:
    _, actor_id = _account_person(db, name=f"非法退房操作人-{proposed_at}")
    resident, _ = _account_person(db, name=f"非法退房住户-{proposed_at}")
    room = _room(db)
    stay = _stay(db, person_id=resident.id, room=room, actor_id=actor_id)
    db.commit()

    with pytest.raises(HousingInvalidStayTransition):
        HousingOccupancyService(db).request_checkout(
            stay.id,
            CheckoutRequest(version=stay.version, actual_check_out_at=proposed_at),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, stay.id)
    assert preserved is not None
    assert (
        preserved.status,
        preserved.version,
        preserved.checkout_requested_actual_at,
    ) == ("checked_in", 1, None)


def test_checkout_request_stale_version_is_atomic_and_session_remains_usable(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="过期退房操作人")
    resident, _ = _account_person(db, name="过期退房住户")
    room = _room(db)
    stay = _stay(db, person_id=resident.id, room=room, actor_id=actor_id)
    db.commit()

    with pytest.raises(HousingStaleVersion):
        HousingOccupancyService(db).request_checkout(
            stay.id,
            CheckoutRequest(
                version=stay.version + 1,
                actual_check_out_at=NOW - timedelta(minutes=10),
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, stay.id)
    assert preserved is not None
    assert (preserved.status, preserved.version) == ("checked_in", 1)
    assert db.get(HousingRoom, room.id) is not None


def test_transfer_atomically_moves_current_stay_and_records_history(db: Session) -> None:
    _, actor_id = _account_person(db, name="调房操作人")
    resident, _ = _account_person(db, name="调房住户")
    source_room = _room(db)
    target_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    source.notes = "保留的住宿备注"
    db.flush()
    authorized: list[tuple[str, UUID]] = []
    observed: list[str] = []
    room_lock_statements: list[str] = []
    connection = db.connection()

    def capture_locks(conn, cursor, statement, parameters, context, executemany):
        del conn, cursor, parameters, context, executemany
        if "FROM people" in statement and "FOR UPDATE" in statement:
            observed.append("people")
        elif "FROM housing_rooms" in statement and "FOR UPDATE" in statement:
            observed.append("rooms")
            room_lock_statements.append(statement)
        elif "FROM housing_stays" in statement and "FOR UPDATE" in statement:
            observed.append("stays")

    def authorize_source(room: HousingRoom) -> None:
        observed.append("authorize_source")
        authorized.append(("source", room.id))

    def authorize_target(room: HousingRoom) -> None:
        observed.append("authorize_target")
        authorized.append(("target", room.id))

    service = HousingOccupancyService(db)
    transferred_at = NOW - timedelta(hours=1)

    event.listen(connection, "before_cursor_execute", capture_locks)
    try:
        old_stay, new_stay, transfer = service.transfer(
            source.id,
            TransferStayRequest(
                version=source.version,
                target_room_id=target_room.id,
                transferred_at=transferred_at,
                reason="问题房临时调整",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=authorize_source,
            authorize_target=authorize_target,
        )
    finally:
        event.remove(connection, "before_cursor_execute", capture_locks)

    assert authorized == [("source", source_room.id), ("target", target_room.id)]
    assert observed.index("people") < observed.index("rooms") < observed.index("stays")
    assert observed.index("stays") < observed.index("authorize_source")
    assert observed.index("authorize_source") < observed.index("authorize_target")
    assert any("ORDER BY housing_rooms.id" in statement for statement in room_lock_statements)
    assert old_stay.id == source.id
    assert old_stay.status == "checked_out"
    assert old_stay.actual_check_out_at == transferred_at
    assert old_stay.checked_out_by_user_id == actor_id
    assert old_stay.version == 2
    assert new_stay.id != source.id
    assert new_stay.person_id == resident.id
    assert new_stay.room_id == target_room.id
    assert new_stay.status == "checked_in"
    assert new_stay.actual_check_in_at == transferred_at
    assert new_stay.planned_check_in_at == transferred_at
    assert new_stay.duration_mode == source.duration_mode
    assert new_stay.stay_purpose == source.stay_purpose
    assert new_stay.notes == source.notes
    assert new_stay.room_name_snapshot == target_room.room_number
    assert new_stay.version == 1
    assert transfer.person_id == resident.id
    assert transfer.from_stay_id == source.id
    assert transfer.to_stay_id == new_stay.id
    assert transfer.transferred_at == transferred_at
    assert transfer.transferred_by_user_id == actor_id
    assert transfer.reason == "问题房临时调整"
    assert service.room_projections([source_room.id], as_of=NOW)[
        source_room.id
    ].current_stays == ()
    assert [
        item.id
        for item in service.room_projections([target_room.id], as_of=NOW)[
            target_room.id
        ].current_stays
    ] == [new_stay.id]


def test_transfer_preserves_system_checkout_pending_state(db: Session) -> None:
    _, actor_id = _account_person(db, name="系统待退房调房操作人")
    resident, _ = _account_person(db, name="系统待退房调房住户")
    source_room = _room(db)
    target_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
        status="checkout_pending",
        request_fields=False,
        version=2,
    )
    requested_triplet = (
        source.checkout_requested_actual_at,
        source.checkout_requested_at,
        source.checkout_requested_by_user_id,
    )

    old_stay, new_stay, _ = HousingOccupancyService(db).transfer(
        source.id,
        TransferStayRequest(
            version=source.version,
            target_room_id=target_room.id,
            transferred_at=NOW - timedelta(minutes=30),
            reason="待退房期间安全调房",
        ),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_source=_allow,
        authorize_target=_allow,
    )

    assert old_stay.status == "checked_out"
    assert new_stay.status == "checkout_pending"
    assert (
        new_stay.checkout_requested_actual_at,
        new_stay.checkout_requested_at,
        new_stay.checkout_requested_by_user_id,
    ) == requested_triplet
    with pytest.raises(HousingCheckoutConfirmationRequired):
        HousingOccupancyService(db).confirm_checkout(
            new_stay.id,
            CheckoutConfirmation(version=new_stay.version),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )


def test_transfer_rejects_manually_requested_checkout_pending_atomically(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="人工待退房调房操作人")
    resident, _ = _account_person(db, name="人工待退房调房住户")
    source_room = _room(db)
    target_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
        status="checkout_pending",
        request_fields=True,
        version=2,
    )
    source_id = source.id
    db.commit()

    with pytest.raises(HousingInvalidStayTransition):
        HousingOccupancyService(db).transfer(
            source_id,
            TransferStayRequest(
                version=2,
                target_room_id=target_room.id,
                transferred_at=NOW - timedelta(minutes=15),
                reason="人工申请后不得调房",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=_allow,
            authorize_target=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, source_id)
    assert preserved is not None
    assert (preserved.status, preserved.version, preserved.actual_check_out_at) == (
        "checkout_pending",
        2,
        None,
    )
    assert db.scalar(select(func.count()).select_from(HousingTransfer)) == 0


def test_fixed_term_transfer_copies_only_the_remaining_interval(db: Session) -> None:
    _, actor_id = _account_person(db, name="定期调房操作人")
    resident, _ = _account_person(db, name="定期调房住户")
    source_room = _room(db)
    target_room = _room(db)
    planned_out = NOW + timedelta(days=3)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
        duration_mode="fixed_term",
        planned_check_out_at=planned_out,
    )

    _, destination, _ = HousingOccupancyService(db).transfer(
        source.id,
        TransferStayRequest(
            version=source.version,
            target_room_id=target_room.id,
            transferred_at=NOW - timedelta(minutes=30),
            reason="定期住宿调房",
        ),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_source=_allow,
        authorize_target=_allow,
    )

    assert destination.duration_mode == "fixed_term"
    assert destination.planned_check_in_at == NOW - timedelta(minutes=30)
    assert destination.planned_check_out_at == planned_out


def test_backdated_transfer_rejects_expired_fixed_term_at_request_time_atomically(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="到期回填调房操作人")
    resident, _ = _account_person(db, name="到期回填调房住户")
    source_room = _room(db)
    target_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
        duration_mode="fixed_term",
        planned_check_out_at=NOW,
    )
    source_id = source.id
    source_state = (source.status, source.actual_check_out_at, source.version)
    stay_count_before = db.scalar(select(func.count()).select_from(HousingStay)) or 0
    transfer_count_before = db.scalar(select(func.count()).select_from(HousingTransfer)) or 0
    destination_count_before = (
        db.scalar(
            select(func.count())
            .select_from(HousingStay)
            .where(
                HousingStay.person_id == resident.id,
                HousingStay.room_id == target_room.id,
            )
        )
        or 0
    )
    assert destination_count_before == 0
    db.commit()

    with pytest.raises(HousingInvalidStayTransition):
        HousingOccupancyService(db).transfer(
            source_id,
            TransferStayRequest(
                version=1,
                target_room_id=target_room.id,
                transferred_at=NOW - timedelta(minutes=30),
                reason="不得通过回填时间绕过到期限制",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=_allow,
            authorize_target=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, source_id)
    assert preserved is not None
    assert (preserved.status, preserved.actual_check_out_at, preserved.version) == source_state
    assert db.scalar(select(func.count()).select_from(HousingStay)) == stay_count_before
    assert db.scalar(select(func.count()).select_from(HousingTransfer)) == transfer_count_before
    assert (
        db.scalar(
            select(func.count())
            .select_from(HousingStay)
            .where(
                HousingStay.person_id == resident.id,
                HousingStay.room_id == target_room.id,
            )
        )
        == destination_count_before
    )


@pytest.mark.parametrize("stay_purpose", ["camp", "temporary"])
def test_transfer_preserves_camp_or_temporary_purpose_details(
    db: Session,
    stay_purpose: str,
) -> None:
    _, actor_id = _account_person(db, name=f"用途调房操作人-{stay_purpose}")
    resident, _ = _account_person(db, name=f"用途调房住户-{stay_purpose}")
    source_room = _room(db)
    target_room = _room(db)
    planned_out = NOW + timedelta(days=3)
    cohort = None
    if stay_purpose == "camp":
        cohort = Cohort(
            id=uuid4(),
            name=f"调房营期-{uuid4().hex[:8]}",
            starts_at=NOW - timedelta(days=5),
            ends_at=planned_out,
            default_check_in_at=NOW - timedelta(days=5),
            default_check_out_at=planned_out,
            status="active",
            created_by_user_id=actor_id,
        )
        db.add(cohort)
        db.flush()
    visit_reason = "家长临时探访" if stay_purpose == "temporary" else None
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
        duration_mode="fixed_term",
        planned_check_out_at=planned_out,
        stay_purpose=stay_purpose,
        cohort_id=cohort.id if cohort is not None else None,
        visit_reason=visit_reason,
    )

    _, destination, _ = HousingOccupancyService(db).transfer(
        source.id,
        TransferStayRequest(
            version=source.version,
            target_room_id=target_room.id,
            transferred_at=NOW - timedelta(minutes=30),
            reason="保留住宿用途",
        ),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_source=_allow,
        authorize_target=_allow,
    )

    assert destination.stay_purpose == stay_purpose
    assert destination.cohort_id == (cohort.id if cohort is not None else None)
    assert destination.visit_reason == visit_reason
    assert destination.planned_check_out_at == planned_out


@pytest.mark.parametrize(
    ("target_status", "deny_target", "expected_error"),
    [
        ("maintenance", False, HousingRoomUnavailable),
        ("available", True, HousingResourceNotFound),
    ],
)
def test_transfer_target_failure_leaves_source_and_history_untouched(
    db: Session,
    target_status: str,
    deny_target: bool,
    expected_error: type[Exception],
) -> None:
    _, actor_id = _account_person(db, name=f"失败调房操作人-{target_status}-{deny_target}")
    resident, _ = _account_person(db, name=f"失败调房住户-{target_status}-{deny_target}")
    source_room = _room(db)
    target_room = _room(db, service_status=target_status)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    source_id = source.id
    db.commit()

    def authorize_target(room: HousingRoom) -> None:
        del room
        if deny_target:
            raise HousingResourceNotFound("housing room not found")

    with pytest.raises(expected_error):
        HousingOccupancyService(db).transfer(
            source_id,
            TransferStayRequest(
                version=1,
                target_room_id=target_room.id,
                transferred_at=NOW - timedelta(minutes=30),
                reason="目标房失败",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=_allow,
            authorize_target=authorize_target,
        )

    db.expire_all()
    preserved = db.get(HousingStay, source_id)
    assert preserved is not None
    assert (
        preserved.status,
        preserved.actual_check_out_at,
        preserved.version,
    ) == ("checked_in", None, 1)
    assert db.scalar(select(func.count()).select_from(HousingTransfer)) == 0
    assert db.scalar(
        select(func.count())
        .select_from(HousingStay)
        .where(HousingStay.person_id == resident.id)
    ) == 1


def test_transfer_stale_version_is_atomic_and_session_remains_usable(db: Session) -> None:
    _, actor_id = _account_person(db, name="过期调房操作人")
    resident, _ = _account_person(db, name="过期调房住户")
    source_room = _room(db)
    target_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    source_id = source.id
    db.commit()

    with pytest.raises(HousingStaleVersion):
        HousingOccupancyService(db).transfer(
            source_id,
            TransferStayRequest(
                version=2,
                target_room_id=target_room.id,
                transferred_at=NOW - timedelta(minutes=30),
                reason="过期版本调房",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=_allow,
            authorize_target=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, source_id)
    assert preserved is not None
    assert (preserved.status, preserved.actual_check_out_at, preserved.version) == (
        "checked_in",
        None,
        1,
    )
    assert db.scalar(select(func.count()).select_from(HousingTransfer)) == 0


@pytest.mark.parametrize("failure_point", ["destination_stay", "transfer_history"])
def test_transfer_flush_failure_is_typed_atomic_and_session_remains_usable(
    db: Session,
    failure_point: str,
) -> None:
    _, actor_id = _account_person(db, name=f"数据库失败调房操作人-{failure_point}")
    resident, _ = _account_person(db, name=f"数据库失败调房住户-{failure_point}")
    source_room = _room(db)
    target_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    source_id = source.id
    resident_id = resident.id
    if failure_point == "destination_stay":
        _install_destination_stay_failure(db, target_room_id=target_room.id)
    else:
        _install_transfer_history_failure(db, source_stay_id=source.id)
    db.commit()

    with pytest.raises(HousingServiceError) as error:
        HousingOccupancyService(db).transfer(
            source_id,
            TransferStayRequest(
                version=1,
                target_room_id=target_room.id,
                transferred_at=NOW - timedelta(minutes=30),
                reason=f"强制 {failure_point} flush 失败",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=_allow,
            authorize_target=_allow,
        )

    assert error.value.code == "housing_persistence_failure"
    assert str(error.value) == "housing transfer could not be completed"
    assert "forced" not in str(error.value)
    db.expire_all()
    preserved = db.get(HousingStay, source_id)
    assert preserved is not None
    assert (preserved.status, preserved.actual_check_out_at, preserved.version) == (
        "checked_in",
        None,
        1,
    )
    assert db.scalar(
        select(func.count()).select_from(HousingStay).where(
            HousingStay.person_id == resident_id
        )
    ) == 1
    assert db.scalar(select(func.count()).select_from(HousingTransfer)) == 0


@pytest.mark.parametrize(
    ("target_capacity", "occupant_sex", "expected_error"),
    [
        (1, "male", HousingCapacityConflict),
        (2, "female", HousingGenderConflict),
    ],
)
def test_transfer_capacity_or_gender_conflict_is_atomic(
    db: Session,
    target_capacity: int,
    occupant_sex: str,
    expected_error: type[Exception],
) -> None:
    _, actor_id = _account_person(
        db,
        name=f"冲突调房操作人-{target_capacity}-{occupant_sex}",
    )
    resident, _ = _account_person(
        db,
        name=f"男性冲突调房住户-{target_capacity}-{occupant_sex}",
        sex="male",
    )
    occupant, _ = _account_person(
        db,
        name=f"目标房住户-{target_capacity}-{occupant_sex}",
        sex=occupant_sex,
    )
    source_room = _room(db)
    target_room = _room(db, capacity=target_capacity)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    _stay(
        db,
        person_id=occupant.id,
        room=target_room,
        actor_id=actor_id,
    )
    source_id = source.id
    db.commit()

    with pytest.raises(expected_error):
        HousingOccupancyService(db).transfer(
            source_id,
            TransferStayRequest(
                version=1,
                target_room_id=target_room.id,
                transferred_at=NOW - timedelta(minutes=30),
                reason="目标房冲突",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=_allow,
            authorize_target=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, source_id)
    assert preserved is not None
    assert (preserved.status, preserved.actual_check_out_at, preserved.version) == (
        "checked_in",
        None,
        1,
    )
    assert db.scalar(select(func.count()).select_from(HousingTransfer)) == 0


def test_backdated_transfer_rejects_overlapping_checked_out_capacity_history(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="历史容量冲突调房操作人")
    resident, _ = _account_person(db, name="历史容量冲突调房住户", sex="male")
    prior_occupant, _ = _account_person(db, name="历史容量目标房住户", sex="male")
    source_room = _room(db)
    target_room = _room(db, capacity=1)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    history = _stay(
        db,
        person_id=prior_occupant.id,
        room=target_room,
        actor_id=actor_id,
    )
    history.status = "checked_out"
    history.actual_check_out_at = NOW - timedelta(minutes=15)
    history.checked_out_by_user_id = actor_id
    source_id = source.id
    history_id = history.id
    db.commit()

    with pytest.raises(HousingCapacityConflict):
        HousingOccupancyService(db).transfer(
            source_id,
            TransferStayRequest(
                version=1,
                target_room_id=target_room.id,
                transferred_at=NOW - timedelta(minutes=30),
                reason="回溯调房不得覆盖历史容量",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=_allow,
            authorize_target=_allow,
        )

    db.expire_all()
    preserved_source = db.get(HousingStay, source_id)
    preserved_history = db.get(HousingStay, history_id)
    assert preserved_source is not None
    assert preserved_history is not None
    assert (
        preserved_source.status,
        preserved_source.actual_check_out_at,
        preserved_source.version,
    ) == ("checked_in", None, 1)
    assert (
        preserved_history.status,
        preserved_history.actual_check_out_at,
        preserved_history.version,
    ) == ("checked_out", NOW - timedelta(minutes=15), 1)
    assert db.scalar(select(func.count()).select_from(HousingTransfer)) == 0


def test_backdated_transfer_rejects_overlapping_checked_out_gender_history(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="历史性别冲突调房操作人")
    resident, _ = _account_person(db, name="历史性别冲突调房住户", sex="male")
    prior_occupant, _ = _account_person(db, name="历史异性目标房住户", sex="female")
    source_room = _room(db)
    target_room = _room(db, capacity=2)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    history = _stay(
        db,
        person_id=prior_occupant.id,
        room=target_room,
        actor_id=actor_id,
    )
    history.status = "checked_out"
    history.actual_check_out_at = NOW - timedelta(minutes=15)
    history.checked_out_by_user_id = actor_id
    source_id = source.id
    history_id = history.id
    db.commit()

    with pytest.raises(HousingGenderConflict):
        HousingOccupancyService(db).transfer(
            source_id,
            TransferStayRequest(
                version=1,
                target_room_id=target_room.id,
                transferred_at=NOW - timedelta(minutes=30),
                reason="回溯调房不得覆盖历史性别",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=_allow,
            authorize_target=_allow,
        )

    db.expire_all()
    preserved_source = db.get(HousingStay, source_id)
    preserved_history = db.get(HousingStay, history_id)
    assert preserved_source is not None
    assert preserved_history is not None
    assert (
        preserved_source.status,
        preserved_source.actual_check_out_at,
        preserved_source.version,
    ) == ("checked_in", None, 1)
    assert (
        preserved_history.status,
        preserved_history.actual_check_out_at,
        preserved_history.version,
    ) == ("checked_out", NOW - timedelta(minutes=15), 1)
    assert db.scalar(select(func.count()).select_from(HousingTransfer)) == 0


@pytest.mark.parametrize(
    ("target_capacity", "resident_sexes", "expected_loser"),
    [
        (1, ("male", "male"), "housing_capacity_conflict"),
        (2, ("male", "female"), "housing_gender_conflict"),
    ],
)
def test_concurrent_transfers_commit_one_typed_winner_without_partial_history(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
    target_capacity: int,
    resident_sexes: tuple[str, str],
    expected_loser: str,
) -> None:
    with Session(postgres_engine) as setup, setup.begin():
        actor, actor_id = _account_person(
            setup,
            name=f"并发调房操作人-{expected_loser}",
        )
        residents = [
            _account_person(
                setup,
                name=f"并发调房住户-{expected_loser}-{index}",
                sex=sex,
            )[0]
            for index, sex in enumerate(resident_sexes)
        ]
        source_rooms = [_room(setup) for _ in residents]
        target_room = _room(setup, capacity=target_capacity)
        sources = [
            _stay(
                setup,
                person_id=resident.id,
                room=source_room,
                actor_id=actor_id,
            )
            for resident, source_room in zip(residents, source_rooms, strict=True)
        ]
        rooms = [*source_rooms, target_room]
        floors = [setup.get(HousingFloor, room.floor_id) for room in rooms]
        buildings = [setup.get(HousingBuilding, room.building_id) for room in rooms]
        assert all(floor is not None for floor in floors)
        assert all(building is not None for building in buildings)
        person_ids = (actor.id, *(resident.id for resident in residents))
        room_ids = tuple(room.id for room in rooms)
        floor_ids = tuple(room.floor_id for room in rooms)
        building_ids = tuple(room.building_id for room in rooms)
        source_ids = tuple(source.id for source in sources)
        resident_ids = tuple(resident.id for resident in residents)
        target_room_id = target_room.id

    request.addfinalizer(
        partial(
            _cleanup_transfer_race,
            postgres_engine,
            person_ids=person_ids,
            room_ids=room_ids,
            floor_ids=floor_ids,
            building_ids=building_ids,
        )
    )
    first_room_lock_acquired = Event()
    release_first_room_lock = Event()
    second_room_lock_attempted = Event()
    second_room_lock_acquired = Event()
    first_backend_pids: Queue[int] = Queue()
    second_backend_pids: Queue[int] = Queue()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            _transfer_worker,
            postgres_engine,
            source_stay_id=source_ids[0],
            target_room_id=target_room_id,
            actor_id=actor_id,
            backend_pids=first_backend_pids,
            room_lock_acquired=first_room_lock_acquired,
            release_room_lock=release_first_room_lock,
        )
        # Set only after PostgreSQL completes the sorted room FOR UPDATE query.
        assert first_room_lock_acquired.wait(timeout=5)
        try:
            first_backend_pid = first_backend_pids.get(timeout=5)
            second = executor.submit(
                _transfer_worker,
                postgres_engine,
                source_stay_id=source_ids[1],
                target_room_id=target_room_id,
                actor_id=actor_id,
                backend_pids=second_backend_pids,
                room_lock_attempted=second_room_lock_attempted,
                room_lock_acquired=second_room_lock_acquired,
            )
            # The second query was issued but cannot complete while the first holds
            # the shared target-room row lock.
            assert second_room_lock_attempted.wait(timeout=5)
            second_backend_pid = second_backend_pids.get(timeout=5)
            _wait_for_postgres_blocker(
                postgres_engine,
                blocked_pid=second_backend_pid,
                blocker_pid=first_backend_pid,
                description="second transfer target-room lock",
            )
            assert not second_room_lock_acquired.is_set()
        finally:
            release_first_room_lock.set()
        results = [first.result(timeout=15), second.result(timeout=15)]

    assert second_room_lock_acquired.is_set()
    assert results[0].outcome == "committed"
    assert results[1].outcome == expected_loser
    assert all(result.session_reusable for result in results)
    for result in results:
        assert result.lock_order.index("people") < result.lock_order.index("rooms")
        assert result.lock_order.index("rooms") < result.lock_order.index("stays")
        assert result.ordered_room_lock is True

    with Session(postgres_engine) as verification:
        stays = list(
            verification.scalars(
                select(HousingStay)
                .where(HousingStay.person_id.in_(resident_ids))
                .order_by(HousingStay.person_id, HousingStay.id)
            )
        )
        transfers = list(
            verification.scalars(
                select(HousingTransfer)
                .where(HousingTransfer.person_id.in_(resident_ids))
                .order_by(HousingTransfer.person_id)
            )
        )
        target_current = [
            stay
            for stay in stays
            if stay.room_id == target_room_id
            and stay.status in {"checked_in", "checkout_pending"}
        ]
        assert len(target_current) == 1
        assert len(target_current) <= target_capacity
        target_sexes = {
            verification.get(Person, stay.person_id).sex
            for stay in target_current
            if verification.get(Person, stay.person_id) is not None
        }
        assert len(target_sexes) == 1
        assert len(transfers) == 1

        transfer = transfers[0]
        for resident_id, source_id in zip(resident_ids, source_ids, strict=True):
            resident_stays = [stay for stay in stays if stay.person_id == resident_id]
            active = [
                stay
                for stay in resident_stays
                if stay.status in {"planned", "checked_in", "checkout_pending"}
            ]
            assert len(active) == 1
            if transfer.person_id == resident_id:
                assert len(resident_stays) == 2
                assert {stay.status for stay in resident_stays} == {
                    "checked_in",
                    "checked_out",
                }
                assert transfer.from_stay_id == source_id
                assert transfer.to_stay_id in {stay.id for stay in resident_stays}
            else:
                assert len(resident_stays) == 1
                assert resident_stays[0].id == source_id
                assert resident_stays[0].status == "checked_in"


@pytest.mark.parametrize(
    "transferred_at",
    [
        NOW + timedelta(seconds=1),
        NOW - timedelta(days=2),
        NOW - timedelta(days=2, seconds=1),
    ],
)
def test_transfer_rejects_future_or_nonpositive_source_period(
    db: Session,
    transferred_at: datetime,
) -> None:
    _, actor_id = _account_person(db, name=f"非法调房操作人-{transferred_at}")
    resident, _ = _account_person(db, name=f"非法调房住户-{transferred_at}")
    source_room = _room(db)
    target_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    source_id = source.id
    db.commit()

    with pytest.raises(HousingInvalidStayTransition):
        HousingOccupancyService(db).transfer(
            source_id,
            TransferStayRequest(
                version=1,
                target_room_id=target_room.id,
                transferred_at=transferred_at,
                reason="非法调房时间",
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_source=_allow,
            authorize_target=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, source_id)
    assert preserved is not None
    assert (preserved.status, preserved.actual_check_out_at, preserved.version) == (
        "checked_in",
        None,
        1,
    )


def test_transfer_rejects_same_room_and_expired_fixed_term_before_flush(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="边界调房操作人")
    resident, _ = _account_person(db, name="边界调房住户")
    source_room = _room(db)
    target_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
        duration_mode="fixed_term",
        planned_check_out_at=NOW - timedelta(minutes=1),
    )
    source_id = source.id
    db.commit()

    for target_room_id, transferred_at in (
        (source_room.id, NOW - timedelta(minutes=30)),
        (target_room.id, NOW),
    ):
        with pytest.raises(HousingInvalidStayTransition):
            HousingOccupancyService(db).transfer(
                source_id,
                TransferStayRequest(
                    version=1,
                    target_room_id=target_room_id,
                    transferred_at=transferred_at,
                    reason="无剩余区间",
                ),
                actor_user_id=actor_id,
                occurred_at=NOW,
                authorize_source=_allow,
                authorize_target=_allow,
            )

    db.expire_all()
    preserved = db.get(HousingStay, source_id)
    assert preserved is not None
    assert (preserved.status, preserved.version) == ("checked_in", 1)


class _TransferAccess:
    access_revision = "a" * 64

    def __init__(
        self,
        readable_ids: set[UUID],
        manageable_ids: set[UUID] | None = None,
    ) -> None:
        self.readable_ids = readable_ids
        self.manageable_ids = (
            readable_ids if manageable_ids is None else manageable_ids
        )

    def can_read_room(self, room: HousingRoom) -> bool:
        return room.id in self.readable_ids

    def can_manage_transfers(self, room: HousingRoom) -> bool:
        return room.id in self.manageable_ids

    def room_permissions_criteria(self, *permissions: str):
        assert permissions == ("housing.read", "housing.transfers.manage")
        return HousingRoom.id.in_(self.readable_ids) & HousingRoom.id.in_(
            self.manageable_ids
        )


def _permission_id(db: Session, key: str) -> UUID:
    permission_id = db.scalar(select(Permission.id).where(Permission.key == key))
    assert permission_id is not None
    return permission_id


def _housing_override(
    *,
    account_id: UUID,
    permission_id: UUID,
    effect: str,
    scope_kind: str,
    scope_id: UUID,
) -> UserPermissionOverride:
    return UserPermissionOverride(
        user_id=account_id,
        permission_id=permission_id,
        effect=effect,
        scope_kind=scope_kind,
        scope_id=scope_id,
    )


def test_transfer_options_apply_scoped_sql_before_loading_rooms_or_residents(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, actor_id = _account_person(db, name="SQL 调房候选操作人")
    resident, _ = _account_person(db, name="SQL 调房候选住户", sex="male")
    source_room = _room(db)
    building_scoped_room = _room(db, capacity=2)
    floor_scoped_room = _room(db, capacity=2)
    denied_room = _room(db, capacity=2)
    read_only_room = _room(db, capacity=2)
    manage_only_room = _room(db, capacity=2)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    authorized_occupant, _ = _account_person(
        db,
        name="授权房间住户敏感姓名",
        sex="male",
    )
    hidden_occupant, _ = _account_person(
        db,
        name="未授权房间住户敏感姓名",
        sex="female",
    )
    _stay(
        db,
        person_id=authorized_occupant.id,
        room=building_scoped_room,
        actor_id=actor_id,
    )
    _stay(
        db,
        person_id=hidden_occupant.id,
        room=denied_room,
        actor_id=actor_id,
    )
    read_permission_id = _permission_id(db, "housing.read")
    transfer_permission_id = _permission_id(db, "housing.transfers.manage")
    db.add_all(
        [
            _housing_override(
                account_id=actor_id,
                permission_id=read_permission_id,
                effect="allow",
                scope_kind="building",
                scope_id=source_room.building_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=transfer_permission_id,
                effect="allow",
                scope_kind="floor",
                scope_id=source_room.floor_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=read_permission_id,
                effect="allow",
                scope_kind="building",
                scope_id=building_scoped_room.building_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=transfer_permission_id,
                effect="allow",
                scope_kind="building",
                scope_id=building_scoped_room.building_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=read_permission_id,
                effect="allow",
                scope_kind="floor",
                scope_id=floor_scoped_room.floor_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=transfer_permission_id,
                effect="allow",
                scope_kind="floor",
                scope_id=floor_scoped_room.floor_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=read_permission_id,
                effect="allow",
                scope_kind="building",
                scope_id=denied_room.building_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=read_permission_id,
                effect="deny",
                scope_kind="floor",
                scope_id=denied_room.floor_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=transfer_permission_id,
                effect="allow",
                scope_kind="building",
                scope_id=denied_room.building_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=read_permission_id,
                effect="allow",
                scope_kind="building",
                scope_id=read_only_room.building_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=transfer_permission_id,
                effect="allow",
                scope_kind="floor",
                scope_id=manage_only_room.floor_id,
            ),
        ]
    )
    db.commit()
    source_id = source.id
    expected_ids = {building_scoped_room.id, floor_scoped_room.id}
    excluded_ids = {denied_room.id, read_only_room.id, manage_only_room.id}
    access = HousingAccessService(db, actor_id, NOW)
    db.expunge_all()

    def reject_room_projections(*args, **kwargs):
        del args, kwargs
        raise AssertionError("transfer options must not call room_projections")

    monkeypatch.setattr(
        HousingOccupancyService,
        "room_projections",
        reject_room_projections,
    )
    loaded_models: list[object] = []
    statements: list[str] = []

    def capture_load(_session, instance: object) -> None:
        loaded_models.append(instance)

    def capture_sql(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(" ".join(statement.lower().split()))

    connection = db.connection()
    event.listen(db, "loaded_as_persistent", capture_load)
    event.listen(connection, "before_cursor_execute", capture_sql)
    try:
        response = HousingOccupancyService(db).list_transfer_options(
            source_id,
            occurred_at=NOW,
            access=access,
        )
    finally:
        event.remove(db, "loaded_as_persistent", capture_load)
        event.remove(connection, "before_cursor_execute", capture_sql)

    assert {item.room_id for item in response.items} == expected_ids
    assert excluded_ids.isdisjoint(item.room_id for item in response.items)
    assert [
        (
            item.building_label,
            item.floor_label,
            item.room_label,
            item.room_id,
        )
        for item in response.items
    ] == sorted(
        (
            item.building_label,
            item.floor_label,
            item.room_label,
            item.room_id,
        )
        for item in response.items
    )
    building_option = next(
        item for item in response.items if item.room_id == building_scoped_room.id
    )
    assert (building_option.current_occupants, building_option.current_gender) == (1, "male")
    assert not any(isinstance(model, (HousingRoom, Person)) for model in loaded_models)
    sql = "\n".join(statements)
    for sensitive_column in (
        "people.full_name",
        "people.identity_ciphertext",
        "people.phone_ciphertext",
        "housing_stays.room_name_snapshot",
        "housing_stays.floor_name_snapshot",
        "housing_stays.building_name_snapshot",
        "housing_stays.notes",
    ):
        assert sensitive_column not in sql


def test_transfer_options_source_deny_is_private_and_does_not_materialize_source(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="SQL deny-first 调房操作人")
    resident, _ = _account_person(db, name="SQL deny-first 调房住户")
    source_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    read_permission_id = _permission_id(db, "housing.read")
    transfer_permission_id = _permission_id(db, "housing.transfers.manage")
    db.add_all(
        [
            _housing_override(
                account_id=actor_id,
                permission_id=read_permission_id,
                effect="allow",
                scope_kind="building",
                scope_id=source_room.building_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=read_permission_id,
                effect="deny",
                scope_kind="floor",
                scope_id=source_room.floor_id,
            ),
            _housing_override(
                account_id=actor_id,
                permission_id=transfer_permission_id,
                effect="allow",
                scope_kind="floor",
                scope_id=source_room.floor_id,
            ),
        ]
    )
    db.commit()
    source_id = source.id
    access = HousingAccessService(db, actor_id, NOW)
    db.expunge_all()
    loaded_models: list[object] = []

    def capture_load(_session, instance: object) -> None:
        loaded_models.append(instance)

    event.listen(db, "loaded_as_persistent", capture_load)
    try:
        with pytest.raises(HousingResourceNotFound) as error:
            HousingOccupancyService(db).list_transfer_options(
                source_id,
                occurred_at=NOW,
                access=access,
            )
    finally:
        event.remove(db, "loaded_as_persistent", capture_load)

    assert error.value.code == "housing_resource_not_found"
    assert str(error.value) == "housing stay not found"
    assert not any(isinstance(model, (HousingRoom, Person)) for model in loaded_models)


def test_transfer_options_have_stable_order_and_hard_limit(db: Session) -> None:
    _, actor_id = _account_person(db, name="调房候选上限操作人")
    resident, _ = _account_person(db, name="调房候选上限住户")
    source_room = _room(db)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    target_rooms = [_room(db, capacity=2) for _ in range(201)]
    readable_ids = {source_room.id, *(room.id for room in target_rooms)}

    response = HousingOccupancyService(db).list_transfer_options(
        source.id,
        occurred_at=NOW,
        access=_TransferAccess(readable_ids),
    )

    assert len(response.items) == 200
    assert [
        (
            item.building_label,
            item.floor_label,
            item.room_label,
            item.room_id,
        )
        for item in response.items
    ] == sorted(
        (
            item.building_label,
            item.floor_label,
            item.room_label,
            item.room_id,
        )
        for item in response.items
    )


def test_transfer_options_respect_scope_and_report_advisory_compatibility(
    db: Session,
) -> None:
    _, actor_id = _account_person(db, name="调房候选操作人")
    resident, _ = _account_person(db, name="男性调房候选住户", sex="male")
    source_room = _room(db)
    compatible_room = _room(db, capacity=2)
    full_room = _room(db, capacity=1)
    female_room = _room(db, capacity=2)
    hidden_room = _room(db, capacity=2)
    unavailable_room = _room(db, capacity=2, service_status="maintenance")
    pending_room = _room(db, capacity=2, verification_status="pending_verification")
    functional_room = _room(db, capacity=0, room_type="functional")
    problem_room = _room(db, capacity=2, service_status="problem")
    disabled_room = _room(db, capacity=2, service_status="disabled")
    inactive_floor_room = _room(db, capacity=2)
    inactive_floor = db.get(HousingFloor, inactive_floor_room.floor_id)
    assert inactive_floor is not None
    inactive_floor.is_active = False
    inactive_building_room = _room(db, capacity=2)
    inactive_building = db.get(HousingBuilding, inactive_building_room.building_id)
    assert inactive_building is not None
    inactive_building.is_active = False
    readable_without_manage = _room(db, capacity=2)
    manageable_without_read = _room(db, capacity=2)
    source = _stay(
        db,
        person_id=resident.id,
        room=source_room,
        actor_id=actor_id,
    )
    full_resident, _ = _account_person(db, name="满房住户", sex="male")
    female_resident, _ = _account_person(db, name="女住户", sex="female")
    _stay(
        db,
        person_id=full_resident.id,
        room=full_room,
        actor_id=actor_id,
    )
    _stay(
        db,
        person_id=female_resident.id,
        room=female_room,
        actor_id=actor_id,
    )
    readable_ids = {
        source_room.id,
        compatible_room.id,
        full_room.id,
        female_room.id,
        unavailable_room.id,
        pending_room.id,
        functional_room.id,
        problem_room.id,
        disabled_room.id,
        inactive_floor_room.id,
        inactive_building_room.id,
        readable_without_manage.id,
    }
    manageable_ids = {
        source_room.id,
        compatible_room.id,
        full_room.id,
        female_room.id,
        unavailable_room.id,
        pending_room.id,
        functional_room.id,
        problem_room.id,
        disabled_room.id,
        inactive_floor_room.id,
        inactive_building_room.id,
        manageable_without_read.id,
    }

    response = HousingOccupancyService(db).list_transfer_options(
        source.id,
        occurred_at=NOW,
        access=_TransferAccess(readable_ids, manageable_ids),
    )

    by_id = {item.room_id: item for item in response.items}
    assert response.access_revision == "a" * 64
    assert source_room.id not in by_id
    assert hidden_room.id not in by_id
    excluded_ids = {
        unavailable_room.id,
        pending_room.id,
        functional_room.id,
        problem_room.id,
        disabled_room.id,
        inactive_floor_room.id,
        inactive_building_room.id,
        readable_without_manage.id,
        manageable_without_read.id,
    }
    assert excluded_ids.isdisjoint(by_id)
    assert by_id[compatible_room.id].advisory_compatible is True
    assert by_id[full_room.id].advisory_compatible is False
    assert by_id[female_room.id].advisory_compatible is False
    assert by_id[full_room.id].current_occupants == 1
    assert by_id[female_room.id].current_gender == "female"
