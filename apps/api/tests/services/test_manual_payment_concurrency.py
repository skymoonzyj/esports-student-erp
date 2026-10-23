from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.admissions.errors import PaymentOperationConflict
from app.admissions.models import AdmissionCase, AdmissionCasePayment, Payment, PaymentRefund
from app.admissions.payment_service import PaymentService
from app.admissions.schemas import ManualPaymentCreate, RefundCreate
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person
from app.revenue_share.models import RevenueSharePaymentStatus, RevenueShareReconcileQueue
from app.security.encryption import EnvelopeCipher, LookupDigest

NOW = datetime(2026, 9, 11, tzinfo=UTC)


def _service(db: Session) -> PaymentService:
    return PaymentService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(reversed(range(32)))),
    )


@pytest.mark.parametrize("operation", ["payment", "refund"])
@pytest.mark.parametrize("changed_payload", [False, True])
def test_concurrent_manual_money_requests_record_one_operation(
    postgres_engine: Engine, operation: str, changed_payload: bool
) -> None:
    """Real independent transactions exercise the lock, ledger, and audit writes."""
    with Session(postgres_engine, expire_on_commit=False) as db:
        marker = uuid4().hex
        person = Person(
            full_name="收退款并发测试",
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
        case = AdmissionCase(
            status="pending_match",
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        db.add(case)
        db.commit()
        person_id, actor_id, case_id = person.id, actor.id, case.id

    payment_id: UUID | None = None
    operation_key = uuid4()
    barrier = Barrier(2)

    def submit(index: int) -> UUID | str:
        amount = "20.00" if changed_payload and index == 1 else "10.00"
        with Session(postgres_engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            service = _service(db)
            try:
                if operation == "payment":
                    return service.create_manual_payment(
                        ManualPaymentCreate(
                            idempotency_key=operation_key,
                            admission_case_id=case_id,
                            amount=amount,
                            paid_at=NOW,
                            note="同一收款操作重试",
                        ),
                        actor_user_id=actor_id,
                        request_id=f"manual-concurrent-{index}",
                    ).payment.id
                assert payment_id is not None
                return service.create_refund(
                    payment_id,
                    RefundCreate(
                        idempotency_key=operation_key,
                        amount=amount,
                        refunded_at=NOW,
                        reason="同一退款操作重试",
                    ),
                    actor_user_id=actor_id,
                    request_id=f"refund-concurrent-{index}",
                ).id
            except PaymentOperationConflict:
                return "changed_payload"

    try:
        if operation == "refund":
            with Session(postgres_engine, expire_on_commit=False) as db:
                payment_id = _service(db).create_manual_payment(
                    ManualPaymentCreate(
                        idempotency_key=uuid4(),
                        admission_case_id=case_id,
                        amount="100.00",
                        paid_at=NOW,
                        note="并发退款的原始收款",
                    ),
                    actor_user_id=actor_id,
                    request_id="refund-concurrent-original",
                ).payment.id
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, index) for index in range(2)]
            results = [future.result(timeout=20) for future in futures]
        if changed_payload:
            assert results.count("changed_payload") == 1
        else:
            assert results[0] == results[1]
            assert isinstance(results[0], UUID)
        model = Payment if operation == "payment" else PaymentRefund
        action = (
            "admissions.payment.create_manual"
            if operation == "payment"
            else "admissions.payment.refund"
        )
        with Session(postgres_engine) as db:
            assert db.scalar(
                select(func.count()).select_from(model).where(
                    model.created_by_user_id == actor_id,
                    model.idempotency_key == operation_key,
                )
            ) == 1
            assert db.scalar(
                select(func.count()).select_from(AuditEvent).where(
                    AuditEvent.actor_user_id == actor_id,
                    AuditEvent.action == action,
                )
            ) == 1
    finally:
        with postgres_engine.begin() as connection:
            payment_ids = select(Payment.id).where(Payment.created_by_user_id == actor_id)
            connection.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            connection.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == actor_id))
            connection.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            connection.execute(delete(RevenueShareReconcileQueue).where(
                RevenueShareReconcileQueue.payment_id.in_(payment_ids)
            ))
            connection.execute(delete(RevenueSharePaymentStatus).where(
                RevenueSharePaymentStatus.payment_id.in_(payment_ids)
            ))
            connection.execute(delete(PaymentRefund).where(
                PaymentRefund.created_by_user_id == actor_id
            ))
            connection.execute(delete(AdmissionCasePayment).where(
                AdmissionCasePayment.admission_case_id == case_id
            ))
            connection.execute(delete(Payment).where(Payment.created_by_user_id == actor_id))
            connection.execute(delete(AdmissionCase).where(AdmissionCase.id == case_id))
            connection.execute(delete(UserAccount).where(UserAccount.id == actor_id))
            connection.execute(delete(Person).where(Person.id == person_id))
