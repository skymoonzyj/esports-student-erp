from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment, EnrollmentEvent
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person
from app.teaching.cohort_service import CohortService
from app.teaching.models import Cohort, CohortStatusEvent, TeachingClass
from app.teaching.scheduler import SCHEDULER_LOCK_KEY, reconcile_due_cohorts

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
        email=f"scheduler-{unique}@example.test",
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


def _register_cleanup(
    request: pytest.FixtureRequest,
    engine: Engine,
    *,
    person_id: UUID,
    account_id: UUID,
    cohort_ids: list[UUID],
) -> None:
    def cleanup() -> None:
        with Session(engine) as db, db.begin():
            db.execute(
                delete(CohortStatusEvent).where(
                    CohortStatusEvent.cohort_id.in_(cohort_ids)
                )
            )
            db.execute(delete(Cohort).where(Cohort.id.in_(cohort_ids)))
            db.execute(delete(UserAccount).where(UserAccount.id == account_id))
            db.execute(delete(Person).where(Person.id == person_id))

    request.addfinalizer(cleanup)


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
        statuses = set(
            verification.scalars(
                select(Cohort.status).where(Cohort.id.in_(cohort_ids))
            )
        )
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
                select(CohortStatusEvent).where(
                    CohortStatusEvent.cohort_id == cohort_id
                )
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
