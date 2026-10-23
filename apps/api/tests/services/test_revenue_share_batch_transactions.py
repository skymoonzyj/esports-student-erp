from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.admissions.models import Payment, PaymentRefund
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person
from app.revenue_share.models import (
    RevenueShareLedgerEntry,
    RevenueSharePaymentStatus,
    RevenueShareReconcileQueue,
)
from app.revenue_share.service import RevenueShareService


@pytest.fixture
def committed_payments(postgres_engine: Engine):
    with Session(postgres_engine, expire_on_commit=False) as db:
        marker = uuid4().hex
        person = Person(
            full_name="批处理事务测试",
            identity_ciphertext=marker,
            identity_digest=marker,
            sex="male",
            date_of_birth=date(1990, 1, 1),
        )
        db.add(person)
        db.flush()
        actor = UserAccount(
            person_id=person.id,
            email_normalized=f"{marker}@example.test",
            password_hash="unused",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(actor)
        db.flush()
        payments = [
            Payment(
                source_kind="manual",
                payer_person_id=person.id,
                amount_cents=10_000,
                currency="CNY",
                paid_at=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(seconds=i),
                manual_note="批处理测试",
                created_by_user_id=actor.id,
            )
            for i in range(2)
        ]
        db.add_all(payments)
        db.commit()
        actor_id, person_id, payment_ids = actor.id, person.id, [p.id for p in payments]
    try:
        yield actor_id, person_id, payment_ids
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            connection.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == actor_id))
            connection.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            connection.execute(
                delete(RevenueShareLedgerEntry).where(
                    RevenueShareLedgerEntry.payment_id.in_(payment_ids)
                )
            )
            connection.execute(
                delete(RevenueSharePaymentStatus).where(
                    RevenueSharePaymentStatus.payment_id.in_(payment_ids)
                )
            )
            connection.execute(
                delete(PaymentRefund).where(PaymentRefund.payment_id.in_(payment_ids))
            )
            connection.execute(delete(Payment).where(Payment.id.in_(payment_ids)))
            connection.execute(delete(UserAccount).where(UserAccount.id == actor_id))
            connection.execute(delete(Person).where(Person.id == person_id))


def test_later_payment_failure_keeps_earlier_payment_and_audit_committed(
    postgres_engine: Engine,
    committed_payments,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id, _, payment_ids = committed_payments
    original = RevenueShareService.reconcile_payment_in_transaction

    def fail_second(self, payment_id):
        if payment_id == payment_ids[1]:
            raise RuntimeError("second payment failed")
        return original(self, payment_id)

    monkeypatch.setattr(RevenueShareService, "reconcile_payment_in_transaction", fail_second)
    with Session(postgres_engine) as db, pytest.raises(RuntimeError, match="second payment failed"):
        RevenueShareService(db).reconcile_batch(actor_user_id=actor_id, request_id="partial-batch")
    # A separate connection proves this is a durable commit, not just a savepoint.
    with Session(postgres_engine) as check:
        assert check.get(RevenueSharePaymentStatus, payment_ids[0]).state == "pending"
        assert check.get(RevenueSharePaymentStatus, payment_ids[1]) is None
        assert (
            check.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.actor_user_id == actor_id)
            )
            == 1
        )
        assert all(
            check.get(RevenueShareReconcileQueue, payment_id) is not None
            for payment_id in payment_ids
        )


def test_refund_arriving_during_batch_is_not_lost_when_queue_is_consumed(
    postgres_engine: Engine,
    committed_payments,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id, person_id, payment_ids = committed_payments
    payment_id = payment_ids[0]
    with Session(postgres_engine) as db:
        db.add(
            RevenueShareLedgerEntry(
                event_key=f"concurrent:{uuid4()}",
                payment_id=payment_id,
                entry_kind="earning",
                role_kind="additional",
                beneficiary_kind="person",
                beneficiary_person_id=person_id,
                calculation_type="percentage",
                percentage_basis_points_snapshot=1000,
                base_amount_cents=10_000,
                amount_cents=1000,
                occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
                created_by_user_id=actor_id,
            )
        )
        db.commit()
    payment_locked, refund_attempted = Event(), Event()
    original = RevenueShareService._reconcile_refunds

    def hold_payment_lock(self, payment):
        payment_locked.set()
        assert refund_attempted.wait(10)
        return original(self, payment)

    monkeypatch.setattr(RevenueShareService, "_reconcile_refunds", hold_payment_lock)

    def reconcile():
        with Session(postgres_engine) as db:
            return RevenueShareService(db).reconcile_batch(
                batch_size=1, actor_user_id=actor_id, request_id="before-concurrent-refund"
            )

    def refund():
        assert payment_locked.wait(10)
        with Session(postgres_engine) as db:
            refund_attempted.set()
            db.add(
                PaymentRefund(
                    payment_id=payment_id,
                    amount_cents=5000,
                    refunded_at=datetime(2026, 8, 2, tzinfo=UTC),
                    refund_type="manual",
                    reason="并发退款",
                    created_by_user_id=actor_id,
                )
            )
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        batch_future = pool.submit(reconcile)
        refund_future = pool.submit(refund)
        assert batch_future.result(timeout=20).processed == 1
        refund_future.result(timeout=20)
    with Session(postgres_engine) as check:
        assert check.get(RevenueShareReconcileQueue, payment_id) is not None
        assert (
            RevenueShareService(check)
            .reconcile_batch(
                batch_size=1, actor_user_id=actor_id, request_id="after-concurrent-refund"
            )
            .processed
            == 1
        )
        assert check.get(RevenueShareReconcileQueue, payment_id) is None
        assert RevenueShareService(check).balance_for("person", person_id).earned_cents == 500
