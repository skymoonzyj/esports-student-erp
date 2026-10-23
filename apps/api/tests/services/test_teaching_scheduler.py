from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from functools import partial
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment, EnrollmentEvent
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.evaluations.survey_models import CampSurveyCycle, CampSurveySnapshotVersion
from app.evaluations.survey_snapshot_service import CampSurveySnapshotService
from app.identity.models import Person
from app.teaching.cohort_service import CohortService
from app.teaching.models import Cohort, CohortStatusEvent, TeachingClass
from app.teaching.scheduler import SCHEDULER_LOCK_KEY, reconcile_due_cohorts
from tests.account_identifiers import add_test_primary_login_identifier

NOW = datetime(2026, 9, 1, 8, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


def _add_actor(db: Session, *, marker: str) -> tuple[Person, UserAccount]:
    unique = uuid4().hex
    person = Person(
        full_name=f"Scheduler actor {marker}",
        identity_ciphertext="scheduler-test-ciphertext",
        identity_digest=f"scheduler-test-identity-{unique}",
        phone_ciphertext=None,
        phone_digest=None,
        email=None,
        sex="female",
        date_of_birth=date(1988, 1, 2),
    )
    db.add(person)
    db.flush()
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"scheduler-test-phone-{unique}",
        email_normalized=f"scheduler-{unique}@example.test",
        password_hash="scheduler-test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    add_test_primary_login_identifier(db, account)
    return person, account


def _add_cohort(
    db: Session,
    *,
    actor_user_id: UUID,
    name: str,
    status: str,
    starts_at: datetime,
    ends_at: datetime,
    auto_start_enabled: bool,
    auto_complete_enabled: bool,
) -> Cohort:
    cohort = Cohort(
        name=f"{name} {uuid4().hex[:8]}",
        starts_at=starts_at,
        ends_at=ends_at,
        default_check_in_at=starts_at,
        default_check_out_at=ends_at,
        status=status,
        auto_start_enabled=auto_start_enabled,
        auto_complete_enabled=auto_complete_enabled,
        created_by_user_id=actor_user_id,
    )
    db.add(cohort)
    db.flush()
    return cohort


def _add_active_enrollment(
    db: Session,
    *,
    cohort: Cohort,
    actor: UserAccount,
) -> Enrollment:
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"scheduler-completion-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=actor.id,
    )
    db.add(teaching_class)
    admission_case = AdmissionCase(
        student_person_id=actor.person_id,
        status="ready",
        agreed_tuition_cents=100_000,
        created_by_user_id=actor.id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=actor.person_id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=100_000,
        status="active",
        confirmed_at=NOW - timedelta(days=2),
        created_by_user_id=actor.id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    db.flush()
    return enrollment


def _request_ids(prefix: str) -> Callable[[], str]:
    sequence = 0

    def next_request_id() -> str:
        nonlocal sequence
        sequence += 1
        return f"{prefix}-{sequence}"

    return next_request_id


def test_scheduler_reconciles_due_flags_once_and_audits(db: Session) -> None:
    _, actor = _add_actor(db, marker="matrix")
    due_start = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="due start",
        status="open",
        starts_at=NOW - timedelta(hours=1),
        ends_at=NOW + timedelta(days=1),
        auto_start_enabled=True,
        auto_complete_enabled=True,
    )
    due_complete = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="due complete",
        status="active",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW - timedelta(hours=1),
        auto_start_enabled=True,
        auto_complete_enabled=True,
    )
    disabled_start = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="disabled start",
        status="open",
        starts_at=NOW - timedelta(hours=1),
        ends_at=NOW + timedelta(days=1),
        auto_start_enabled=False,
        auto_complete_enabled=True,
    )
    disabled_complete = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="disabled complete",
        status="active",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW - timedelta(hours=1),
        auto_start_enabled=True,
        auto_complete_enabled=False,
    )
    blocked_draft = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="blocked draft",
        status="draft",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW - timedelta(days=1),
        auto_start_enabled=True,
        auto_complete_enabled=True,
    )
    due_enrollment = _add_active_enrollment(
        db,
        cohort=due_complete,
        actor=actor,
    )
    db.commit()

    changed = reconcile_due_cohorts(
        db,
        now=NOW,
        request_id_factory=_request_ids("scheduler-matrix"),
    )
    second = reconcile_due_cohorts(
        db,
        now=NOW,
        request_id_factory=_request_ids("scheduler-matrix-repeat"),
    )

    db.expire_all()
    assert changed == 2
    assert second == 0
    assert db.get(Cohort, due_start.id).status == "active"  # type: ignore[union-attr]
    assert db.get(Cohort, due_complete.id).status == "completed"  # type: ignore[union-attr]
    assert db.get(Cohort, disabled_start.id).status == "open"  # type: ignore[union-attr]
    assert db.get(Cohort, disabled_complete.id).status == "active"  # type: ignore[union-attr]
    assert db.get(Cohort, blocked_draft.id).status == "draft"  # type: ignore[union-attr]
    stored_enrollment = db.get(Enrollment, due_enrollment.id)
    assert stored_enrollment is not None
    assert stored_enrollment.status == "completed"
    assert stored_enrollment.completed_at == NOW
    cycle = db.scalar(
        select(CampSurveyCycle).where(CampSurveyCycle.enrollment_id == due_enrollment.id)
    )
    assert cycle is not None
    assert cycle.current_snapshot_version == 1
    assert (
        db.scalar(
            select(CampSurveySnapshotVersion.id).where(
                CampSurveySnapshotVersion.cycle_id == cycle.id,
                CampSurveySnapshotVersion.version == 1,
            )
        )
        is not None
    )
    completion_events = list(
        db.scalars(
            select(EnrollmentEvent).where(
                EnrollmentEvent.enrollment_id == due_enrollment.id,
                EnrollmentEvent.event_type == "completed",
            )
        )
    )
    assert len(completion_events) == 1
    assert completion_events[0].actor_user_id is None
    assert completion_events[0].request_id.startswith("scheduler-matrix-")

    cohort_ids = {due_start.id, due_complete.id}
    events = list(
        db.scalars(
            select(CohortStatusEvent)
            .where(
                CohortStatusEvent.cohort_id.in_(cohort_ids),
                CohortStatusEvent.trigger == "scheduler",
            )
            .order_by(CohortStatusEvent.request_id)
        )
    )
    audits = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_type == "cohort",
                AuditEvent.resource_id.in_(cohort_ids),
                AuditEvent.action == "teaching.cohort.transition",
            )
        )
    )
    assert {(event.old_status, event.new_status) for event in events} == {
        ("open", "active"),
        ("active", "completed"),
    }
    assert len(events) == 2
    assert len(audits) == 2
    assert all(event.actor_user_id is None for event in [*events, *audits])


def test_scheduler_advances_only_one_adjacent_state_per_run(db: Session) -> None:
    _, actor = _add_actor(db, marker="adjacent")
    cohort = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="past both boundaries",
        status="open",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW - timedelta(days=1),
        auto_start_enabled=True,
        auto_complete_enabled=True,
    )
    db.commit()
    request_ids = _request_ids("scheduler-adjacent")

    assert reconcile_due_cohorts(db, now=NOW, request_id_factory=request_ids) == 1
    db.refresh(cohort)
    assert cohort.status == "active"
    db.commit()
    assert reconcile_due_cohorts(db, now=NOW, request_id_factory=request_ids) == 1
    db.refresh(cohort)
    assert cohort.status == "completed"
    db.commit()
    assert reconcile_due_cohorts(db, now=NOW, request_id_factory=request_ids) == 0

    events = list(
        db.scalars(
            select(CohortStatusEvent)
            .where(
                CohortStatusEvent.cohort_id == cohort.id,
                CohortStatusEvent.trigger == "scheduler",
            )
            .order_by(CohortStatusEvent.request_id)
        )
    )
    assert [event.request_id for event in events] == [
        "scheduler-adjacent-1",
        "scheduler-adjacent-2",
    ]
    assert [(event.old_status, event.new_status) for event in events] == [
        ("open", "active"),
        ("active", "completed"),
    ]


def test_completion_snapshot_failure_rolls_back_status_and_enrollment(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, actor = _add_actor(db, marker="snapshot-failure")
    cohort = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="snapshot failure",
        status="active",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW - timedelta(days=1),
        auto_start_enabled=False,
        auto_complete_enabled=True,
    )
    enrollment = _add_active_enrollment(db, cohort=cohort, actor=actor)
    db.commit()

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(CampSurveySnapshotService, "ensure_cycle_in_transaction", fail)
    with pytest.raises(RuntimeError, match="snapshot failed"):
        CohortService(db).transition(
            cohort.id,
            target="completed",
            trigger="manual",
            actor_user_id=actor.id,
            occurred_at=NOW,
            request_id="completion-snapshot-failure",
        )

    db.expire_all()
    assert db.get(Cohort, cohort.id).status == "active"  # type: ignore[union-attr]
    assert db.get(Enrollment, enrollment.id).status == "active"  # type: ignore[union-attr]
    assert (
        db.scalar(select(CampSurveyCycle.id).where(CampSurveyCycle.enrollment_id == enrollment.id))
        is None
    )


def test_manual_completion_creates_initial_survey_cycle_and_snapshot(db: Session) -> None:
    _, actor = _add_actor(db, marker="manual-survey")
    cohort = _add_cohort(
        db,
        actor_user_id=actor.id,
        name="manual survey",
        status="active",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW + timedelta(days=1),
        auto_start_enabled=False,
        auto_complete_enabled=False,
    )
    enrollment = _add_active_enrollment(db, cohort=cohort, actor=actor)
    db.commit()

    CohortService(db).transition(
        cohort.id,
        target="completed",
        trigger="manual",
        actor_user_id=actor.id,
        occurred_at=NOW,
        request_id="manual-completion-survey",
    )

    cycle = db.scalar(select(CampSurveyCycle).where(CampSurveyCycle.enrollment_id == enrollment.id))
    assert cycle is not None
    assert cycle.opens_at == NOW
    assert cycle.current_snapshot_version == 1
    snapshot = db.scalar(
        select(CampSurveySnapshotVersion).where(
            CampSurveySnapshotVersion.cycle_id == cycle.id,
            CampSurveySnapshotVersion.version == 1,
        )
    )
    assert snapshot is not None
    assert snapshot.created_by_user_id == actor.id


def test_scheduler_rejects_naive_now_without_opening_a_transaction(db: Session) -> None:
    with pytest.raises(ValueError, match="offset"):
        reconcile_due_cohorts(db, now=NOW.replace(tzinfo=None))
    assert not db.in_transaction()


def test_scheduler_returns_zero_when_advisory_lock_is_held(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as holder, holder.begin():
        holder.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEDULER_LOCK_KEY})
        with Session(postgres_engine) as contender:
            assert reconcile_due_cohorts(contender, now=NOW) == 0


def _persistent_due_cohorts(
    engine: Engine,
    *,
    count: int,
) -> tuple[UUID, UUID, list[UUID]]:
    with Session(engine) as setup:
        person, actor = _add_actor(setup, marker="persistent")
        cohorts = [
            _add_cohort(
                setup,
                actor_user_id=actor.id,
                name=f"persistent due {index}",
                status="open",
                starts_at=NOW - timedelta(hours=2 - index),
                ends_at=NOW + timedelta(days=1),
                auto_start_enabled=True,
                auto_complete_enabled=True,
            )
            for index in range(count)
        ]
        setup.commit()
        return person.id, actor.id, [cohort.id for cohort in cohorts]


def _cleanup_persistent_due_cohorts(
    engine: Engine,
    *,
    person_id: UUID,
    account_id: UUID,
    cohort_ids: list[UUID],
) -> None:
    with Session(engine) as db, db.begin():
        database_name = db.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str) and (
            database_name == "erp_e2e" or database_name.endswith("_test")
        )
        assert (
            db.scalar(
                text(
                    "SELECT tgenabled FROM pg_trigger WHERE tgname='audit_events_no_update' "
                    "AND tgrelid='audit_events'::regclass AND NOT tgisinternal"
                )
            )
            == "O"
        )
        # The fixture's automatic transitions have no actor FK, so account /
        # cohort deletion does not remove their polymorphic audit references.
        # Keep the append-only exception narrowly inside this isolated test
        # transaction; rollback also restores the trigger on a delete failure.
        db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
        db.execute(
            delete(AuditEvent).where(
                AuditEvent.actor_user_id.is_(None),
                AuditEvent.action == "teaching.cohort.transition",
                AuditEvent.resource_type == "cohort",
                AuditEvent.resource_id.in_(cohort_ids),
            )
        )
        db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
        db.execute(delete(CohortStatusEvent).where(CohortStatusEvent.cohort_id.in_(cohort_ids)))
        db.execute(delete(Cohort).where(Cohort.id.in_(cohort_ids)))
        db.execute(delete(UserAccount).where(UserAccount.id == account_id))
        db.execute(delete(Person).where(Person.id == person_id))


def _register_cleanup(
    request: pytest.FixtureRequest,
    engine: Engine,
    *,
    person_id: UUID,
    account_id: UUID,
    cohort_ids: list[UUID],
) -> None:
    request.addfinalizer(
        partial(
            _cleanup_persistent_due_cohorts,
            engine,
            person_id=person_id,
            account_id=account_id,
            cohort_ids=cohort_ids,
        )
    )


def test_scheduler_holds_batch_lock_until_every_candidate_finishes(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person_id, account_id, cohort_ids = _persistent_due_cohorts(
        postgres_engine,
        count=2,
    )
    _register_cleanup(
        request,
        postgres_engine,
        person_id=person_id,
        account_id=account_id,
        cohort_ids=cohort_ids,
    )
    first_transitioned = threading.Event()
    release_first = threading.Event()
    original = CohortService._reconcile_locked
    transition_count = 0
    transition_count_lock = threading.Lock()
    transition_order: list[UUID] = []

    def pause_after_first(self, cohort, **kwargs):
        nonlocal transition_count
        changed = original(self, cohort, **kwargs)
        if changed:
            with transition_count_lock:
                transition_count += 1
                transition_order.append(cohort.id)
                should_pause = transition_count == 1
            if should_pause:
                first_transitioned.set()
                if not release_first.wait(timeout=5):
                    raise TimeoutError("scheduler batch test did not release first transition")
        return changed

    monkeypatch.setattr(CohortService, "_reconcile_locked", pause_after_first)
    results: list[int] = []
    errors: list[BaseException] = []

    def run_first_scheduler() -> None:
        try:
            with Session(postgres_engine) as worker:
                results.append(
                    reconcile_due_cohorts(
                        worker,
                        now=NOW,
                        request_id_factory=_request_ids("scheduler-batch"),
                    )
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced in the test thread
            errors.append(exc)

    worker = threading.Thread(target=run_first_scheduler)
    worker.start()
    try:
        assert first_transitioned.wait(timeout=5)
        with Session(postgres_engine) as contender:
            assert reconcile_due_cohorts(contender, now=NOW) == 0
    finally:
        release_first.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert errors == []
    assert results == [2]
    assert transition_order == cohort_ids

    with Session(postgres_engine) as verification:
        statuses = set(verification.scalars(select(Cohort.status).where(Cohort.id.in_(cohort_ids))))
        event_count = verification.scalar(
            select(func.count(CohortStatusEvent.id)).where(
                CohortStatusEvent.cohort_id.in_(cohort_ids),
                CohortStatusEvent.trigger == "scheduler",
            )
        )
    assert statuses == {"active"}
    assert event_count == 2


def test_scheduler_racing_request_reconciliation_creates_one_event(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person_id, account_id, cohort_ids = _persistent_due_cohorts(
        postgres_engine,
        count=1,
    )
    _register_cleanup(
        request,
        postgres_engine,
        person_id=person_id,
        account_id=account_id,
        cohort_ids=cohort_ids,
    )
    cohort_id = cohort_ids[0]
    request_transitioned = threading.Event()
    release_request = threading.Event()
    request_thread_id: list[int] = []
    original = CohortService._reconcile_locked

    def pause_request(self, cohort, **kwargs):
        changed = original(self, cohort, **kwargs)
        if changed and threading.get_ident() in request_thread_id:
            request_transitioned.set()
            if not release_request.wait(timeout=5):
                raise TimeoutError("request reconciliation test did not release")
        return changed

    monkeypatch.setattr(CohortService, "_reconcile_locked", pause_request)
    errors: list[BaseException] = []

    def run_request_reconciliation() -> None:
        request_thread_id.append(threading.get_ident())
        try:
            with Session(postgres_engine) as worker:
                CohortService(worker).reconcile_due_cohort(
                    cohort_id,
                    now=NOW,
                    trigger="request_reconciliation",
                    request_id="request-race-winner",
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced in the test thread
            errors.append(exc)

    worker = threading.Thread(target=run_request_reconciliation)
    worker.start()
    try:
        assert request_transitioned.wait(timeout=5)
        with Session(postgres_engine) as scheduler_db:
            assert reconcile_due_cohorts(scheduler_db, now=NOW) == 0
    finally:
        release_request.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert errors == []

    with Session(postgres_engine) as verification:
        events = list(
            verification.scalars(
                select(CohortStatusEvent).where(CohortStatusEvent.cohort_id == cohort_id)
            )
        )
        audits = list(
            verification.scalars(
                select(AuditEvent).where(
                    AuditEvent.resource_type == "cohort",
                    AuditEvent.resource_id == cohort_id,
                    AuditEvent.action == "teaching.cohort.transition",
                )
            )
        )
    assert len(events) == 1
    assert events[0].trigger == "request_reconciliation"
    assert events[0].actor_user_id is None
    assert len(audits) == 1
    assert audits[0].actor_user_id is None


def _audit_baseline(engine: Engine) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        return [
            (str(identifier), hashlib.sha256(raw.encode()).hexdigest())
            for identifier, raw in connection.execute(
                text("SELECT id,to_jsonb(a)::text FROM audit_events a ORDER BY id")
            )
        ]


def _audit_trigger_state(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.scalar(
            text(
                "SELECT tgenabled FROM pg_trigger WHERE tgname='audit_events_no_update' "
                "AND tgrelid='audit_events'::regclass AND NOT tgisinternal"
            )
        )


def _seed_owned_transition(engine: Engine, request: pytest.FixtureRequest):
    person_id, account_id, cohort_ids = _persistent_due_cohorts(engine, count=1)
    owned = {"person_id": person_id, "account_id": account_id, "cohort_ids": cohort_ids}
    _register_cleanup(request, engine, **owned)
    with Session(engine) as db:
        CohortService(db).reconcile_due_cohort(
            cohort_ids[0],
            now=NOW,
            trigger="request_reconciliation",
            request_id=f"cleanup-proof-{uuid4()}",
        )
    return owned


@pytest.mark.parametrize("body_fails", [False, True], ids=["success", "body-failure"])
def test_persistent_scheduler_cleanup_preserves_prior_audits(
    postgres_engine: Engine, request: pytest.FixtureRequest, body_fails: bool
) -> None:
    original = _audit_baseline(postgres_engine)
    unrelated = _seed_owned_transition(postgres_engine, request)
    baseline = _audit_baseline(postgres_engine)
    assert len(baseline) == len(original) + 1
    owned = _seed_owned_transition(postgres_engine, request)
    assert len(_audit_baseline(postgres_engine)) == len(baseline) + 1
    assert _audit_trigger_state(postgres_engine) == "O"
    expected_failure = (
        pytest.raises(AssertionError, match="synthetic_test_body_failure")
        if body_fails
        else nullcontext()
    )
    with expected_failure:
        try:
            if body_fails:
                raise AssertionError("synthetic_test_body_failure")
        finally:
            _cleanup_persistent_due_cohorts(postgres_engine, **owned)
    assert _audit_baseline(postgres_engine) == baseline
    assert _audit_trigger_state(postgres_engine) == "O"
    with Session(postgres_engine) as verification:
        assert verification.get(Cohort, owned["cohort_ids"][0]) is None
        assert verification.get(Cohort, unrelated["cohort_ids"][0]) is not None
    _cleanup_persistent_due_cohorts(postgres_engine, **unrelated)
    assert _audit_baseline(postgres_engine) == original
    assert _audit_trigger_state(postgres_engine) == "O"


def test_persistent_scheduler_cleanup_failure_restores_audit_trigger(
    postgres_engine: Engine, request: pytest.FixtureRequest
) -> None:
    from sqlalchemy import event

    original = _audit_baseline(postgres_engine)
    owned = _seed_owned_transition(postgres_engine, request)
    before_cleanup = _audit_baseline(postgres_engine)
    assert len(before_cleanup) == len(original) + 1

    def fail_audit_delete(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM audit_events"):
            raise RuntimeError("synthetic_audit_delete_failure")

    event.listen(postgres_engine, "before_cursor_execute", fail_audit_delete)
    try:
        with pytest.raises(RuntimeError, match="synthetic_audit_delete_failure"):
            _cleanup_persistent_due_cohorts(postgres_engine, **owned)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail_audit_delete)
    assert _audit_trigger_state(postgres_engine) == "O"
    assert _audit_baseline(postgres_engine) == before_cleanup
    with Session(postgres_engine) as verification:
        assert verification.get(Cohort, owned["cohort_ids"][0]) is not None
    _cleanup_persistent_due_cohorts(postgres_engine, **owned)
    assert _audit_baseline(postgres_engine) == original
    assert _audit_trigger_state(postgres_engine) == "O"
