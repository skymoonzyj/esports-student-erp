from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import Barrier, Event, Lock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.food_preferences.errors import (
    FoodPreferenceOnboardingIncomplete,
    FoodPreferenceVersionChanged,
)
from app.food_preferences.models import (
    FoodCuisineOption,
    PersonFoodPreference,
    PersonFoodPreferenceCuisine,
    PersonFoodPreferenceDish,
    PersonFoodPreferenceVersion,
)
from app.food_preferences.preference_service import (
    FoodPreferenceProjection,
    FoodPreferenceSelfProjection,
    FoodPreferenceService,
    ReplaceFoodPreferenceCommand,
)
from app.food_preferences.schemas import CuisineSelectionInput, FoodPreferenceWriteRequest
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from tests.account_identifiers import add_test_primary_login_identifier

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


@dataclass(frozen=True)
class _Scenario:
    engine: Engine
    person_id: UUID
    admin_person_id: UUID
    guardian_person_id: UUID
    self_account_id: UUID
    admin_account_id: UUID
    guardian_account_id: UUID
    option_id: UUID


_SEEDED_SCENARIOS: list[_Scenario] = []


@dataclass(frozen=True)
class _WorkerResult:
    status: str
    session_reusable: bool
    observed_version: int
    observed_dishes: tuple[str, ...]


@pytest.fixture(autouse=True)
def _cleanup_food_preference_scenarios() -> Iterator[None]:
    """Keep committed cross-thread fixtures from poisoning later migrations.

    These tests intentionally commit from several sessions so that PostgreSQL
    locking is observable.  The normal per-test transaction fixture therefore
    cannot roll their rows back; clean every scenario created during the test
    after the assertions, including the onboarding completion marker.
    """

    yield
    scenarios = list(_SEEDED_SCENARIOS)
    _SEEDED_SCENARIOS.clear()
    for scenario in scenarios:
        account_ids = [
            scenario.self_account_id,
            scenario.admin_account_id,
            scenario.guardian_account_id,
        ]
        person_ids = [
            scenario.person_id,
            scenario.admin_person_id,
            scenario.guardian_person_id,
        ]
        with scenario.engine.connect() as connection:
            connection.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            connection.execute(
                text(
                    "ALTER TABLE person_food_preference_versions "
                    "DISABLE TRIGGER trg_reject_food_preference_version_mutation"
                )
            )
            connection.commit()
        try:
            with scenario.engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM audit_events WHERE actor_user_id = ANY(CAST(:account_ids AS uuid[])) "
                        "OR resource_id = ANY(CAST(:person_ids AS uuid[]))"
                    ),
                    {"account_ids": account_ids, "person_ids": person_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM person_food_preference_cuisines "
                        "WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                    ),
                    {"person_ids": person_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM person_food_preference_dishes "
                        "WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                    ),
                    {"person_ids": person_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM person_food_preference_versions "
                        "WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                    ),
                    {"person_ids": person_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM person_food_preferences "
                        "WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                    ),
                    {"person_ids": person_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM guardian_relationships "
                        "WHERE guardian_person_id = ANY(CAST(:person_ids AS uuid[])) "
                        "OR student_person_id = ANY(CAST(:person_ids AS uuid[]))"
                    ),
                    {"person_ids": person_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM student_profiles WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                    ),
                    {"person_ids": person_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM staff_profiles WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                    ),
                    {"person_ids": person_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM visitor_profiles WHERE person_id = ANY(CAST(:person_ids AS uuid[]))"
                    ),
                    {"person_ids": person_ids},
                )
                connection.execute(
                    text("DELETE FROM food_cuisine_options WHERE id = :option_id"),
                    {"option_id": scenario.option_id},
                )
                connection.execute(
                    text("DELETE FROM user_accounts WHERE id = ANY(CAST(:account_ids AS uuid[]))"),
                    {"account_ids": account_ids},
                )
                connection.execute(
                    text("DELETE FROM people WHERE id = ANY(CAST(:person_ids AS uuid[]))"),
                    {"person_ids": person_ids},
                )
        finally:
            with scenario.engine.connect() as connection:
                connection.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
                connection.execute(
                    text(
                        "ALTER TABLE person_food_preference_versions "
                        "ENABLE TRIGGER trg_reject_food_preference_version_mutation"
                    )
                )
                connection.commit()


class _PersonLockProbe:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.attempt_count = 0
        self.first_lock_acquired = Event()
        self.second_lock_attempt = Event()
        self._guard = Lock()
        self._attempts: dict[int, int] = {}

    @staticmethod
    def _is_person_lock(statement: str) -> bool:
        normalized = " ".join(statement.upper().split())
        return "FROM PEOPLE" in normalized and "FOR UPDATE" in normalized

    def _before_cursor_execute(
        self,
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, executemany
        if not self._is_person_lock(statement):
            return
        with self._guard:
            self.attempt_count += 1
            self._attempts[id(context)] = self.attempt_count
            if self.attempt_count == 2:
                self.second_lock_attempt.set()

    def _after_cursor_execute(
        self,
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, executemany
        if not self._is_person_lock(statement):
            return
        if self._attempts[id(context)] == 1:
            self.first_lock_acquired.set()
            assert self.second_lock_attempt.wait(10), "second writer did not contend"

    def install(self) -> None:
        event.listen(self.engine, "before_cursor_execute", self._before_cursor_execute)
        event.listen(self.engine, "after_cursor_execute", self._after_cursor_execute)

    def remove(self) -> None:
        event.remove(
            self.engine,
            "before_cursor_execute",
            self._before_cursor_execute,
        )
        event.remove(
            self.engine,
            "after_cursor_execute",
            self._after_cursor_execute,
        )


class _ProjectionReadProbe:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.reader_midpoint = Event()
        self.writer_root_acquired = Event()
        self.writer_done = Event()
        self.reader_used_shared_person_lock = False

    @staticmethod
    def _normalized(statement: str) -> str:
        return " ".join(statement.upper().split())

    def _after_cursor_execute(
        self,
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del cursor, parameters, context, executemany
        normalized = self._normalized(statement)
        worker = connection.info.get("food_preference_worker")
        if worker == "reader" and "FROM PEOPLE" in normalized:
            self.reader_used_shared_person_lock = "FOR SHARE" in normalized
        elif (
            worker == "writer"
            and "FROM PERSON_FOOD_PREFERENCES" in normalized
            and "FOR UPDATE" in normalized
        ):
            self.writer_root_acquired.set()
        elif (
            worker == "reader"
            and "FROM PERSON_FOOD_PREFERENCE_CUISINES" in normalized
        ):
            self.reader_midpoint.set()
            if self.writer_root_acquired.wait(1):
                assert self.writer_done.wait(10), "writer did not commit at midpoint"

    def install(self) -> None:
        event.listen(self.engine, "after_cursor_execute", self._after_cursor_execute)

    def remove(self) -> None:
        event.remove(
            self.engine,
            "after_cursor_execute",
            self._after_cursor_execute,
        )


def _person(*, person_id: UUID, label: str) -> Person:
    return Person(
        id=person_id,
        full_name=f"饮食偏好并发{label}",
        identity_ciphertext=f"food-concurrency-cipher-{person_id.hex}",
        identity_digest=f"food-concurrency-id-{person_id.hex}",
        sex="female",
        date_of_birth=date(1990, 1, 1),
    )


def _account(*, account_id: UUID, person_id: UUID, label: str) -> UserAccount:
    return UserAccount(
        id=account_id,
        person_id=person_id,
        login_phone_digest=f"food-concurrency-phone-{account_id.hex}",
        email_normalized=f"food-concurrency-{label}-{account_id.hex}@example.test",
        password_hash="test-password",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )


def _seed(postgres_engine: Engine) -> _Scenario:
    person_id = uuid4()
    self_account_id = uuid4()
    admin_person_id = uuid4()
    admin_account_id = uuid4()
    guardian_person_id = uuid4()
    guardian_account_id = uuid4()
    option_id = uuid4()
    with Session(postgres_engine) as db, db.begin():
        db.add_all(
            [
                _person(person_id=person_id, label="目标"),
                _person(person_id=admin_person_id, label="管理员"),
                _person(person_id=guardian_person_id, label="监护人"),
            ]
        )
        db.flush()
        accounts = [
            _account(
                account_id=self_account_id,
                person_id=person_id,
                label="self",
            ),
            _account(
                account_id=admin_account_id,
                person_id=admin_person_id,
                label="admin",
            ),
            _account(
                account_id=guardian_account_id,
                person_id=guardian_person_id,
                label="guardian",
            ),
        ]
        db.add_all(
            [
                *accounts,
                StudentProfile(
                    person_id=person_id,
                    student_number=f"FOOD-CONC-{person_id.hex[:12]}",
                    status="active",
                ),
                GuardianRelationship(
                    guardian_person_id=guardian_person_id,
                    student_person_id=person_id,
                    relationship="mother",
                    is_primary=True,
                    valid_from=date(2026, 1, 1),
                ),
                FoodCuisineOption(
                    id=option_id,
                    name=f"并发菜系{option_id.hex[:6]}",
                    normalized_name=f"并发菜系{option_id.hex[:6]}".casefold(),
                    sort_order=10,
                    is_active=True,
                    is_other=False,
                    created_by_user_id=admin_account_id,
                    updated_by_user_id=admin_account_id,
                ),
            ]
        )
        for account in accounts:
            add_test_primary_login_identifier(db, account)
    scenario = _Scenario(
        engine=postgres_engine,
        person_id=person_id,
        admin_person_id=admin_person_id,
        guardian_person_id=guardian_person_id,
        self_account_id=self_account_id,
        admin_account_id=admin_account_id,
        guardian_account_id=guardian_account_id,
        option_id=option_id,
    )
    _SEEDED_SCENARIOS.append(scenario)
    return scenario


def _command(
    scenario: _Scenario,
    *,
    expected_version: int,
    dish: str,
    actor_user_id: UUID,
    source: str,
) -> ReplaceFoodPreferenceCommand:
    return ReplaceFoodPreferenceCommand(
        expected_version=expected_version,
        cuisines=(CuisineSelectionInput(cuisine_option_id=scenario.option_id),),
        dishes=(dish,),
        actor_user_id=actor_user_id,
        source=source,
        request_id=f"food-concurrency-{uuid4()}",
    )


def _generic_worker(
    scenario: _Scenario,
    *,
    expected_version: int,
    dish: str,
    actor_user_id: UUID,
    source: str,
    start: Barrier | None = None,
) -> _WorkerResult:
    with Session(scenario.engine, expire_on_commit=False) as db:
        db.execute(text("SET LOCAL statement_timeout = '8s'"))
        if start is not None:
            start.wait(timeout=10)
        try:
            FoodPreferenceService(db).replace(
                scenario.person_id,
                _command(
                    scenario,
                    expected_version=expected_version,
                    dish=dish,
                    actor_user_id=actor_user_id,
                    source=source,
                ),
            )
        except FoodPreferenceVersionChanged:
            status = FoodPreferenceVersionChanged.code
        else:
            status = "committed"
        session_reusable = db.scalar(select(1)) == 1
        observed = FoodPreferenceService(db).get(scenario.person_id)
        return _WorkerResult(
            status=status,
            session_reusable=session_reusable,
            observed_version=observed.version,
            observed_dishes=observed.dishes,
        )


def _self_worker(
    scenario: _Scenario,
    *,
    dish: str,
) -> _WorkerResult:
    with Session(scenario.engine, expire_on_commit=False) as db:
        db.execute(text("SET LOCAL statement_timeout = '8s'"))
        try:
            FoodPreferenceService(db).replace_self(
                account_id=scenario.self_account_id,
                input=FoodPreferenceWriteRequest(
                    expected_version=0,
                    cuisines=[
                        CuisineSelectionInput(cuisine_option_id=scenario.option_id)
                    ],
                    dishes=[dish],
                ),
                request_id=f"food-self-concurrency-{uuid4()}",
                occurred_at=NOW,
            )
        except FoodPreferenceVersionChanged:
            status = FoodPreferenceVersionChanged.code
        else:
            status = "committed"
        session_reusable = db.scalar(select(1)) == 1
        observed = FoodPreferenceService(db).get(scenario.person_id)
        return _WorkerResult(
            status=status,
            session_reusable=session_reusable,
            observed_version=observed.version,
            observed_dishes=observed.dishes,
        )


def _run_ordered(
    probe: _PersonLockProbe,
    first: Callable[[], _WorkerResult],
    second: Callable[[], _WorkerResult],
) -> tuple[_WorkerResult, _WorkerResult]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        assert probe.first_lock_acquired.wait(10), "first writer did not lock Person"
        second_future = pool.submit(second)
        return first_future.result(timeout=20), second_future.result(timeout=20)


def _assert_one_winner(results: tuple[_WorkerResult, _WorkerResult]) -> None:
    assert sorted(result.status for result in results) == [
        "committed",
        FoodPreferenceVersionChanged.code,
    ]
    assert all(result.session_reusable for result in results)
    assert {result.observed_version for result in results} == {1}
    assert len({result.observed_dishes for result in results}) == 1


def test_replace_locked_never_commits_the_callers_transaction(
    postgres_engine: Engine,
) -> None:
    scenario = _seed(postgres_engine)
    with Session(scenario.engine) as db:
        transaction = db.begin()
        projection = FoodPreferenceService(db).replace_locked(
            scenario.person_id,
            _command(
                scenario,
                expected_version=0,
                dish="调用方事务菜品",
                actor_user_id=scenario.admin_account_id,
                source="camp_survey",
            ),
        )
        assert projection.version == 1
        assert transaction.is_active

        with Session(scenario.engine) as observer:
            assert observer.get(PersonFoodPreference, scenario.person_id) is None
            assert observer.scalar(
                select(func.count(PersonFoodPreferenceVersion.id)).where(
                    PersonFoodPreferenceVersion.person_id == scenario.person_id
                )
            ) == 0

        transaction.rollback()
        assert db.scalar(select(1)) == 1

    with Session(scenario.engine) as verification:
        assert verification.get(PersonFoodPreference, scenario.person_id) is None
        assert verification.scalar(
            select(func.count(PersonFoodPreferenceVersion.id)).where(
                PersonFoodPreferenceVersion.person_id == scenario.person_id
            )
        ) == 0


def test_self_replace_rechecks_onboarding_after_person_lock(
    postgres_engine: Engine,
) -> None:
    scenario = _seed(postgres_engine)
    with Session(scenario.engine) as setup:
        account = setup.get(UserAccount, scenario.guardian_account_id)
        assert account is not None
        target_person_id = account.person_id
        assert setup.scalar(
            select(func.count(GuardianRelationship.id)).where(
                GuardianRelationship.guardian_person_id == target_person_id
            )
        ) == 1
        assert setup.scalar(
            select(func.count(StudentProfile.person_id)).where(
                StudentProfile.person_id == target_person_id
            )
        ) == 0
        assert setup.scalar(
            select(func.count(StaffProfile.person_id)).where(
                StaffProfile.person_id == target_person_id
            )
        ) == 0
        assert setup.scalar(
            select(func.count(VisitorProfile.person_id)).where(
                VisitorProfile.person_id == target_person_id
            )
        ) == 0

    before_person_lock = Event()
    release_person_lock = Event()

    def block_worker_before_person_lock(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del cursor, parameters, context, executemany
        if (
            connection.info.get("food_preference_worker")
            == "onboarding-identity-race"
            and _PersonLockProbe._is_person_lock(statement)
        ):
            before_person_lock.set()
            assert release_person_lock.wait(10), "identity insert did not commit"

    def confirm_empty_profile() -> tuple[str, bool]:
        with Session(scenario.engine) as db:
            connection = db.connection()
            connection.info["food_preference_worker"] = "onboarding-identity-race"
            db.execute(text("SET LOCAL statement_timeout = '8s'"))
            try:
                try:
                    FoodPreferenceService(db).replace_self(
                        account_id=scenario.guardian_account_id,
                        input=FoodPreferenceWriteRequest(expected_version=0),
                        request_id="food-self-concurrent-identity-created",
                        occurred_at=NOW,
                    )
                except FoodPreferenceOnboardingIncomplete:
                    status = FoodPreferenceOnboardingIncomplete.code
                else:
                    status = "committed"
                return status, db.scalar(select(1)) == 1
            finally:
                if not connection.closed:
                    connection.info.pop("food_preference_worker", None)

    event.listen(
        scenario.engine,
        "before_cursor_execute",
        block_worker_before_person_lock,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(confirm_empty_profile)
            assert before_person_lock.wait(10), "worker did not reach Person lock"
            with Session(scenario.engine) as identity_writer, identity_writer.begin():
                identity_writer.add(
                    StudentProfile(
                        person_id=target_person_id,
                        student_number=f"FOOD-CONC-RACE-{target_person_id.hex[:10]}",
                        status="active",
                    )
                )
            release_person_lock.set()
            status, session_reusable = future.result(timeout=20)
    finally:
        release_person_lock.set()
        event.remove(
            scenario.engine,
            "before_cursor_execute",
            block_worker_before_person_lock,
        )

    assert status == FoodPreferenceOnboardingIncomplete.code
    assert session_reusable
    with Session(scenario.engine) as verification:
        assert verification.get(StudentProfile, target_person_id) is not None
        assert verification.scalar(
            select(
                UserAccount.food_preferences_onboarding_completed_at
            ).where(UserAccount.id == scenario.guardian_account_id)
        ) is None
        assert verification.scalar(
            select(func.count(PersonFoodPreference.person_id)).where(
                PersonFoodPreference.person_id == target_person_id
            )
        ) == 0
        assert verification.scalar(
            select(func.count(PersonFoodPreferenceCuisine.person_id)).where(
                PersonFoodPreferenceCuisine.person_id == target_person_id
            )
        ) == 0
        assert verification.scalar(
            select(func.count(PersonFoodPreferenceDish.person_id)).where(
                PersonFoodPreferenceDish.person_id == target_person_id
            )
        ) == 0
        assert verification.scalar(
            select(func.count(PersonFoodPreferenceVersion.id)).where(
                PersonFoodPreferenceVersion.person_id == target_person_id
            )
        ) == 0
        assert verification.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.resource_id == target_person_id
            )
        ) == 0


def test_get_does_not_mix_root_and_children_across_concurrent_replace(
    postgres_engine: Engine,
) -> None:
    scenario = _seed(postgres_engine)
    with Session(scenario.engine) as setup:
        FoodPreferenceService(setup).replace(
            scenario.person_id,
            _command(
                scenario,
                expected_version=0,
                dish="读取前版本",
                actor_user_id=scenario.admin_account_id,
                source="profile_admin",
            ),
        )

    probe = _ProjectionReadProbe(postgres_engine)

    def read_projection() -> FoodPreferenceProjection:
        with Session(scenario.engine) as db:
            connection = db.connection()
            connection.info["food_preference_worker"] = "reader"
            try:
                return FoodPreferenceService(db).get(scenario.person_id)
            finally:
                connection.info.pop("food_preference_worker", None)

    def replace_projection() -> FoodPreferenceProjection:
        with Session(scenario.engine) as db:
            connection = db.connection()
            connection.info["food_preference_worker"] = "writer"
            db.execute(text("SET LOCAL statement_timeout = '8s'"))
            try:
                return FoodPreferenceService(db).replace(
                    scenario.person_id,
                    _command(
                        scenario,
                        expected_version=1,
                        dish="读取后版本",
                        actor_user_id=scenario.admin_account_id,
                        source="profile_admin",
                    ),
                )
            finally:
                probe.writer_done.set()
                if not connection.closed:
                    connection.info.pop("food_preference_worker", None)

    probe.install()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reader_future = pool.submit(read_projection)
            assert probe.reader_midpoint.wait(10), "reader did not reach child rows"
            writer_future = pool.submit(replace_projection)
            reader = reader_future.result(timeout=20)
            writer = writer_future.result(timeout=20)
    finally:
        probe.remove()

    assert reader.version == 1
    assert reader.dishes == ("读取前版本",)
    assert probe.reader_used_shared_person_lock
    assert writer.version == 2
    assert writer.dishes == ("读取后版本",)


def test_get_self_refreshes_completion_after_waiting_for_first_self_replace(
    postgres_engine: Engine,
) -> None:
    scenario = _seed(postgres_engine)
    account_read = Event()
    writer_done = Event()
    completed_at = NOW.replace(hour=11)

    def pause_reader_after_account_snapshot(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del cursor, parameters, context, executemany
        normalized = " ".join(statement.upper().split())
        if (
            connection.info.get("food_preference_worker")
            == "self-projection-reader"
            and "FROM USER_ACCOUNTS JOIN PEOPLE" in normalized
        ):
            account_read.set()
            assert writer_done.wait(10), "first self replace did not commit"

    def read_self_projection() -> FoodPreferenceSelfProjection:
        with Session(scenario.engine) as db:
            connection = db.connection()
            connection.info["food_preference_worker"] = "self-projection-reader"
            try:
                return FoodPreferenceService(db).get_self(
                    account_id=scenario.self_account_id
                )
            finally:
                connection.info.pop("food_preference_worker", None)

    def complete_first_self_replace() -> None:
        assert account_read.wait(10), "reader did not snapshot the account"
        try:
            with Session(scenario.engine) as db:
                FoodPreferenceService(db).replace_self(
                    account_id=scenario.self_account_id,
                    input=FoodPreferenceWriteRequest(
                        expected_version=0,
                        cuisines=(
                            CuisineSelectionInput(
                                cuisine_option_id=scenario.option_id
                            ),
                        ),
                        dishes=("并发首次完成",),
                    ),
                    request_id="food-self-read-after-concurrent-completion",
                    occurred_at=completed_at,
                )
        finally:
            writer_done.set()

    event.listen(
        scenario.engine,
        "after_cursor_execute",
        pause_reader_after_account_snapshot,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            read_future = pool.submit(read_self_projection)
            write_future = pool.submit(complete_first_self_replace)
            projection = read_future.result(timeout=20)
            write_future.result(timeout=20)
    finally:
        writer_done.set()
        event.remove(
            scenario.engine,
            "after_cursor_execute",
            pause_reader_after_account_snapshot,
        )

    assert projection.profile.version == 1
    assert projection.profile.dishes == ("并发首次完成",)
    assert projection.onboarding_required is False
    assert projection.onboarding_completed_at == completed_at


def test_simultaneous_first_expected_zero_has_one_version_and_usable_loser(
    postgres_engine: Engine,
) -> None:
    scenario = _seed(postgres_engine)
    start = Barrier(2)
    probe = _PersonLockProbe(postgres_engine)
    probe.install()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    _generic_worker,
                    scenario,
                    expected_version=0,
                    dish=dish,
                    actor_user_id=scenario.admin_account_id,
                    source="profile_admin",
                    start=start,
                )
                for dish in ("并发首次甲", "并发首次乙")
            ]
            results = tuple(future.result(timeout=20) for future in futures)
    finally:
        probe.remove()

    assert probe.attempt_count == 2
    _assert_one_winner(results)
    with Session(postgres_engine) as db:
        root = db.get(PersonFoodPreference, scenario.person_id)
        assert root is not None and root.version == 1
        assert db.scalar(
            select(func.count(PersonFoodPreferenceVersion.id)).where(
                PersonFoodPreferenceVersion.person_id == scenario.person_id
            )
        ) == 1


@pytest.mark.parametrize(
    ("first_source", "second_source"),
    [
        ("profile_admin", "profile_guardian"),
        ("profile_guardian", "profile_admin"),
    ],
)
def test_stale_generic_and_guardian_writes_share_person_root_lock_order(
    postgres_engine: Engine,
    first_source: str,
    second_source: str,
) -> None:
    scenario = _seed(postgres_engine)
    with Session(scenario.engine) as setup:
        FoodPreferenceService(setup).replace(
            scenario.person_id,
            _command(
                scenario,
                expected_version=0,
                dish="并发初始版本",
                actor_user_id=scenario.admin_account_id,
                source="profile_admin",
            ),
        )

    actor_by_source = {
        "profile_admin": scenario.admin_account_id,
        "profile_guardian": scenario.guardian_account_id,
    }
    probe = _PersonLockProbe(postgres_engine)
    probe.install()
    try:
        results = _run_ordered(
            probe,
            lambda: _generic_worker(
                scenario,
                expected_version=1,
                dish=f"先写-{first_source}",
                actor_user_id=actor_by_source[first_source],
                source=first_source,
            ),
            lambda: _generic_worker(
                scenario,
                expected_version=1,
                dish=f"后写-{second_source}",
                actor_user_id=actor_by_source[second_source],
                source=second_source,
            ),
        )
    finally:
        probe.remove()

    assert probe.attempt_count == 2
    assert [result.status for result in results] == [
        "committed",
        FoodPreferenceVersionChanged.code,
    ]
    assert all(result.session_reusable for result in results)
    assert {result.observed_version for result in results} == {2}
    assert {result.observed_dishes for result in results} == {
        (f"先写-{first_source}",)
    }
    with Session(postgres_engine) as db:
        assert db.scalar(
            select(func.count(PersonFoodPreferenceVersion.id)).where(
                PersonFoodPreferenceVersion.person_id == scenario.person_id
            )
        ) == 2


@pytest.mark.parametrize(
    ("first_kind", "completion_expected"),
    [("admin", False), ("self", True)],
)
def test_generic_admin_and_self_onboarding_do_not_deadlock_in_either_direction(
    postgres_engine: Engine,
    first_kind: str,
    completion_expected: bool,
) -> None:
    scenario = _seed(postgres_engine)
    admin = lambda: _generic_worker(
        scenario,
        expected_version=0,
        dish="管理员并发写入",
        actor_user_id=scenario.admin_account_id,
        source="profile_admin",
    )
    self_onboarding = lambda: _self_worker(scenario, dish="本人并发写入")
    first = admin if first_kind == "admin" else self_onboarding
    second = self_onboarding if first_kind == "admin" else admin

    probe = _PersonLockProbe(postgres_engine)
    probe.install()
    try:
        results = _run_ordered(probe, first, second)
    finally:
        probe.remove()

    assert probe.attempt_count == 2
    assert [result.status for result in results] == [
        "committed",
        FoodPreferenceVersionChanged.code,
    ]
    assert all(result.session_reusable for result in results)
    assert {result.observed_version for result in results} == {1}
    expected_dish = "管理员并发写入" if first_kind == "admin" else "本人并发写入"
    assert {result.observed_dishes for result in results} == {(expected_dish,)}
    with Session(postgres_engine) as db:
        completion = db.scalar(
            select(UserAccount.food_preferences_onboarding_completed_at).where(
                UserAccount.id == scenario.self_account_id
            )
        )
        if completion_expected:
            assert completion == NOW
        else:
            assert completion is None
        assert db.scalar(
            select(func.count(PersonFoodPreferenceVersion.id)).where(
                PersonFoodPreferenceVersion.person_id == scenario.person_id
            )
        ) == 1


def test_same_account_generic_then_self_avoids_foreign_key_lock_deadlock(
    postgres_engine: Engine,
) -> None:
    scenario = _seed(postgres_engine)
    probe = _PersonLockProbe(postgres_engine)
    probe.install()
    try:
        results = _run_ordered(
            probe,
            lambda: _generic_worker(
                scenario,
                expected_version=0,
                dish="同账号问卷写入",
                actor_user_id=scenario.self_account_id,
                source="camp_survey",
            ),
            lambda: _self_worker(scenario, dish="同账号本人写入"),
        )
    finally:
        probe.remove()

    assert probe.attempt_count == 2
    assert [result.status for result in results] == [
        "committed",
        FoodPreferenceVersionChanged.code,
    ]
    assert all(result.session_reusable for result in results)
    assert {result.observed_version for result in results} == {1}
    assert {result.observed_dishes for result in results} == {("同账号问卷写入",)}
    with Session(postgres_engine) as db:
        assert db.scalar(
            select(UserAccount.food_preferences_onboarding_completed_at).where(
                UserAccount.id == scenario.self_account_id
            )
        ) is None
        assert db.scalar(
            select(func.count(PersonFoodPreferenceVersion.id)).where(
                PersonFoodPreferenceVersion.person_id == scenario.person_id
            )
        ) == 1
