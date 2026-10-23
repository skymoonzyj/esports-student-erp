from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.admissions.payment_service import PaymentService
from app.admissions.schemas import ManualPaymentCreate, RefundCreate


def test_operation_fingerprint_normalizes_money_and_timezone_but_covers_all_business_fields():
    original = ManualPaymentCreate(
        idempotency_key=uuid4(), admission_case_id=uuid4(), amount="100.00",
        paid_at=datetime(2026, 9, 11, tzinfo=UTC), note="核对到账",
    )
    fingerprint = PaymentService._manual_fingerprint(original)
    assert PaymentService._manual_fingerprint(original.model_copy(update={
        "amount": "100.0", "idempotency_key": uuid4(),
        "paid_at": original.paid_at.astimezone(timezone(timedelta(hours=8))),
    })) == fingerprint
    for change in (
        {"amount": "100.01"}, {"admission_case_id": uuid4()},
        {"paid_at": original.paid_at + timedelta(days=1)}, {"note": "另一笔到账"},
    ):
        assert PaymentService._manual_fingerprint(original.model_copy(update=change)) != fingerprint


def test_refund_fingerprint_covers_target_date_reason_and_amount():
    payment_id = uuid4()
    original = RefundCreate(
        idempotency_key=uuid4(), amount="10.00",
        refunded_at=datetime(2026, 9, 11, tzinfo=UTC), reason="部分退款",
    )
    fingerprint = PaymentService._manual_fingerprint(original, payment_id=payment_id)
    assert PaymentService._manual_fingerprint(original, payment_id=uuid4()) != fingerprint
    for change in (
        {"amount": "11.00"}, {"reason": "另一笔退款"},
        {"refunded_at": original.refunded_at + timedelta(days=1)},
    ):
        assert PaymentService._manual_fingerprint(
            original.model_copy(update=change), payment_id=payment_id,
        ) != fingerprint


def test_manual_inputs_require_explicit_operation_keys():
    with pytest.raises(ValidationError):
        ManualPaymentCreate(admission_case_id=uuid4(), amount="1.00", paid_at=datetime.now(UTC), note="收款")
    with pytest.raises(ValidationError):
        RefundCreate(amount="1.00", refunded_at=datetime.now(UTC), reason="退款")
