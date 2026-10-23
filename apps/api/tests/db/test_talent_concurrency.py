from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.talent_tests.catalog import CURRENT_CATALOG_VERSION, TEST_IDS
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession
from tests.services.talent_helpers import cleanup_actor, reload_actor, student_actor, submission

NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)


@pytest.fixture
def committed_actor(postgres_engine):
    with Session(postgres_engine, expire_on_commit=False) as db:
        actor = student_actor(db)
        ids = actor.account.id, actor.person.id
    yield ids
    with Session(postgres_engine) as db:
        cleanup_actor(db, ids[0])


def counts(db, person_id, account_id):
    sessions = select(TalentTestSession.id).where(TalentTestSession.person_id == person_id)
    return (
        db.scalar(
            select(func.count())
            .select_from(TalentTestSession)
            .where(TalentTestSession.person_id == person_id)
        ),
        db.scalar(
            select(func.count())
            .select_from(TalentTestResult)
            .where(TalentTestResult.session_id.in_(sessions))
        ),
        db.scalar(
            select(func.count())
            .select_from(TalentTestReport)
            .where(TalentTestReport.session_id.in_(sessions))
        ),
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.actor_user_id == account_id)
        ),
    )


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_start_one_battery(postgres_engine, committed_actor, same_key):
    from app.talent_tests.errors import TalentConflict
    from app.talent_tests.service import TalentTestService

    account_id, person_id = committed_actor
    key, barrier = uuid4(), Barrier(2)

    def run(index):
        with Session(postgres_engine) as db:
            actor = reload_actor(db, account_id)
            barrier.wait(10)
            try:
                view, created = TalentTestService(db).start(
                    actor=actor,
                    request_id=key if same_key or index == 0 else uuid4(),
                    catalog_version=CURRENT_CATALOG_VERSION,
                    now=NOW,
                )
                result = (view.id, created)
            except TalentConflict:
                result = "conflict"
            assert db.scalar(select(1)) == 1
            return result

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, i) for i in range(2)]
        outcomes = [f.result(20) for f in futures]
    if same_key:
        assert outcomes[0][0] == outcomes[1][0]
        assert sum(row[1] for row in outcomes) == 1
    else:
        assert outcomes.count("conflict") == 1
    with Session(postgres_engine) as db:
        assert counts(db, person_id, account_id) == (1, 0, 0, 1)


@pytest.mark.parametrize(
    "scenario",
    ["same_body", "changed_body", "different_keys", "last_replay", "seven_eight"],
)
def test_concurrent_results_are_linearizable(postgres_engine, committed_actor, scenario):
    from app.talent_tests.errors import TalentConflict
    from app.talent_tests.service import TalentTestService

    account_id, person_id = committed_actor
    with Session(postgres_engine) as db:
        actor, svc = reload_actor(db, account_id), TalentTestService(db)
        current, _ = svc.start(
            actor=actor,
            request_id=uuid4(),
            catalog_version=CURRENT_CATALOG_VERSION,
            now=NOW,
        )
        prefill = 7 if scenario == "last_replay" else 6 if scenario == "seven_eight" else 0
        for test_id in TEST_IDS[:prefill]:
            svc.submit(actor=actor, session_id=current.id, value=submission(test_id), now=NOW)
    value = submission(TEST_IDS[prefill])
    values = [value, value]
    if scenario == "changed_body":
        values[1] = value.model_copy(update={"duration_ms": 12000})
    elif scenario == "different_keys":
        values[1] = submission("reactiontime")
    elif scenario == "seven_eight":
        values[1] = submission(TEST_IDS[7])
    barrier = Barrier(2)

    def run(index):
        with Session(postgres_engine) as db:
            actor = reload_actor(db, account_id)
            # Intentionally preload stale identity-map state before the rival commits.
            stale = db.get(TalentTestSession, current.id)
            assert stale.status == "in_progress"
            assert len(stale.results) == prefill
            barrier.wait(10)
            try:
                view, created = TalentTestService(db).submit(
                    actor=actor, session_id=current.id, value=values[index], now=NOW
                )
                outcome = (view.result.id, created)
            except TalentConflict:
                outcome = "conflict"
            assert db.scalar(select(1)) == 1
            return outcome

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, i) for i in range(2)]
        outcomes = [f.result(20) for f in futures]
    if scenario in {"changed_body", "different_keys"}:
        assert outcomes.count("conflict") == 1
    elif scenario != "seven_eight":
        assert outcomes[0][0] == outcomes[1][0]
        assert sum(row[1] for row in outcomes) == 1
    else:
        assert all(row[1] for row in outcomes)
    completed = scenario in {"last_replay", "seven_eight"}
    with Session(postgres_engine) as db:
        assert counts(db, person_id, account_id) == (
            1,
            8 if completed else 1,
            int(completed),
            2 if completed else 1,
        )
        assert db.get(TalentTestSession, current.id).status == (
            "completed" if completed else "in_progress"
        )


@pytest.mark.parametrize("first", ["abandon", "submit"])
@pytest.mark.parametrize("prefill", [0, 7])
def test_abandon_and_submit_ordered_race(postgres_engine, committed_actor, first, prefill):
    from app.talent_tests.errors import TalentConflict
    from app.talent_tests.service import TalentTestService

    account_id, person_id = committed_actor
    with Session(postgres_engine) as db:
        actor, svc = reload_actor(db, account_id), TalentTestService(db)
        current, _ = svc.start(
            actor=actor,
            request_id=uuid4(),
            catalog_version=CURRENT_CATALOG_VERSION,
            now=NOW,
        )
        for test_id in TEST_IDS[:prefill]:
            svc.submit(actor=actor, session_id=current.id, value=submission(test_id), now=NOW)
    first_written, contender_ready, release = Event(), Event(), Event()

    def operation(db, action):
        actor, svc = reload_actor(db, account_id), TalentTestService(db)
        if action == "abandon":
            return svc.abandon(
                actor=actor, session_id=current.id, request_id=uuid4(), now=NOW
            ).status
        return svc.submit(
            actor=actor, session_id=current.id, value=submission(TEST_IDS[prefill]), now=NOW
        )[0].session.status

    def winner():
        with Session(postgres_engine) as db, db.begin():
            status = operation(db, first)
            first_written.set()
            assert release.wait(10)
        return status

    def contender():
        assert first_written.wait(10)
        with Session(postgres_engine) as db:
            contender_ready.set()
            try:
                status = operation(db, "submit" if first == "abandon" else "abandon")
            except TalentConflict:
                status = "conflict"
            assert db.scalar(select(1)) == 1
            return status

    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(winner), pool.submit(contender)
        assert contender_ready.wait(10)
        release.set()
        outcomes = a.result(20), b.result(20)
    completed = first == "submit" and prefill == 7
    if first == "abandon" or completed:
        assert outcomes[1] == "conflict"
    else:
        assert outcomes == ("in_progress", "abandoned")
    with Session(postgres_engine) as db:
        assert counts(db, person_id, account_id) == (
            1,
            prefill + int(first == "submit"),
            int(completed),
            2,
        )
        assert db.get(TalentTestSession, current.id).status == (
            "completed" if completed else "abandoned"
        )


@pytest.mark.parametrize("action", ["submit", "abandon"])
def test_both_commands_wait_on_the_same_session_row(postgres_engine, committed_actor, action):
    """Observe a real PostgreSQL blocker, independent of the common person lock."""
    from app.talent_tests.service import TalentTestService

    account_id, person_id = committed_actor
    with Session(postgres_engine) as db:
        current, _ = TalentTestService(db).start(
            actor=reload_actor(db, account_id),
            request_id=uuid4(),
            catalog_version=CURRENT_CATALOG_VERSION,
            now=NOW,
        )
    ready, worker_pid = Event(), []

    def contender():
        with Session(postgres_engine) as db:
            actor = reload_actor(db, account_id)
            worker_pid.append(db.scalar(select(func.pg_backend_pid())))
            ready.set()
            svc = TalentTestService(db)
            if action == "submit":
                return svc.submit(
                    actor=actor, session_id=current.id, value=submission("reactiontime"), now=NOW
                )[0].session.status
            return svc.abandon(
                actor=actor, session_id=current.id, request_id=uuid4(), now=NOW
            ).status

    with Session(postgres_engine) as holder:
        holder.scalar(
            select(TalentTestSession).where(TalentTestSession.id == current.id).with_for_update()
        )
        holder_pid = holder.scalar(select(func.pg_backend_pid()))
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(contender)
            try:
                assert ready.wait(10)
                deadline, blocked = monotonic() + 10, False
                with Session(postgres_engine) as observer:
                    while monotonic() < deadline:
                        blockers = observer.scalar(select(func.pg_blocking_pids(worker_pid[0])))
                        if holder_pid in blockers:
                            blocked = True
                            break
                        Event().wait(0.01)
                assert blocked, "command never waited on the session mutex"
                assert not future.done()
            finally:
                holder.rollback()
            assert future.result(20) == ("in_progress" if action == "submit" else "abandoned")
    with Session(postgres_engine) as db:
        assert counts(db, person_id, account_id) == (
            1,
            int(action == "submit"),
            0,
            1 if action == "submit" else 2,
        )
