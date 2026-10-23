from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, literal, or_, select
from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount
from app.housing.errors import (
    HousingCapacityConflict,
    HousingGenderConflict,
    HousingPersonTimeConflict,
    HousingServiceError,
)
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.resident_readiness import HousingResidentReadinessPolicy
from app.housing.schemas import StayCreate
from app.identity.models import Person, StaffProfile
from app.personnel_intake.schemas import IntakeInput
from app.security.encryption import EnvelopeCipher, LookupDigest
from tests.api.conftest import make_cn_id
from tests.db.test_personnel_approval_concurrency import approval_world as _approval_world
from tests.db.test_team_concurrency import committed_world as _committed_world
from tests.db.test_team_concurrency import reload_actor
from tests.services.test_personnel_approval import approvals

NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)


@pytest.fixture
def committed_world(postgres_engine):
    yield from _committed_world.__wrapped__(postgres_engine)


@pytest.fixture
def approval_world(postgres_engine, committed_world):
    yield from _approval_world.__wrapped__(postgres_engine, committed_world)


@pytest.mark.parametrize("conflict", ["capacity", "gender", "person"])
def test_accountless_housing_races_preserve_single_winner(
    postgres_engine, approval_world, request, conflict
):
    ids, teams, _ = approval_world
    at = datetime(2026, 10, 18, 12, tzinfo=UTC)
    with Session(postgres_engine, expire_on_commit=False) as setup:
        rooms = _rooms(setup, 2, capacity=1 if conflict == "capacity" else 2)
        setup.commit()
        person_ids = []
        for index in range(1 if conflict == "person" else 2):
            sequence = 951 + index * 2 if conflict != "gender" else 951 + index
            data = IntakeInput(
                kind="player",
                full_name=f"并发无账号{index}",
                identity_number=make_cn_id("20000102", sequence),
                date_of_birth=date(2000, 1, 2),
                sex="female" if sequence % 2 == 0 else "male",
                phone=f"13900000{sequence}",
                email=f"housing-race-{sequence}@example.com",
                team_id=teams[0],
            )
            person_ids.append(
                approvals(setup)
                .direct_create(reload_actor(setup, ids["admin2"]), data, uuid4())
                .person_id
            )
        room_ids = tuple(room.id for room in rooms)
    request.addfinalizer(
        partial(_cleanup_committed_race_rows, postgres_engine, room_ids=room_ids, person_ids=())
    )
    barrier = Barrier(2)

    def worker(index):
        with Session(postgres_engine, autoflush=False) as db:
            room_id = room_ids[index] if conflict == "person" else room_ids[0]
            person_id = person_ids[0] if conflict == "person" else person_ids[index]
            observed = db.get(HousingRoom, room_id).version
            barrier.wait(timeout=10)
            try:
                HousingOccupancyService(
                    db,
                    resident_readiness=HousingResidentReadinessPolicy(
                        db,
                        cipher=EnvelopeCipher(bytes(range(32))),
                        lookup=LookupDigest(bytes(range(32))),
                    ),
                ).create_stay(
                    room_id,
                    StayCreate(
                        person_id=person_id,
                        stay_purpose="permanent",
                        duration_mode="long_term",
                        planned_check_in_at=at,
                    ),
                    actor_user_id=ids["admin2"][0],
                    occurred_at=at,
                    authorize_room=lambda room: None,
                )
                outcome = "committed"
            except HousingServiceError as exc:
                outcome = exc.code
            return _WorkerResult(outcome, db.scalar(select(literal(1))) == 1, observed)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, (0, 1)))
    _assert_one_commit(
        results,
        loser_code={
            "capacity": HousingCapacityConflict.code,
            "gender": HousingGenderConflict.code,
            "person": HousingPersonTimeConflict.code,
        }[conflict],
    )


@dataclass(frozen=True)
class _WorkerResult:
    outcome: str
    session_reusable: bool
    observed_room_version: int


def _person_and_account(
    db: Session,
    *,
    name: str,
    sex: str,
) -> tuple[UUID, UUID]:
    person_id = uuid4()
    account_id = uuid4()
    db.add(
        Person(
            id=person_id,
            full_name=name,
            identity_ciphertext=f"concurrency-cipher-{person_id.hex}",
            identity_digest=f"concurrency-digest-{person_id.hex}",
            phone_ciphertext=f"concurrency-phone-cipher-{person_id.hex}",
            phone_digest=f"concurrency-phone-digest-{person_id.hex}",
            email=None,
            email_ciphertext=f"concurrency-email-cipher-{person_id.hex}",
            email_digest=f"concurrency-email-digest-{person_id.hex}",
            sex=sex,
            date_of_birth=date(1990, 1, 1),
        )
    )
    db.flush()
    db.add(StaffProfile(person_id=person_id, staff_type="life_teacher"))
    db.add(
        UserAccount(
            id=account_id,
            person_id=person_id,
            login_phone_digest=f"concurrency-login-{account_id.hex}",
            email_normalized=f"{account_id.hex}@example.test",
            password_hash="concurrency-password-hash",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
    )
    db.flush()
    db.add(
        AccountLoginIdentifier(
            account_id=account_id,
            kind="email",
            identifier_digest=uuid4().hex + uuid4().hex,
            verified_at=NOW,
            verification_source="test_verified",
            is_primary=True,
        )
    )
    db.flush()
    return person_id, account_id


def _rooms(db: Session, count: int, *, capacity: int) -> list[HousingRoom]:
    suffix = uuid4().hex[:10].upper()
    building = HousingBuilding(
        id=uuid4(),
        code=f"C{suffix[:8]}",
        name=f"并发测试楼{suffix}",
        display_order=0,
        is_active=True,
    )
    floor = HousingFloor(
        id=uuid4(),
        building_id=building.id,
        number=2,
        name=f"并发二楼{suffix}",
        display_order=0,
        is_active=True,
    )
    rooms = [
        HousingRoom(
            id=uuid4(),
            building_id=building.id,
            floor_id=floor.id,
            room_number=f"{suffix}-{index + 1}",
            normalized_key=f"{suffix}-{index + 1}",
            room_type="custom",
            capacity=capacity,
            verification_status="verified",
            service_status="available",
            version=1,
        )
        for index in range(count)
    ]
    db.add_all([building, floor, *rooms])
    db.flush()
    return rooms


def _stay_input(person_id: UUID) -> StayCreate:
    return StayCreate(
        person_id=person_id,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=NOW - timedelta(minutes=1),
    )


def _race_worker(
    engine: Engine,
    barrier: Barrier,
    *,
    room_id: UUID,
    person_id: UUID,
    actor_id: UUID,
) -> _WorkerResult:
    with Session(engine, autoflush=False) as worker:
        observed = worker.get(HousingRoom, room_id)
        assert observed is not None
        observed_version = observed.version
        barrier.wait(timeout=10)
        try:
            HousingOccupancyService(worker).create_stay(
                room_id,
                _stay_input(person_id),
                actor_user_id=actor_id,
                occurred_at=NOW,
                authorize_room=lambda room: None,
            )
        except HousingServiceError as exc:
            outcome = exc.code
        else:
            outcome = "committed"
        session_reusable = worker.scalar(select(literal(1))) == 1
        return _WorkerResult(outcome, session_reusable, observed_version)


def _assert_one_commit(
    results: list[_WorkerResult],
    *,
    loser_code: str,
) -> None:
    assert [result.outcome for result in results].count("committed") == 1
    assert [result.outcome for result in results].count(loser_code) == 1
    assert all(result.session_reusable for result in results)
    assert {result.observed_room_version for result in results} == {1}


def _cleanup_committed_race_rows(
    engine: Engine,
    *,
    room_ids: tuple[UUID, ...],
    person_ids: tuple[UUID, ...],
) -> None:
    with Session(engine) as cleanup, cleanup.begin():
        room_rows = list(
            cleanup.execute(
                select(HousingRoom.floor_id, HousingRoom.building_id).where(
                    HousingRoom.id.in_(room_ids)
                )
            ).tuples()
        )
        floor_ids = tuple(sorted({floor_id for floor_id, _ in room_rows}))
        building_ids = tuple(sorted({building_id for _, building_id in room_rows}))
        cleanup.execute(
            delete(HousingStay).where(
                or_(
                    HousingStay.room_id.in_(room_ids),
                    HousingStay.person_id.in_(person_ids),
                )
            )
        )
        cleanup.execute(delete(StaffProfile).where(StaffProfile.person_id.in_(person_ids)))
        account_ids = select(UserAccount.id).where(UserAccount.person_id.in_(person_ids))
        cleanup.execute(
            delete(AccountLoginIdentifier).where(AccountLoginIdentifier.account_id.in_(account_ids))
        )
        cleanup.execute(delete(UserAccount).where(UserAccount.person_id.in_(person_ids)))
        cleanup.execute(delete(HousingRoom).where(HousingRoom.id.in_(room_ids)))
        if floor_ids:
            cleanup.execute(delete(HousingFloor).where(HousingFloor.id.in_(floor_ids)))
        if building_ids:
            cleanup.execute(delete(HousingBuilding).where(HousingBuilding.id.in_(building_ids)))
        cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))


def test_same_room_final_capacity_races_serialize_on_room_lock(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    with Session(postgres_engine) as setup, setup.begin():
        room = _rooms(setup, 1, capacity=2)[0]
        existing_id, actor_id = _person_and_account(
            setup,
            name="并发既有男住户",
            sex="male",
        )
        first_id, _ = _person_and_account(
            setup,
            name="并发候选住户甲",
            sex="male",
        )
        second_id, _ = _person_and_account(
            setup,
            name="并发候选住户乙",
            sex="male",
        )
        setup.add(
            HousingStay(
                person_id=existing_id,
                room_id=room.id,
                stay_purpose="permanent",
                duration_mode="long_term",
                planned_check_in_at=NOW - timedelta(days=1),
                actual_check_in_at=NOW - timedelta(days=1),
                status="checked_in",
                room_name_snapshot=room.room_number,
                floor_name_snapshot="并发二楼",
                building_name_snapshot="并发测试楼",
                created_by_user_id=actor_id,
                checked_in_by_user_id=actor_id,
                version=1,
            )
        )
        room_id = room.id
        person_ids = (existing_id, first_id, second_id)

    request.addfinalizer(
        partial(
            _cleanup_committed_race_rows,
            postgres_engine,
            room_ids=(room_id,),
            person_ids=person_ids,
        )
    )

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda person_id: _race_worker(
                    postgres_engine,
                    barrier,
                    room_id=room_id,
                    person_id=person_id,
                    actor_id=actor_id,
                ),
                (first_id, second_id),
            )
        )

    _assert_one_commit(results, loser_code=HousingCapacityConflict.code)
    with Session(postgres_engine) as verification:
        assert (
            verification.scalar(
                select(func.count(HousingStay.id)).where(
                    HousingStay.room_id == room_id,
                    HousingStay.status.in_(("planned", "checked_in", "checkout_pending")),
                )
            )
            == 2
        )


def test_same_room_opposite_sex_race_starts_with_both_candidates_eligible(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    with Session(postgres_engine) as setup, setup.begin():
        room = _rooms(setup, 1, capacity=2)[0]
        male_id, actor_id = _person_and_account(
            setup,
            name="空房并发男住户",
            sex="male",
        )
        female_id, _ = _person_and_account(
            setup,
            name="空房并发女住户",
            sex="female",
        )
        room_id = room.id

    request.addfinalizer(
        partial(
            _cleanup_committed_race_rows,
            postgres_engine,
            room_ids=(room_id,),
            person_ids=(male_id, female_id),
        )
    )

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda person_id: _race_worker(
                    postgres_engine,
                    barrier,
                    room_id=room_id,
                    person_id=person_id,
                    actor_id=actor_id,
                ),
                (male_id, female_id),
            )
        )

    _assert_one_commit(results, loser_code=HousingGenderConflict.code)
    with Session(postgres_engine) as verification:
        assert (
            verification.scalar(
                select(func.count(HousingStay.id)).where(
                    HousingStay.room_id == room_id,
                    HousingStay.status.in_(("planned", "checked_in", "checkout_pending")),
                )
            )
            == 1
        )


def test_two_rooms_same_person_race_serializes_on_person_lock_and_gist(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    with Session(postgres_engine) as setup, setup.begin():
        first_room, second_room = _rooms(setup, 2, capacity=1)
        person_id, actor_id = _person_and_account(
            setup,
            name="跨房并发同一人员",
            sex="male",
        )
        room_ids = (first_room.id, second_room.id)

    request.addfinalizer(
        partial(
            _cleanup_committed_race_rows,
            postgres_engine,
            room_ids=room_ids,
            person_ids=(person_id,),
        )
    )

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda room_id: _race_worker(
                    postgres_engine,
                    barrier,
                    room_id=room_id,
                    person_id=person_id,
                    actor_id=actor_id,
                ),
                room_ids,
            )
        )

    _assert_one_commit(results, loser_code=HousingPersonTimeConflict.code)
    with Session(postgres_engine) as verification:
        assert (
            verification.scalar(
                select(func.count(HousingStay.id)).where(
                    HousingStay.person_id == person_id,
                    HousingStay.status.in_(("planned", "checked_in", "checkout_pending")),
                )
            )
            == 1
        )
