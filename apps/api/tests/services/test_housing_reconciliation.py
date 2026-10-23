from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, event, func, literal, select, text
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import UserAccount
from app.housing.errors import HousingStaleVersion
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingStay,
    HousingTransfer,
)
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.reconciliation import HousingCheckoutReconciler
from app.housing.schemas import CheckoutRequest, TransferStayRequest
from app.identity.models import Person, StaffProfile
from app.teaching.models import Cohort, TeachingClass

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


def _person_account(db: Session, *, name: str) -> tuple[Person, UserAccount]:
    marker = uuid4().hex
    person = Person(
        id=uuid4(),
        full_name=name,
        identity_ciphertext=f"reconcile-identity-{marker}",
        identity_digest=f"reconcile-identity-digest-{marker}",
        phone_ciphertext=f"reconcile-phone-{marker}",
        phone_digest=f"reconcile-phone-digest-{marker}",
        email=f"{marker}@reconcile.example.test",
        sex="male",
        date_of_birth=date(2000, 1, 1),
    )
    account = UserAccount(
        id=uuid4(),
        person_id=person.id,
        login_phone_digest=f"reconcile-login-{marker}",
        email_normalized=f"{marker}@reconcile-login.example.test",
        password_hash="reconcile-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(person)
    db.flush()
    db.add_all(
        [
            account,
            StaffProfile(person_id=person.id, staff_type="life_teacher"),
        ]
    )
    db.flush()
    return person, account


def _room(db: Session) -> HousingRoom:
    suffix = uuid4().hex[:10].upper()
    building = HousingBuilding(
        id=uuid4(),
        code=f"R{suffix[:8]}",
        name=f"协调测试楼{suffix}",
        display_order=0,
        is_active=True,
    )
    floor = HousingFloor(
        id=uuid4(),
        building_id=building.id,
        number=2,
        name=f"协调二楼{suffix}",
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
        capacity=20,
        verification_status="verified",
        service_status="available",
        version=1,
    )
    db.add_all([building, floor, room])
    db.flush()
    return room


def _cohort(
    db: Session,
    *,
    actor_id: UUID,
    name: str,
    ends_at: datetime,
    status: str = "active",
) -> Cohort:
    cohort = Cohort(
        id=uuid4(),
        name=f"{name}-{uuid4().hex[:8]}",
        starts_at=NOW - timedelta(days=10),
        ends_at=ends_at,
        default_check_in_at=NOW - timedelta(days=10),
        default_check_out_at=ends_at,
        status=status,
        created_by_user_id=actor_id,
    )
    db.add(cohort)
    db.flush()
    return cohort


def _stay(
    db: Session,
    *,
    person_id: UUID,
    room: HousingRoom,
    actor_id: UUID,
    status: str = "checked_in",
    duration_mode: str = "long_term",
    planned_check_out_at: datetime | None = None,
    stay_purpose: str = "permanent",
    cohort_id: UUID | None = None,
) -> HousingStay:
    actual_check_in_at = NOW - timedelta(days=5)
    current = status in {"checked_in", "checkout_pending", "checked_out"}
    checked_out = status == "checked_out"
    cancelled = status == "cancelled"
    stay = HousingStay(
        id=uuid4(),
        person_id=person_id,
        room_id=room.id,
        stay_purpose=stay_purpose,
        duration_mode=duration_mode,
        cohort_id=cohort_id,
        planned_check_in_at=NOW - timedelta(days=5),
        planned_check_out_at=planned_check_out_at,
        actual_check_in_at=actual_check_in_at if current else None,
        actual_check_out_at=(NOW - timedelta(days=1)) if checked_out else None,
        status=status,
        room_name_snapshot=room.room_number,
        floor_name_snapshot="协调二楼",
        building_name_snapshot="协调测试楼",
        cancellation_reason="计划取消" if cancelled else None,
        created_by_user_id=actor_id,
        checked_in_by_user_id=actor_id if current else None,
        checked_out_by_user_id=actor_id if checked_out else None,
        cancelled_by_user_id=actor_id if cancelled else None,
        version=1,
    )
    db.add(stay)
    db.flush()
    return stay


def _enrollment(
    db: Session,
    *,
    actor_id: UUID,
    student_id: UUID,
    cohort: Cohort,
    status: str,
) -> Enrollment:
    teaching_class = TeachingClass(
        id=uuid4(),
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"协调班级-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=actor_id,
    )
    admission_case = AdmissionCase(
        id=uuid4(),
        student_person_id=student_id,
        status="ready",
        agreed_tuition_cents=100_000,
        created_by_user_id=actor_id,
    )
    db.add_all([teaching_class, admission_case])
    db.flush()
    enrollment = Enrollment(
        id=uuid4(),
        admission_case_id=admission_case.id,
        student_person_id=student_id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=100_000,
        status=status,
        confirmed_at=NOW - timedelta(days=10),
        cancelled_at=NOW - timedelta(days=1) if status == "cancelled" else None,
        created_by_user_id=actor_id,
    )
    db.add(enrollment)
    db.flush()
    return enrollment


@dataclass(frozen=True)
class _EnrollmentRaceScenario:
    actor_id: UUID
    actor_person_id: UUID
    resident_id: UUID
    room_id: UUID
    floor_id: UUID
    building_id: UUID
    cohort_id: UUID
    stay_id: UUID
    enrollment_id: UUID


def _setup_enrollment_race(
    engine: Engine,
    *,
    initial_status: str,
) -> _EnrollmentRaceScenario:
    with Session(engine) as setup, setup.begin():
        actor_person, actor = _person_account(
            setup,
            name=f"报名并发协调器-{initial_status}",
        )
        resident, _ = _person_account(
            setup,
            name=f"报名并发住户-{initial_status}",
        )
        room = _room(setup)
        cohort = _cohort(
            setup,
            actor_id=actor.id,
            name=f"报名并发营期-{initial_status}",
            ends_at=NOW + timedelta(days=5),
        )
        stay = _stay(
            setup,
            person_id=resident.id,
            room=room,
            actor_id=actor.id,
            duration_mode="fixed_term",
            planned_check_out_at=NOW + timedelta(days=5),
            stay_purpose="camp",
            cohort_id=cohort.id,
        )
        enrollment = _enrollment(
            setup,
            actor_id=actor.id,
            student_id=resident.id,
            cohort=cohort,
            status=initial_status,
        )
        return _EnrollmentRaceScenario(
            actor_id=actor.id,
            actor_person_id=actor_person.id,
            resident_id=resident.id,
            room_id=room.id,
            floor_id=room.floor_id,
            building_id=room.building_id,
            cohort_id=cohort.id,
            stay_id=stay.id,
            enrollment_id=enrollment.id,
        )


def _cleanup_enrollment_race(
    engine: Engine,
    *,
    scenario: _EnrollmentRaceScenario,
) -> None:
    person_ids = (scenario.actor_person_id, scenario.resident_id)
    with Session(engine) as cleanup, cleanup.begin():
        enrollment_ids = select(Enrollment.id).where(
            Enrollment.cohort_id == scenario.cohort_id
        )
        cleanup.execute(
            delete(Enrollment).where(Enrollment.id.in_(enrollment_ids))
        )
        cleanup.execute(
            delete(AdmissionCase).where(
                AdmissionCase.student_person_id == scenario.resident_id
            )
        )
        cleanup.execute(
            delete(TeachingClass).where(
                TeachingClass.cohort_id == scenario.cohort_id
            )
        )
        cleanup.execute(delete(HousingStay).where(HousingStay.id == scenario.stay_id))
        cleanup.execute(delete(HousingRoom).where(HousingRoom.id == scenario.room_id))
        cleanup.execute(delete(HousingFloor).where(HousingFloor.id == scenario.floor_id))
        cleanup.execute(
            delete(HousingBuilding).where(HousingBuilding.id == scenario.building_id)
        )
        cleanup.execute(delete(Cohort).where(Cohort.id == scenario.cohort_id))
        cleanup.execute(delete(UserAccount).where(UserAccount.person_id.in_(person_ids)))
        cleanup.execute(delete(StaffProfile).where(StaffProfile.person_id.in_(person_ids)))
        cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))


def _mutate_enrollment_with_cohort_lock(
    engine: Engine,
    *,
    scenario: _EnrollmentRaceScenario,
    mutation: str,
    locked: threading.Event,
    release: threading.Event,
    backend_pids: Queue[int],
    results: list[bool],
    errors: list[BaseException],
) -> None:
    try:
        with Session(engine) as writer:
            with writer.begin():
                backend_pid = int(writer.scalar(text("SELECT pg_backend_pid()")) or 0)
                assert backend_pid > 0
                backend_pids.put(backend_pid)
                cohort = writer.scalar(
                    select(Cohort)
                    .where(Cohort.id == scenario.cohort_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                assert cohort is not None
                if mutation == "cancel":
                    enrollment = writer.get(Enrollment, scenario.enrollment_id)
                    assert enrollment is not None
                    enrollment.status = "cancelled"
                    enrollment.cancelled_at = NOW
                    writer.flush()
                else:
                    _enrollment(
                        writer,
                        actor_id=scenario.actor_id,
                        student_id=scenario.resident_id,
                        cohort=cohort,
                        status="active",
                    )
                locked.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("enrollment race release was not signalled")
            results.append(writer.scalar(select(literal(1))) == 1)
    except BaseException as exc:  # noqa: BLE001 - surfaced in the parent test
        errors.append(exc)
        locked.set()


def _run_reconciliation_worker(
    engine: Engine,
    *,
    started: threading.Event,
    done: threading.Event,
    backend_pids: Queue[int],
    counts: list[int],
    reusable: list[bool],
    errors: list[BaseException],
) -> None:
    try:
        with Session(engine) as worker:
            backend_pid = int(worker.scalar(text("SELECT pg_backend_pid()")) or 0)
            assert backend_pid > 0
            backend_pids.put(backend_pid)
            worker.execute(text("SET LOCAL statement_timeout = '8s'"))
            started.set()
            counts.append(HousingCheckoutReconciler(worker).run(NOW))
            reusable.append(worker.scalar(select(literal(1))) == 1)
    except BaseException as exc:  # noqa: BLE001 - surfaced in the parent test
        errors.append(exc)
    finally:
        done.set()


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


@dataclass(frozen=True)
class _LifecycleRaceScenario:
    actor_id: UUID
    actor_person_id: UUID
    resident_id: UUID
    source_room_id: UUID
    target_room_id: UUID | None
    room_ids: tuple[UUID, ...]
    floor_ids: tuple[UUID, ...]
    building_ids: tuple[UUID, ...]
    stay_id: UUID


def _setup_lifecycle_race(
    engine: Engine,
    *,
    action: str,
) -> _LifecycleRaceScenario:
    with Session(engine) as setup, setup.begin():
        actor_person, actor = _person_account(
            setup,
            name=f"住宿并发操作人-{action}",
        )
        resident, _ = _person_account(
            setup,
            name=f"住宿并发住户-{action}",
        )
        source_room = _room(setup)
        target_room = _room(setup) if action == "transfer" else None
        planned_check_out_at = (
            NOW + timedelta(days=1) if action == "transfer" else NOW
        )
        stay = _stay(
            setup,
            person_id=resident.id,
            room=source_room,
            actor_id=actor.id,
            duration_mode="fixed_term",
            planned_check_out_at=planned_check_out_at,
        )
        rooms = [source_room, *([target_room] if target_room is not None else [])]
        return _LifecycleRaceScenario(
            actor_id=actor.id,
            actor_person_id=actor_person.id,
            resident_id=resident.id,
            source_room_id=source_room.id,
            target_room_id=target_room.id if target_room is not None else None,
            room_ids=tuple(room.id for room in rooms),
            floor_ids=tuple(room.floor_id for room in rooms),
            building_ids=tuple(room.building_id for room in rooms),
            stay_id=stay.id,
        )


def _cleanup_lifecycle_race(
    engine: Engine,
    *,
    scenario: _LifecycleRaceScenario,
) -> None:
    person_ids = (scenario.actor_person_id, scenario.resident_id)
    with Session(engine) as cleanup, cleanup.begin():
        cleanup.execute(
            delete(HousingTransfer).where(
                HousingTransfer.person_id == scenario.resident_id
            )
        )
        cleanup.execute(
            delete(HousingStay).where(HousingStay.person_id == scenario.resident_id)
        )
        cleanup.execute(delete(HousingRoom).where(HousingRoom.id.in_(scenario.room_ids)))
        cleanup.execute(
            delete(HousingFloor).where(HousingFloor.id.in_(scenario.floor_ids))
        )
        cleanup.execute(
            delete(HousingBuilding).where(
                HousingBuilding.id.in_(scenario.building_ids)
            )
        )
        cleanup.execute(delete(UserAccount).where(UserAccount.person_id.in_(person_ids)))
        cleanup.execute(delete(StaffProfile).where(StaffProfile.person_id.in_(person_ids)))
        cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))


def _request_checkout_while_holding_stay(
    engine: Engine,
    *,
    scenario: _LifecycleRaceScenario,
    stay_locked: threading.Event,
    release: threading.Event,
    reusable: list[bool],
    errors: list[BaseException],
) -> None:
    try:
        with Session(engine) as worker:
            connection = worker.connection()
            paused = False

            def pause_after_stay_lock(
                _connection,
                _cursor,
                statement,
                _parameters,
                _context,
                _executemany,
            ) -> None:
                nonlocal paused
                normalized = " ".join(statement.lower().split())
                if (
                    not paused
                    and " from housing_stays" in normalized
                    and " for update" in normalized
                ):
                    paused = True
                    stay_locked.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError("checkout race release was not signalled")

            event.listen(connection, "after_cursor_execute", pause_after_stay_lock)
            try:
                HousingOccupancyService(worker).request_checkout(
                    scenario.stay_id,
                    CheckoutRequest(
                        version=1,
                        actual_check_out_at=NOW - timedelta(hours=1),
                    ),
                    actor_user_id=scenario.actor_id,
                    occurred_at=NOW,
                    authorize_room=lambda _room: None,
                )
                reusable.append(worker.scalar(select(literal(1))) == 1)
            finally:
                event.remove(connection, "after_cursor_execute", pause_after_stay_lock)
    except BaseException as exc:  # noqa: BLE001 - surfaced in the parent test
        errors.append(exc)
        stay_locked.set()


def _pause_reconciler_after_stay_lock(
    engine: Engine,
    *,
    now: datetime,
    stay_locked: threading.Event,
    release: threading.Event,
    backend_pids: Queue[int],
    counts: list[int],
    reusable: list[bool],
    errors: list[BaseException],
) -> None:
    try:
        with Session(engine) as worker:
            connection = worker.connection()
            backend_pid = int(worker.scalar(text("SELECT pg_backend_pid()")) or 0)
            assert backend_pid > 0
            backend_pids.put(backend_pid)

            def pause_after_candidate_lock(
                _connection,
                _cursor,
                statement,
                _parameters,
                _context,
                _executemany,
            ) -> None:
                normalized = " ".join(statement.lower().split())
                if (
                    " from housing_stays" in normalized
                    and " for update" in normalized
                    and "skip locked" in normalized
                ):
                    stay_locked.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError("reconciler race release was not signalled")

            event.listen(connection, "after_cursor_execute", pause_after_candidate_lock)
            try:
                counts.append(HousingCheckoutReconciler(worker).run(now))
                reusable.append(worker.scalar(select(literal(1))) == 1)
            finally:
                event.remove(connection, "after_cursor_execute", pause_after_candidate_lock)
    except BaseException as exc:  # noqa: BLE001 - surfaced in the parent test
        errors.append(exc)
        stay_locked.set()


def _transfer_after_reconciler_lock(
    engine: Engine,
    *,
    scenario: _LifecycleRaceScenario,
    client_version: int,
    stay_lock_attempted: threading.Event,
    backend_pids: Queue[int],
    reusable: list[bool],
    stale_codes: list[str],
    source_states_after_stale: list[tuple[str, int, datetime | None]],
    partial_write_counts_after_stale: list[tuple[int, int]],
    refreshed_versions: list[int],
    errors: list[BaseException],
) -> None:
    try:
        with Session(engine) as worker:
            connection = worker.connection()
            backend_pid = int(worker.scalar(text("SELECT pg_backend_pid()")) or 0)
            assert backend_pid > 0
            backend_pids.put(backend_pid)

            def observe_stay_lock_attempt(
                _connection,
                _cursor,
                statement,
                _parameters,
                _context,
                _executemany,
            ) -> None:
                normalized = " ".join(statement.lower().split())
                if " from housing_stays" in normalized and " for update" in normalized:
                    stay_lock_attempted.set()

            event.listen(connection, "before_cursor_execute", observe_stay_lock_attempt)
            try:
                assert scenario.target_room_id is not None
                service = HousingOccupancyService(worker)
                try:
                    service.transfer(
                        scenario.stay_id,
                        TransferStayRequest(
                            version=client_version,
                            target_room_id=scenario.target_room_id,
                            transferred_at=NOW - timedelta(minutes=30),
                            reason="协调器并发后的过期版本调房",
                        ),
                        actor_user_id=scenario.actor_id,
                        occurred_at=NOW,
                        authorize_source=lambda _room: None,
                        authorize_target=lambda _room: None,
                    )
                except HousingStaleVersion as exc:
                    stale_codes.append(exc.code)
                else:
                    raise AssertionError(
                        "transfer must reject the version held before reconciliation"
                    )
                reusable.append(worker.scalar(select(literal(1))) == 1)
                worker.expire_all()
                source = worker.get(HousingStay, scenario.stay_id)
                assert source is not None
                source_states_after_stale.append(
                    (source.status, source.version, source.actual_check_out_at)
                )
                partial_write_counts_after_stale.append(
                    (
                        int(
                            worker.scalar(
                                select(func.count())
                                .select_from(HousingStay)
                                .where(HousingStay.person_id == scenario.resident_id)
                            )
                            or 0
                        ),
                        int(
                            worker.scalar(
                                select(func.count())
                                .select_from(HousingTransfer)
                                .where(HousingTransfer.person_id == scenario.resident_id)
                            )
                            or 0
                        ),
                    )
                )
                refreshed_versions.append(source.version)
                service.transfer(
                    scenario.stay_id,
                    TransferStayRequest(
                        version=source.version,
                        target_room_id=scenario.target_room_id,
                        transferred_at=NOW - timedelta(minutes=30),
                        reason="刷新版本后的系统待退房调房",
                    ),
                    actor_user_id=scenario.actor_id,
                    occurred_at=NOW,
                    authorize_source=lambda _room: None,
                    authorize_target=lambda _room: None,
                )
                reusable.append(worker.scalar(select(literal(1))) == 1)
            finally:
                event.remove(
                    connection,
                    "before_cursor_execute",
                    observe_stay_lock_attempt,
                )
    except BaseException as exc:  # noqa: BLE001 - surfaced in the parent test
        errors.append(exc)
        stay_lock_attempted.set()


def test_reconciler_marks_only_due_checked_in_stays_without_releasing_rooms(
    db: Session,
) -> None:
    _, actor = _person_account(db, name="协调器操作账号")
    room = _room(db)
    due_fixed_person, _ = _person_account(db, name="固定期限到期住户")
    ended_camp_person, _ = _person_account(db, name="营期结束住户")
    cancelled_person, _ = _person_account(db, name="报名取消住户")
    reenrolled_person, _ = _person_account(db, name="取消后重新报名住户")
    unaffected_people = [
        _person_account(db, name=f"不应变化住户-{index}")[0]
        for index in range(5)
    ]
    ended_cohort = _cohort(
        db,
        actor_id=actor.id,
        name="已结束营期",
        ends_at=NOW - timedelta(hours=1),
        status="completed",
    )
    future_cohort = _cohort(
        db,
        actor_id=actor.id,
        name="未结束营期",
        ends_at=NOW + timedelta(days=5),
    )

    due_fixed = _stay(
        db,
        person_id=due_fixed_person.id,
        room=room,
        actor_id=actor.id,
        duration_mode="fixed_term",
        planned_check_out_at=NOW,
    )
    ended_camp = _stay(
        db,
        person_id=ended_camp_person.id,
        room=room,
        actor_id=actor.id,
        duration_mode="fixed_term",
        planned_check_out_at=NOW + timedelta(days=5),
        stay_purpose="camp",
        cohort_id=ended_cohort.id,
    )
    cancelled_camp = _stay(
        db,
        person_id=cancelled_person.id,
        room=room,
        actor_id=actor.id,
        duration_mode="fixed_term",
        planned_check_out_at=NOW + timedelta(days=5),
        stay_purpose="camp",
        cohort_id=future_cohort.id,
    )
    _enrollment(
        db,
        actor_id=actor.id,
        student_id=cancelled_person.id,
        cohort=future_cohort,
        status="cancelled",
    )
    reenrolled_camp = _stay(
        db,
        person_id=reenrolled_person.id,
        room=room,
        actor_id=actor.id,
        duration_mode="fixed_term",
        planned_check_out_at=NOW + timedelta(days=5),
        stay_purpose="camp",
        cohort_id=future_cohort.id,
    )
    _enrollment(
        db,
        actor_id=actor.id,
        student_id=reenrolled_person.id,
        cohort=future_cohort,
        status="cancelled",
    )
    _enrollment(
        db,
        actor_id=actor.id,
        student_id=reenrolled_person.id,
        cohort=future_cohort,
        status="active",
    )

    long_term = _stay(
        db,
        person_id=unaffected_people[0].id,
        room=room,
        actor_id=actor.id,
    )
    planned_due = _stay(
        db,
        person_id=unaffected_people[1].id,
        room=room,
        actor_id=actor.id,
        status="planned",
        duration_mode="fixed_term",
        planned_check_out_at=NOW,
    )
    already_pending = _stay(
        db,
        person_id=unaffected_people[2].id,
        room=room,
        actor_id=actor.id,
        status="checkout_pending",
    )
    checked_out = _stay(
        db,
        person_id=unaffected_people[3].id,
        room=room,
        actor_id=actor.id,
        status="checked_out",
    )
    cancelled = _stay(
        db,
        person_id=unaffected_people[4].id,
        room=room,
        actor_id=actor.id,
        status="cancelled",
        duration_mode="fixed_term",
        planned_check_out_at=NOW,
    )
    db.commit()

    changed = HousingCheckoutReconciler(db).run(NOW)

    assert changed == 3
    due_ids = {due_fixed.id, ended_camp.id, cancelled_camp.id}
    for stay_id in due_ids:
        stay = db.get(HousingStay, stay_id)
        assert stay is not None
        assert stay.status == "checkout_pending"
        assert stay.version == 2
        assert stay.actual_check_out_at is None
        assert stay.checked_out_by_user_id is None
        assert (
            stay.checkout_requested_actual_at,
            stay.checkout_requested_at,
            stay.checkout_requested_by_user_id,
        ) == (None, None, None)

    for stay_id, expected_status in (
        (reenrolled_camp.id, "checked_in"),
        (long_term.id, "checked_in"),
        (planned_due.id, "planned"),
        (already_pending.id, "checkout_pending"),
        (checked_out.id, "checked_out"),
        (cancelled.id, "cancelled"),
    ):
        stay = db.get(HousingStay, stay_id)
        assert stay is not None
        assert (stay.status, stay.version) == (expected_status, 1)

    assert HousingCheckoutReconciler(db).run(NOW) == 0


def test_reconciler_requires_an_aware_now(db: Session) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        HousingCheckoutReconciler(db).run(NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    ("initial_status", "mutation", "expected_count", "expected_status"),
    [
        ("active", "cancel", 1, "checkout_pending"),
        ("cancelled", "reactivate", 0, "checked_in"),
    ],
)
def test_reconciler_serializes_with_cancelled_and_reactivated_enrollment_writes(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
    initial_status: str,
    mutation: str,
    expected_count: int,
    expected_status: str,
) -> None:
    scenario = _setup_enrollment_race(
        postgres_engine,
        initial_status=initial_status,
    )
    request.addfinalizer(
        partial(
            _cleanup_enrollment_race,
            postgres_engine,
            scenario=scenario,
        )
    )
    enrollment_locked = threading.Event()
    release_enrollment = threading.Event()
    reconciliation_started = threading.Event()
    reconciliation_done = threading.Event()
    writer_reusable: list[bool] = []
    writer_backend_pids: Queue[int] = Queue()
    reconciliation_counts: list[int] = []
    reconciliation_reusable: list[bool] = []
    reconciliation_backend_pids: Queue[int] = Queue()
    errors: list[BaseException] = []

    writer = threading.Thread(
        target=_mutate_enrollment_with_cohort_lock,
        kwargs={
            "engine": postgres_engine,
            "scenario": scenario,
            "mutation": mutation,
            "locked": enrollment_locked,
            "release": release_enrollment,
            "backend_pids": writer_backend_pids,
            "results": writer_reusable,
            "errors": errors,
        },
        name=f"housing-enrollment-{mutation}",
    )
    reconciler = threading.Thread(
        target=_run_reconciliation_worker,
        kwargs={
            "engine": postgres_engine,
            "started": reconciliation_started,
            "done": reconciliation_done,
            "backend_pids": reconciliation_backend_pids,
            "counts": reconciliation_counts,
            "reusable": reconciliation_reusable,
            "errors": errors,
        },
        name=f"housing-reconciler-{mutation}",
    )
    writer.start()
    assert enrollment_locked.wait(timeout=5)
    writer_backend_pid = writer_backend_pids.get(timeout=5)
    reconciler.start()
    assert reconciliation_started.wait(timeout=5)
    try:
        reconciliation_backend_pid = reconciliation_backend_pids.get(timeout=5)
        _wait_for_postgres_blocker(
            postgres_engine,
            blocked_pid=reconciliation_backend_pid,
            blocker_pid=writer_backend_pid,
            description="reconciler cohort lock",
        )
        reconciliation_waited_for_cohort = not reconciliation_done.is_set()
    finally:
        release_enrollment.set()
        writer.join(timeout=10)
        reconciler.join(timeout=10)

    assert not writer.is_alive()
    assert not reconciler.is_alive()
    assert errors == []
    assert reconciliation_counts == [expected_count]
    assert reconciliation_waited_for_cohort is True
    assert writer_reusable == [True]
    assert reconciliation_reusable == [True]
    with Session(postgres_engine) as verification:
        stay = verification.get(HousingStay, scenario.stay_id)
        assert stay is not None
        assert (stay.status, stay.version) == (
            expected_status,
            2 if expected_status == "checkout_pending" else 1,
        )
        assert stay.actual_check_out_at is None
        assert (
            stay.checkout_requested_actual_at,
            stay.checkout_requested_at,
            stay.checkout_requested_by_user_id,
        ) == (None, None, None)


def test_manual_checkout_request_and_reconciler_keep_one_complete_pending_state(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    scenario = _setup_lifecycle_race(postgres_engine, action="checkout")
    request.addfinalizer(
        partial(
            _cleanup_lifecycle_race,
            postgres_engine,
            scenario=scenario,
        )
    )
    stay_locked = threading.Event()
    release_checkout = threading.Event()
    checkout_reusable: list[bool] = []
    errors: list[BaseException] = []
    checkout = threading.Thread(
        target=_request_checkout_while_holding_stay,
        kwargs={
            "engine": postgres_engine,
            "scenario": scenario,
            "stay_locked": stay_locked,
            "release": release_checkout,
            "reusable": checkout_reusable,
            "errors": errors,
        },
        name="housing-manual-checkout-race",
    )
    checkout.start()
    assert stay_locked.wait(timeout=5)
    try:
        with Session(postgres_engine) as reconciliation_session:
            assert HousingCheckoutReconciler(reconciliation_session).run(NOW) == 0
            assert reconciliation_session.scalar(select(literal(1))) == 1
    finally:
        release_checkout.set()
        checkout.join(timeout=10)

    assert not checkout.is_alive()
    assert errors == []
    assert checkout_reusable == [True]
    with Session(postgres_engine) as verification:
        stay = verification.get(HousingStay, scenario.stay_id)
        assert stay is not None
        assert (stay.status, stay.version, stay.actual_check_out_at) == (
            "checkout_pending",
            2,
            None,
        )
        assert stay.checkout_requested_actual_at == NOW - timedelta(hours=1)
        assert stay.checkout_requested_at == NOW
        assert stay.checkout_requested_by_user_id == scenario.actor_id


def test_reconciler_and_transfer_preserve_system_pending_without_deadlock(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    scenario = _setup_lifecycle_race(postgres_engine, action="transfer")
    request.addfinalizer(
        partial(
            _cleanup_lifecycle_race,
            postgres_engine,
            scenario=scenario,
        )
    )
    reconciler_locked_stay = threading.Event()
    release_reconciler = threading.Event()
    transfer_attempted_stay_lock = threading.Event()
    reconciliation_counts: list[int] = []
    reconciliation_reusable: list[bool] = []
    reconciliation_backend_pids: Queue[int] = Queue()
    transfer_reusable: list[bool] = []
    transfer_backend_pids: Queue[int] = Queue()
    transfer_stale_codes: list[str] = []
    source_states_after_stale: list[tuple[str, int, datetime | None]] = []
    partial_write_counts_after_stale: list[tuple[int, int]] = []
    refreshed_versions: list[int] = []
    errors: list[BaseException] = []
    reconciler = threading.Thread(
        target=_pause_reconciler_after_stay_lock,
        kwargs={
            "engine": postgres_engine,
            "now": NOW + timedelta(days=2),
            "stay_locked": reconciler_locked_stay,
            "release": release_reconciler,
            "backend_pids": reconciliation_backend_pids,
            "counts": reconciliation_counts,
            "reusable": reconciliation_reusable,
            "errors": errors,
        },
        name="housing-paused-reconciler-transfer-race",
    )
    transfer = threading.Thread(
        target=_transfer_after_reconciler_lock,
        kwargs={
            "engine": postgres_engine,
            "scenario": scenario,
            "client_version": 1,
            "stay_lock_attempted": transfer_attempted_stay_lock,
            "backend_pids": transfer_backend_pids,
            "reusable": transfer_reusable,
            "stale_codes": transfer_stale_codes,
            "source_states_after_stale": source_states_after_stale,
            "partial_write_counts_after_stale": partial_write_counts_after_stale,
            "refreshed_versions": refreshed_versions,
            "errors": errors,
        },
        name="housing-transfer-after-reconciler-race",
    )
    reconciler.start()
    assert reconciler_locked_stay.wait(timeout=5)
    reconciliation_backend_pid = reconciliation_backend_pids.get(timeout=5)
    transfer.start()
    assert transfer_attempted_stay_lock.wait(timeout=5)
    try:
        transfer_backend_pid = transfer_backend_pids.get(timeout=5)
        _wait_for_postgres_blocker(
            postgres_engine,
            blocked_pid=transfer_backend_pid,
            blocker_pid=reconciliation_backend_pid,
            description="transfer stay lock after reconciler SKIP LOCKED selection",
        )
    finally:
        release_reconciler.set()
        reconciler.join(timeout=10)
        transfer.join(timeout=10)

    assert not reconciler.is_alive()
    assert not transfer.is_alive()
    assert errors == []
    assert reconciliation_counts == [1]
    assert reconciliation_reusable == [True]
    assert transfer_stale_codes == ["housing_stale_version"]
    assert source_states_after_stale == [("checkout_pending", 2, None)]
    assert partial_write_counts_after_stale == [(1, 0)]
    assert refreshed_versions == [2]
    assert transfer_reusable == [True, True]
    with Session(postgres_engine) as verification:
        stays = list(
            verification.scalars(
                select(HousingStay)
                .where(HousingStay.person_id == scenario.resident_id)
                .order_by(HousingStay.created_at, HousingStay.id)
            )
        )
        transfers = list(
            verification.scalars(
                select(HousingTransfer).where(
                    HousingTransfer.person_id == scenario.resident_id
                )
            )
        )
        assert len(stays) == 2
        source = next(stay for stay in stays if stay.id == scenario.stay_id)
        destination = next(stay for stay in stays if stay.id != scenario.stay_id)
        assert (source.status, source.version) == ("checked_out", 3)
        assert source.actual_check_out_at == NOW - timedelta(minutes=30)
        assert destination.room_id == scenario.target_room_id
        assert (destination.status, destination.version) == (
            "checkout_pending",
            1,
        )
        assert destination.actual_check_out_at is None
        assert (
            destination.checkout_requested_actual_at,
            destination.checkout_requested_at,
            destination.checkout_requested_by_user_id,
        ) == (None, None, None)
        assert len(transfers) == 1
        assert transfers[0].from_stay_id == source.id
        assert transfers[0].to_stay_id == destination.id
        assert verification.scalar(
            select(func.count()).select_from(HousingTransfer).where(
                HousingTransfer.from_stay_id == source.id
            )
        ) == 1
