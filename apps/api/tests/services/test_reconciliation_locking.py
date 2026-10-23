from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, event, select, text, update
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, AdmissionCasePayment, Enrollment, Payment
from app.auth.models import UserAccount
from app.identity.models import Person
from app.revenue_share import reconciliation_locking as locking
from app.teaching.models import Cohort, TeachingClass

NOW = datetime(2026, 8, 15, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class LockingScenario:
    actor_id: UUID
    people: tuple[UUID, UUID]
    case_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    class_id: UUID
    payment_id: UUID


@pytest.fixture
def locking_scenario(postgres_engine: Engine):
    with Session(postgres_engine, expire_on_commit=False) as db, db.begin():
        people = [Person(full_name=name, identity_ciphertext=uuid4().hex,
                         identity_digest=uuid4().hex, sex="male", date_of_birth=date(1990, 1, 1))
                  for name in ("分成锁序测试管理员", "分成锁序测试学员")]
        db.add_all(people)
        db.flush()
        actor = UserAccount(person_id=people[0].id, email_normalized=f"{uuid4().hex}@example.test",
                            password_hash="unused", status="active", force_password_change=False,
                            failed_login_count=0)
        db.add(actor)
        db.flush()
        cohort = Cohort(name=uuid4().hex, starts_at=NOW, ends_at=NOW + timedelta(days=21),
                        default_check_in_at=NOW, default_check_out_at=NOW + timedelta(days=21),
                        status="active", created_by_user_id=actor.id)
        db.add(cohort)
        db.flush()
        teaching_class = TeachingClass(cohort_id=cohort.id, project_id=PROJECT_ID,
                                       name=uuid4().hex, class_type="standard", capacity=20,
                                       default_tuition_cents=10000, is_active=True,
                                       created_by_user_id=actor.id)
        case = AdmissionCase(student_person_id=people[1].id, status="ready",
                             agreed_tuition_cents=10000, created_by_user_id=actor.id)
        payment = Payment(source_kind="manual", amount_cents=10000, currency="CNY", paid_at=NOW,
                          manual_note="锁序测试", created_by_user_id=actor.id)
        db.add_all((teaching_class, case, payment))
        db.flush()
        enrollment = Enrollment(admission_case_id=case.id, student_person_id=people[1].id,
                                cohort_id=cohort.id, class_id=teaching_class.id,
                                agreed_tuition_cents=10000, status="active", confirmed_at=NOW,
                                created_by_user_id=actor.id)
        db.add(enrollment)
        db.flush()
        case.current_enrollment_id = enrollment.id
        case.status = "enrolled"
        db.add(AdmissionCasePayment(admission_case_id=case.id, payment_id=payment.id,
                                    attached_by_user_id=actor.id))
        data = LockingScenario(actor.id, (people[0].id, people[1].id), case.id, enrollment.id,
                               cohort.id, teaching_class.id, payment.id)
    try:
        yield data
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(update(AdmissionCase).where(AdmissionCase.id == data.case_id)
                               .values(current_enrollment_id=None, status="ready"))
            connection.execute(delete(AdmissionCasePayment).where(AdmissionCasePayment.payment_id == data.payment_id))
            connection.execute(delete(Payment).where(Payment.id == data.payment_id))
            connection.execute(delete(Enrollment).where(Enrollment.admission_case_id == data.case_id))
            connection.execute(delete(AdmissionCase).where(AdmissionCase.id == data.case_id))
            connection.execute(delete(TeachingClass).where(TeachingClass.id == data.class_id))
            connection.execute(delete(Cohort).where(Cohort.id == data.cohort_id))
            connection.execute(delete(UserAccount).where(UserAccount.id == data.actor_id))
            connection.execute(delete(Person).where(Person.id.in_(data.people)))


def test_reconciliation_waiting_for_business_context_does_not_lock_payment_first(
    postgres_engine: Engine, locking_scenario: LockingScenario,
) -> None:
    data = locking_scenario
    context_requested = threading.Event()

    def before_statement(connection, cursor, statement, parameters, context, executemany):
        if connection.info.get("reconciliation_test") and "FROM cohorts" in statement and "FOR UPDATE" in statement:
            context_requested.set()

    def reconcile() -> None:
        with Session(postgres_engine) as db, db.begin():
            db.connection().info["reconciliation_test"] = True
            try:
                db.execute(text("SET LOCAL lock_timeout = '5s'"))
                locking.lock_reconciliation_context(db, data.payment_id)
            finally:
                db.connection().info.pop("reconciliation_test", None)

    event.listen(postgres_engine, "before_cursor_execute", before_statement)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(postgres_engine) as business, business.begin():
                business.scalar(select(Cohort.id).where(Cohort.id == data.cohort_id).with_for_update())
                business.scalar(select(Enrollment.id).where(Enrollment.id == data.enrollment_id).with_for_update())
                future = pool.submit(reconcile)
                assert context_requested.wait(timeout=5)
                # A reverse Payment→Enrollment lock order would make this fail immediately.
                assert business.scalar(select(Payment.id).where(Payment.id == data.payment_id)
                                       .with_for_update(nowait=True)) == data.payment_id
            future.result(timeout=10)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", before_statement)


@pytest.mark.parametrize("change", ["pointer", "new_enrollment", "attachment"])
def test_committed_context_changes_require_rollback_and_can_retry(
    postgres_engine: Engine, locking_scenario: LockingScenario, monkeypatch, change: str,
) -> None:
    data = locking_scenario
    if change == "attachment":
        with postgres_engine.begin() as connection:
            connection.execute(delete(AdmissionCasePayment).where(AdmissionCasePayment.payment_id == data.payment_id))
    original = locking._context_snapshot
    changed = False

    def snapshot_then_change(db: Session, payment_id: UUID):
        nonlocal changed
        snapshot = original(db, payment_id)
        if not changed:
            changed = True
            with Session(postgres_engine) as other, other.begin():
                if change == "pointer":
                    other.execute(update(AdmissionCase).where(AdmissionCase.id == data.case_id)
                                  .values(status="ready", current_enrollment_id=None))
                elif change == "new_enrollment":
                    other.add(Enrollment(admission_case_id=data.case_id, student_person_id=data.people[1],
                                         cohort_id=data.cohort_id, class_id=data.class_id,
                                         agreed_tuition_cents=10000, status="cancelled", confirmed_at=NOW,
                                         cancelled_at=NOW + timedelta(days=1), created_by_user_id=data.actor_id))
                else:
                    other.add(AdmissionCasePayment(admission_case_id=data.case_id, payment_id=data.payment_id,
                                                   attached_by_user_id=data.actor_id))
        return snapshot

    monkeypatch.setattr(locking, "_context_snapshot", snapshot_then_change)
    with Session(postgres_engine) as db:
        with pytest.raises(locking.ReconciliationContextChanged), db.begin():
            locking.lock_reconciliation_context(db, data.payment_id)
        assert not db.in_transaction()
        with db.begin():
            locking.lock_reconciliation_context(db, data.payment_id)


def test_historical_closed_case_and_archived_cohort_still_allow_prelocking(
    postgres_engine: Engine, locking_scenario: LockingScenario,
) -> None:
    data = locking_scenario
    with Session(postgres_engine) as db, db.begin():
        db.execute(update(AdmissionCase).where(AdmissionCase.id == data.case_id)
                   .values(status="closed", current_enrollment_id=None, closed_reason="历史对账"))
        db.execute(update(Cohort).where(Cohort.id == data.cohort_id).values(status="archived"))
    with Session(postgres_engine) as db, db.begin():
        locking.lock_reconciliation_context(db, data.payment_id)
