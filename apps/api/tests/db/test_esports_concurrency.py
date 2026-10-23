from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from threading import Barrier, Event
from time import monotonic
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from app.esports_assessments.schemas import PracticeCreate
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.schemas import PermissionOverrideCreate
from app.rbac.seed import PERMISSIONS, ROLES
from tests.esports_helpers import (
    assessment_counts,
    attempt_request,
    cleanup_assessment_actor,
    committed_assessment_actor,
    counts,
    environment,
    reload_actor,
    result_request,
    session_request,
    settings,
)

pytestmark = pytest.mark.usefixtures("esports_enabled")
NOW = datetime(2026, 9, 22, 8, tzinfo=UTC)


@pytest.fixture
def committed_actor(postgres_engine):
    actor = committed_assessment_actor(postgres_engine)
    yield actor.account_id, actor.person_id
    cleanup_assessment_actor(postgres_engine, actor.person_id)


def _backend_pid(db: Session) -> int:
    pid = db.scalar(select(func.pg_backend_pid()))
    assert pid is not None
    return pid


def _wait_for_database_lock(
    engine: Engine,
    *,
    blocked_pid: int,
    blocker_pid: int,
    timeout: float = 10,
) -> None:
    deadline = monotonic() + timeout
    pause = Event()
    observed: tuple[str | None, list[int]] | None = None
    with engine.connect() as connection:
        while monotonic() < deadline:
            observed = connection.execute(
                text(
                    "SELECT wait_event_type, pg_blocking_pids(pid) "
                    "FROM pg_stat_activity WHERE pid = :pid"
                ),
                {"pid": blocked_pid},
            ).one_or_none()
            if observed is not None and observed[0] == "Lock" and blocker_pid in observed[1]:
                return
            pause.wait(0.01)
    pytest.fail(
        "contender did not enter the expected PostgreSQL lock wait "
        f"(wait_event_type={observed[0] if observed else None}, "
        f"blocker_count={len(observed[1]) if observed else 0})"
    )


def _observed_ordered_race(
    engine: Engine,
    first_operation: Callable[[Session], Any],
    contender_operation: Callable[[Session], Any],
) -> tuple[Any, Any]:
    first_written = Event()
    release = Event()
    blocker_pids: Queue[int] = Queue(maxsize=1)
    contender_pids: Queue[int] = Queue(maxsize=1)

    def winner() -> Any:
        with Session(engine) as db, db.begin():
            blocker_pids.put(_backend_pid(db), timeout=10)
            outcome = first_operation(db)
            first_written.set()
            if not release.wait(10):
                raise TimeoutError("winner release timed out")
        return outcome

    def contender() -> Any:
        if not first_written.wait(10):
            raise TimeoutError("winner did not reach its held transaction")
        with Session(engine) as db:
            contender_pids.put(_backend_pid(db), timeout=10)
            return contender_operation(db)

    with ThreadPoolExecutor(2) as pool:
        winner_future = pool.submit(winner)
        if not first_written.wait(10):
            release.set()
            winner_future.result(20)
            raise AssertionError("winner did not hold its transaction")
        contender_future = pool.submit(contender)
        blocker_pid = blocker_pids.get(timeout=10)
        blocked_pid = contender_pids.get(timeout=10)
        try:
            _wait_for_database_lock(
                engine,
                blocked_pid=blocked_pid,
                blocker_pid=blocker_pid,
            )
        finally:
            release.set()
        return winner_future.result(20), contender_future.result(20)


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_session_create_is_linearizable(postgres_engine, committed_actor, same_key):
    from app.esports_assessments.errors import AssessmentConflict
    from app.esports_assessments.formal_service import FormalAssessmentService

    account_id, person_id = committed_actor
    key, barrier = uuid4(), Barrier(2)

    def run(index):
        with Session(postgres_engine) as db:
            actor = reload_actor(db, account_id)
            barrier.wait(10)
            try:
                view = FormalAssessmentService(db).create(
                    actor,
                    session_request("pc-fps", key if same_key or index == 0 else uuid4()),
                    NOW,
                )
                return view.id
            except AssessmentConflict:
                return "conflict"

    with ThreadPoolExecutor(2) as pool:
        outcomes = [future.result(20) for future in [pool.submit(run, i) for i in range(2)]]
    if same_key:
        assert outcomes[0] == outcomes[1]
    else:
        assert outcomes.count("conflict") == 1
    with Session(postgres_engine) as db:
        assert counts(db, person_id, account_id) == (1, 0, 0, 0, 1)


def test_concurrent_last_submit_creates_one_result_report_and_completion_audit(
    postgres_engine, committed_actor
):
    from app.esports_assessments.formal_service import FormalAssessmentService

    account_id, person_id = committed_actor
    with Session(postgres_engine) as db:
        service, actor = FormalAssessmentService(db), reload_actor(db, account_id)
        session = service.create(actor, session_request(), NOW)
        for index, test_id in enumerate(("precision", "tracking", "switching")):
            started_at = NOW + timedelta(seconds=90 * index)
            attempt = service.start_attempt(
                actor, session.id, attempt_request("pc-fps", test_id), started_at
            )
            service.submit(
                actor,
                attempt.id,
                result_request(
                    "pc-fps",
                    test_id,
                    seed=attempt.seed,
                    catalog_version=attempt.config.catalog_version,
                ),
                started_at + timedelta(seconds=63),
            )
        last = service.start_attempt(
            actor,
            session.id,
            attempt_request("pc-fps", "recognition"),
            NOW + timedelta(seconds=270),
        )
        request = result_request(
            "pc-fps",
            "recognition",
            seed=last.seed,
            catalog_version=last.config.catalog_version,
        )
    barrier = Barrier(2)

    def run():
        with Session(postgres_engine) as db:
            actor = reload_actor(db, account_id)
            barrier.wait(10)
            saved = FormalAssessmentService(db).submit(
                actor,
                last.id,
                request,
                NOW + timedelta(seconds=333),
            )
            return saved.result_id, saved.session.report_id

    with ThreadPoolExecutor(2) as pool:
        outcomes = [future.result(20) for future in [pool.submit(run) for _ in range(2)]]
    assert outcomes[0] == outcomes[1]
    with Session(postgres_engine) as db:
        assert counts(db, person_id, account_id) == (1, 4, 4, 1, 2)
        assert db.get(AssessmentSession, session.id).status == "completed"
        assert (
            db.scalar(select(AssessmentReport.id).where(AssessmentReport.session_id == session.id))
            == outcomes[0][1]
        )


@pytest.mark.parametrize("first", ["abandon", "submit"])
def test_abandon_and_submit_have_one_ordered_winner(postgres_engine, committed_actor, first):
    from app.esports_assessments.errors import AssessmentConflict
    from app.esports_assessments.formal_service import FormalAssessmentService

    account_id, _ = committed_actor
    with Session(postgres_engine) as db:
        service, actor = FormalAssessmentService(db), reload_actor(db, account_id)
        session = service.create(actor, session_request(), NOW)
        attempt = service.start_attempt(
            actor, session.id, attempt_request("pc-fps", "precision"), NOW
        )
        request = result_request(
            "pc-fps",
            "precision",
            seed=attempt.seed,
            catalog_version=attempt.config.catalog_version,
        )

    def operation(db, action):
        service, actor = FormalAssessmentService(db), reload_actor(db, account_id)
        if action == "abandon":
            return service.abandon(actor, session.id, uuid4(), NOW).status
        return service.submit(actor, attempt.id, request, NOW + timedelta(seconds=63)).result_id

    def contender(db):
        try:
            return operation(db, "submit" if first == "abandon" else "abandon")
        except AssessmentConflict as exc:
            return "conflict", exc.code

    outcomes = _observed_ordered_race(
        postgres_engine,
        lambda db: operation(db, first),
        contender,
    )
    if first == "abandon":
        assert outcomes == ("abandoned", ("conflict", "esports_session_terminal"))
    else:
        assert outcomes[1] == "abandoned"
        with Session(postgres_engine) as db:
            replay = FormalAssessmentService(db).submit(
                reload_actor(db, account_id), attempt.id, request, NOW
            )
            assert replay.result_id == outcomes[0]


def test_concurrent_formal_and_practice_start_share_one_account_lease(
    postgres_engine,
    committed_actor,
) -> None:
    from app.esports_assessments.errors import AssessmentConflict
    from app.esports_assessments.formal_service import FormalAssessmentService
    from app.esports_assessments.practice_service import PracticeAssessmentService

    account_id, person_id = committed_actor
    with Session(postgres_engine) as db:
        actor = reload_actor(db, account_id)
        session = FormalAssessmentService(db).create(actor, session_request(), NOW)

    barrier = Barrier(2)

    def start(mode: str):
        with Session(postgres_engine) as db:
            actor = reload_actor(db, account_id)
            barrier.wait(10)
            try:
                if mode == "formal":
                    view = FormalAssessmentService(db).start_attempt(
                        actor,
                        session.id,
                        attempt_request("pc-fps", "precision"),
                        NOW,
                    )
                else:
                    view = PracticeAssessmentService(db).start(
                        actor,
                        PracticeCreate(
                            suite_id="pc-moba",
                            test_id="last-hit",
                            difficulty=3,
                            settings=settings("pc-moba"),
                            environment=environment("pc-moba"),
                            operation_key=uuid4(),
                        ),
                        NOW,
                    )
                return mode, view.id
            except AssessmentConflict as exc:
                return "conflict", exc.code

    with ThreadPoolExecutor(2) as pool:
        outcomes = [
            future.result(20)
            for future in (
                pool.submit(start, "formal"),
                pool.submit(start, "practice"),
            )
        ]
    assert sorted(outcome[0] for outcome in outcomes) in (
        ["conflict", "formal"],
        ["conflict", "practice"],
    )
    assert ("conflict", "esports_active_run") in outcomes
    winner_mode, winner_id = next(outcome for outcome in outcomes if outcome[0] != "conflict")

    later = NOW + timedelta(seconds=91)
    with Session(postgres_engine) as db:
        actor = reload_actor(db, account_id)
        if winner_mode == "formal":
            fresh = PracticeAssessmentService(db).start(
                actor,
                PracticeCreate(
                    suite_id="pc-moba",
                    test_id="last-hit",
                    difficulty=3,
                    settings=settings("pc-moba"),
                    environment=environment("pc-moba"),
                    operation_key=uuid4(),
                ),
                later,
            )
            stale = db.get(AssessmentAttempt, winner_id)
            assert fresh.mode == "practice"
        else:
            fresh = FormalAssessmentService(db).start_attempt(
                actor,
                session.id,
                attempt_request("pc-fps", "precision"),
                later,
            )
            stale = db.get(AssessmentPracticeRun, winner_id)
            assert fresh.mode == "formal"
        assert stale is not None and stale.state == "awaiting_submission"
        live_formal = db.scalar(
            select(func.count())
            .select_from(AssessmentAttempt)
            .where(
                AssessmentAttempt.created_by_user_id == account_id,
                AssessmentAttempt.state == "running",
            )
        )
        live_practice = db.scalar(
            select(func.count())
            .select_from(AssessmentPracticeRun)
            .where(
                AssessmentPracticeRun.created_by_user_id == account_id,
                AssessmentPracticeRun.state == "running",
            )
        )
        assert (live_formal or 0) + (live_practice or 0) == 1
        assert assessment_counts(db, person_id)["formal_results"] == 0


@pytest.mark.parametrize("first", ["deny", "submit"])
def test_permission_change_and_final_submit_have_one_authorized_order(
    postgres_engine,
    committed_actor,
    first,
) -> None:
    from app.esports_assessments.errors import AssessmentForbidden
    from app.esports_assessments.formal_service import FormalAssessmentService
    from app.rbac.service import RbacService

    account_id, person_id = committed_actor
    manager = committed_assessment_actor(postgres_engine)
    try:
        with Session(postgres_engine) as db:
            db.add(UserRole(user_id=manager.account_id, role_id=ROLES["super_admin"].id))
            db.commit()
        with Session(postgres_engine) as db:
            service = FormalAssessmentService(db)
            actor = reload_actor(db, account_id)
            session = service.create(actor, session_request(), NOW)
            for index, test_id in enumerate(("precision", "tracking", "switching")):
                started_at = NOW + timedelta(seconds=index * 90)
                attempt = service.start_attempt(
                    actor,
                    session.id,
                    attempt_request("pc-fps", test_id),
                    started_at,
                )
                service.submit(
                    actor,
                    attempt.id,
                    result_request(
                        "pc-fps",
                        test_id,
                        seed=attempt.seed,
                        catalog_version=attempt.config.catalog_version,
                    ),
                    started_at + timedelta(seconds=63),
                )
            last = service.start_attempt(
                actor,
                session.id,
                attempt_request("pc-fps", "recognition"),
                NOW + timedelta(seconds=270),
            )
            request = result_request(
                "pc-fps",
                "recognition",
                seed=last.seed,
                catalog_version=last.config.catalog_version,
            )

        deny = PermissionOverrideCreate(
            permission_id=PERMISSIONS["esports_assessments.read"].id,
            effect="deny",
            scope_kind="self",
            grant_reason="并发权限复核",
        )

        def submit_operation(db: Session):
            try:
                return FormalAssessmentService(db).submit(
                    reload_actor(db, account_id),
                    last.id,
                    request,
                    NOW + timedelta(seconds=333),
                )
            except AssessmentForbidden:
                return "forbidden"

        def deny_operation(db: Session):
            override, _permission = RbacService(db).add_override(
                actor_user_id=manager.account_id,
                account_id=account_id,
                input=deny,
            )
            return override.id

        operations = {
            "deny": deny_operation,
            "submit": submit_operation,
        }
        outcomes = _observed_ordered_race(
            postgres_engine,
            operations[first],
            operations["submit" if first == "deny" else "deny"],
        )

        with Session(postgres_engine) as db:
            stored_override = db.scalar(
                select(UserPermissionOverride.id).where(
                    UserPermissionOverride.user_id == account_id,
                    UserPermissionOverride.permission_id
                    == PERMISSIONS["esports_assessments.read"].id,
                    UserPermissionOverride.effect == "deny",
                    UserPermissionOverride.scope_kind == "self",
                )
            )
            assert stored_override is not None
            if first == "deny":
                assert outcomes[1] == "forbidden"
                assert assessment_counts(db, person_id) == {
                    "formal_results": 3,
                    "formal_reports": 0,
                    "practice_results": 0,
                }
                assert db.get(AssessmentSession, session.id).status == "in_progress"
            else:
                saved = outcomes[0]
                assert saved.session.report_id is not None
                assert assessment_counts(db, person_id) == {
                    "formal_results": 4,
                    "formal_reports": 1,
                    "practice_results": 0,
                }
                with pytest.raises(AssessmentForbidden):
                    FormalAssessmentService(db).submit(
                        reload_actor(db, account_id),
                        last.id,
                        request,
                        NOW + timedelta(seconds=331),
                    )
            assert db.scalar(
                select(func.count())
                .select_from(AssessmentResult)
                .where(AssessmentResult.session_id == session.id)
            ) == (3 if first == "deny" else 4)
    finally:
        cleanup_assessment_actor(postgres_engine, manager.person_id)
