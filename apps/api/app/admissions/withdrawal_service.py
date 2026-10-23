from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.errors import (
    AdmissionCaseNotReady,
    ArchivedCohortLocked,
    EnrollmentChanged,
    EnrollmentNotActive,
    EnrollmentNotFound,
    WithdrawalNothingToRefund,
    WithdrawalRefundChanged,
)
from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    EnrollmentEvent,
    EnrollmentWithdrawal,
    Payment,
    PaymentRefund,
)
from app.admissions.money import (
    RefundablePayment,
    RefundPolicy,
    calculate_withdrawal,
    cohort_participation_dates,
    split_refund,
)
from app.admissions.schedule import effective_schedule
from app.admissions.schemas import (
    RefundAllocationSchema,
    WithdrawalConfirmRequest,
    WithdrawalPreview,
    WithdrawalPreviewRequest,
)
from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.revenue_share.service import RevenueShareService
from app.teaching.models import Cohort, TeachingClass


class WithdrawalService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _preloaded_enrollment(self, enrollment_id: UUID) -> Enrollment:
        enrollment = self.db.get(Enrollment, enrollment_id)
        if enrollment is None:
            raise EnrollmentNotFound("enrollment was not found")
        if enrollment.status != "active":
            raise EnrollmentNotActive("enrollment is not active")
        return enrollment

    def _case_payments(self, admission_case_id: UUID) -> list[Payment]:
        return list(
            self.db.scalars(
                select(Payment)
                .join(
                    AdmissionCasePayment,
                    AdmissionCasePayment.payment_id == Payment.id,
                )
                .where(AdmissionCasePayment.admission_case_id == admission_case_id)
                .order_by(Payment.id)
            )
        )

    def _refundable_payments(
        self,
        payments: Sequence[Payment],
    ) -> list[RefundablePayment]:
        if not payments:
            return []
        payment_ids = [payment.id for payment in payments]
        refunded_by_payment = {
            payment_id: int(refunded_cents or 0)
            for payment_id, refunded_cents in self.db.execute(
                select(
                    PaymentRefund.payment_id,
                    func.coalesce(func.sum(PaymentRefund.amount_cents), 0),
                )
                .where(PaymentRefund.payment_id.in_(payment_ids))
                .group_by(PaymentRefund.payment_id)
            )
        }
        return [
            RefundablePayment(
                payment_id=payment.id,
                paid_at=payment.paid_at,
                refundable_cents=max(
                    payment.amount_cents - refunded_by_payment.get(payment.id, 0),
                    0,
                ),
            )
            for payment in payments
        ]

    def _calculate_preview(
        self,
        *,
        enrollment: Enrollment,
        cohort: Cohort,
        payments: Sequence[Payment],
        withdrawal_date: date,
        policy: RefundPolicy,
    ) -> WithdrawalPreview:
        refundable_payments = self._refundable_payments(payments)
        available_paid_cents = sum(
            payment.refundable_cents for payment in refundable_payments
        )
        schedule = effective_schedule(enrollment, cohort)
        starts_on, ends_on = cohort_participation_dates(
            schedule.starts_at,
            schedule.ends_at,
        )
        calculation = calculate_withdrawal(
            starts_on=starts_on,
            ends_on=ends_on,
            withdrawal_on=withdrawal_date,
            agreed_tuition_cents=enrollment.agreed_tuition_cents,
            available_paid_cents=available_paid_cents,
            policy=policy,
        )
        if calculation.refund_cents <= 0:
            raise WithdrawalNothingToRefund("withdrawal has no refundable amount")
        allocations = split_refund(calculation.refund_cents, refundable_payments)
        return WithdrawalPreview(
            withdrawal_date=withdrawal_date,
            policy=policy,
            total_days=calculation.total_days,
            remaining_days=calculation.remaining_days,
            agreed_tuition_cents=calculation.agreed_tuition_cents,
            available_paid_cents=calculation.available_paid_cents,
            refund_cents=calculation.refund_cents,
            allocations=[
                RefundAllocationSchema(
                    payment_id=allocation.payment_id,
                    amount_cents=allocation.amount_cents,
                )
                for allocation in allocations
            ],
        )

    def preview(
        self,
        enrollment_id: UUID,
        input: WithdrawalPreviewRequest,
    ) -> WithdrawalPreview:
        enrollment = self._preloaded_enrollment(enrollment_id)
        cohort = self.db.get(Cohort, enrollment.cohort_id)
        if cohort is None:
            raise EnrollmentChanged("enrollment cohort changed")
        payments = self._case_payments(enrollment.admission_case_id)
        return self._calculate_preview(
            enrollment=enrollment,
            cohort=cohort,
            payments=payments,
            withdrawal_date=input.withdrawal_date,
            policy=input.policy,
        )

    @staticmethod
    def _snapshot_matches(
        input: WithdrawalConfirmRequest,
        preview: WithdrawalPreview,
    ) -> bool:
        input_allocations = tuple(
            (item.payment_id, item.amount_cents) for item in input.allocations
        )
        preview_allocations = tuple(
            (item.payment_id, item.amount_cents) for item in preview.allocations
        )
        return (
            input.withdrawal_date == preview.withdrawal_date
            and input.policy == preview.policy
            and input.total_days == preview.total_days
            and input.remaining_days == preview.remaining_days
            and input.agreed_tuition_cents == preview.agreed_tuition_cents
            and input.available_paid_cents == preview.available_paid_cents
            and input.refund_cents == preview.refund_cents
            and input_allocations == preview_allocations
        )

    def _locked_payments(self, admission_case_id: UUID) -> list[Payment]:
        payment_ids = list(
            self.db.scalars(
                select(AdmissionCasePayment.payment_id)
                .where(AdmissionCasePayment.admission_case_id == admission_case_id)
                .order_by(AdmissionCasePayment.payment_id)
            )
        )
        if not payment_ids:
            return []
        return list(
            self.db.scalars(
                select(Payment)
                .where(Payment.id.in_(payment_ids))
                .order_by(Payment.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    @staticmethod
    def _audit_metadata(
        enrollment: Enrollment,
        *,
        refund_type: str,
    ) -> dict[str, str]:
        return {
            "student_person_id": str(enrollment.student_person_id),
            "admission_case_id": str(enrollment.admission_case_id),
            "enrollment_id": str(enrollment.id),
            "cohort_id": str(enrollment.cohort_id),
            "class_id": str(enrollment.class_id),
            "refund_type": refund_type,
        }

    def confirm(
        self,
        enrollment_id: UUID,
        input: WithdrawalConfirmRequest,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> EnrollmentWithdrawal:
        pre_enrollment = self._preloaded_enrollment(enrollment_id)
        expected_cohort_id = pre_enrollment.cohort_id
        expected_class_id = pre_enrollment.class_id
        expected_case_id = pre_enrollment.admission_case_id

        with write_transaction(self.db):
            cohort = self.db.scalar(
                select(Cohort)
                .where(Cohort.id == expected_cohort_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if cohort is None:
                raise EnrollmentChanged("enrollment cohort changed")
            if cohort.status == "archived":
                raise ArchivedCohortLocked("archived cohort admissions cannot be changed")

            teaching_class = self.db.scalar(
                select(TeachingClass)
                .where(TeachingClass.id == expected_class_id)
                .order_by(TeachingClass.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if teaching_class is None or teaching_class.cohort_id != cohort.id:
                raise EnrollmentChanged("enrollment class changed")

            enrollment = self.db.scalar(
                select(Enrollment)
                .where(Enrollment.id == enrollment_id)
                .order_by(Enrollment.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if enrollment is None:
                raise EnrollmentNotFound("enrollment was not found")
            if enrollment.status != "active":
                raise EnrollmentNotActive("enrollment is not active")
            if (
                enrollment.cohort_id != expected_cohort_id
                or enrollment.class_id != expected_class_id
                or enrollment.admission_case_id != expected_case_id
            ):
                raise EnrollmentChanged("enrollment changed during withdrawal")

            admission_case = self.db.scalar(
                select(AdmissionCase)
                .where(AdmissionCase.id == expected_case_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                admission_case is None
                or admission_case.status != "enrolled"
                or admission_case.current_enrollment_id != enrollment.id
            ):
                raise AdmissionCaseNotReady("admission case is not enrolled")

            payments = self._locked_payments(admission_case.id)
            try:
                current_preview = self._calculate_preview(
                    enrollment=enrollment,
                    cohort=cohort,
                    payments=payments,
                    withdrawal_date=input.withdrawal_date,
                    policy=input.policy,
                )
            except WithdrawalNothingToRefund as exc:
                raise WithdrawalRefundChanged(
                    "withdrawal refund changed after preview"
                ) from exc
            if not self._snapshot_matches(input, current_preview):
                raise WithdrawalRefundChanged("withdrawal refund changed after preview")

            occurred_at = self.db.scalar(select(func.now()))
            if occurred_at is None:
                raise RuntimeError("database time was unavailable")
            withdrawal = EnrollmentWithdrawal(
                enrollment_id=enrollment.id,
                admission_case_id=admission_case.id,
                withdrawal_date=input.withdrawal_date,
                refund_policy=input.policy,
                total_days_snapshot=current_preview.total_days,
                remaining_days_snapshot=current_preview.remaining_days,
                agreed_tuition_cents_snapshot=current_preview.agreed_tuition_cents,
                available_paid_cents_snapshot=current_preview.available_paid_cents,
                calculated_refund_cents=current_preview.refund_cents,
                reason=input.reason,
                confirmed_by_user_id=actor_user_id,
                confirmed_at=occurred_at,
            )
            self.db.add(withdrawal)
            self.db.flush()
            refund_type = (
                "withdrawal_prorated"
                if input.policy == "prorated_remaining_days"
                else "withdrawal_full"
            )
            self.db.add_all(
                [
                    PaymentRefund(
                        payment_id=allocation.payment_id,
                        withdrawal_id=withdrawal.id,
                        amount_cents=allocation.amount_cents,
                        refunded_at=occurred_at,
                        refund_type=refund_type,
                        reason=input.reason,
                        created_by_user_id=actor_user_id,
                    )
                    for allocation in current_preview.allocations
                ]
            )
            self.db.flush()
            enrollment.status = "cancelled"
            enrollment.cancelled_at = occurred_at
            enrollment.updated_by_user_id = actor_user_id
            admission_case.status = "ready"
            admission_case.current_enrollment_id = None
            admission_case.agreed_tuition_cents = None
            admission_case.tuition_override_reason = None
            admission_case.closed_reason = None
            admission_case.updated_by_user_id = actor_user_id
            # Production sessions disable autoflush. Persist the cancellation before
            # revenue-share queries use populate_existing, otherwise the old active
            # enrollment can be refreshed back into the identity map.
            self.db.flush()
            for allocation in current_preview.allocations:
                RevenueShareService(self.db).reconcile_payment_in_transaction(
                    allocation.payment_id
                )
            self.db.add(
                EnrollmentEvent(
                    enrollment_id=enrollment.id,
                    event_type="withdrawal_refunded",
                    old_class_id=teaching_class.id,
                    new_class_id=None,
                    old_status="active",
                    new_status="cancelled",
                    reason=input.reason,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
            )
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="admissions.enrollment.withdrawal_refund",
                resource_type="enrollment",
                resource_id=enrollment.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata=self._audit_metadata(
                    enrollment,
                    refund_type=refund_type,
                ),
            )
        return withdrawal
