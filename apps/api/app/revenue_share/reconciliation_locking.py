from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, AdmissionCasePayment, Enrollment, Payment
from app.revenue_share.errors import RevenueShareNotFound
from app.teaching.models import Cohort


class ReconciliationContextChanged(RuntimeError):
    """The caller must roll back this payment transaction before retrying."""


@dataclass(frozen=True)
class _ContextSnapshot:
    admission_case_id: UUID | None
    current_enrollment_id: UUID | None
    enrollments: tuple[tuple[UUID, UUID], ...]


def _context_snapshot(db: Session, payment_id: UUID) -> _ContextSnapshot:
    # A single statement observes the case pointer and all historical enrollment
    # relationships together. Select values rather than potentially stale ORM objects.
    rows = db.execute(
        select(
            AdmissionCasePayment.admission_case_id,
            AdmissionCase.current_enrollment_id,
            Enrollment.id,
            Enrollment.cohort_id,
        )
        .join(AdmissionCase, AdmissionCase.id == AdmissionCasePayment.admission_case_id)
        .outerjoin(Enrollment, Enrollment.admission_case_id == AdmissionCase.id)
        .where(AdmissionCasePayment.payment_id == payment_id)
        .order_by(Enrollment.id)
    ).tuples().all()
    if not rows:
        return _ContextSnapshot(None, None, ())
    return _ContextSnapshot(
        admission_case_id=rows[0][0],
        current_enrollment_id=rows[0][1],
        enrollments=tuple((row[2], row[3]) for row in rows if row[2] is not None),
    )


def _require_unchanged(db: Session, payment_id: UUID, expected: _ContextSnapshot) -> None:
    if _context_snapshot(db, payment_id) != expected:
        raise ReconciliationContextChanged("payment reconciliation context changed")


def lock_reconciliation_context(db: Session, payment_id: UUID) -> None:
    """Prelock one standalone reconciliation in the same order as payment writes.

    Call inside a fresh transaction, before taking any payment/enrollment locks,
    then call reconcile_payment_in_transaction in that same transaction. This
    helper does not commit, roll back, or apply current admission eligibility:
    closed cases and archived cohorts still permit historical reconciliation.

    Relationship changes are retried from the beginning after rollback; never
    acquire newly discovered cohort/enrollment locks after locking a payment.
    """
    expected = _context_snapshot(db, payment_id)
    cohort_ids = sorted({cohort_id for _, cohort_id in expected.enrollments})
    if cohort_ids:
        list(db.scalars(
            select(Cohort.id).where(Cohort.id.in_(cohort_ids))
            .order_by(Cohort.id).with_for_update()
        ))
        _require_unchanged(db, payment_id, expected)

    enrollment_ids = [enrollment_id for enrollment_id, _ in expected.enrollments]
    if enrollment_ids:
        list(db.scalars(
            select(Enrollment.id).where(Enrollment.id.in_(enrollment_ids))
            .order_by(Enrollment.id).with_for_update()
        ))
        _require_unchanged(db, payment_id, expected)

    if expected.admission_case_id is not None:
        db.scalar(
            select(AdmissionCase.id).where(AdmissionCase.id == expected.admission_case_id)
            .with_for_update()
        )
        _require_unchanged(db, payment_id, expected)

    payment = db.scalar(select(Payment.id).where(Payment.id == payment_id).with_for_update())
    if payment is None:
        raise RevenueShareNotFound("payment was not found")
    _require_unchanged(db, payment_id, expected)
