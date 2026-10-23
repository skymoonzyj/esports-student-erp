from __future__ import annotations

from collections import defaultdict
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Payment,
    PaymentRefund,
)
from app.identity.models import Person
from app.portal.errors import PortalPermissionDenied
from app.portal.schemas import (
    PortalPaymentStatus,
    PortalPaymentSummary,
    PortalRefundSummary,
    PortalRefundType,
    SelfPaymentsResponse,
)
from app.rbac.service import RbacService


class PortalPaymentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rbac = RbacService(db)

    def list_payments(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        page: int,
        page_size: int,
    ) -> SelfPaymentsResponse:
        decisions = self.rbac.decision_context(user_id)
        decision = decisions.decide(
            "payments.read",
            {"global", f"self:{actor_person_id}"},
        )
        if not decision.allowed:
            raise PortalPermissionDenied("payments.read/self is required")

        query = (
            select(Payment, AdmissionCase.order_number, AdmissionCase.student_person_id)
            .join(AdmissionCasePayment, AdmissionCasePayment.payment_id == Payment.id)
            .join(AdmissionCase, AdmissionCase.id == AdmissionCasePayment.admission_case_id)
            .where(Payment.payer_account_id == user_id)
        )
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        payment_rows = list(
            self.db.execute(
                query.order_by(Payment.paid_at.desc(), Payment.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).tuples()
        )
        if not payment_rows:
            return SelfPaymentsResponse(
                items=[],
                page=page,
                page_size=page_size,
                total=total,
            )

        payments = [payment for payment, _, _ in payment_rows]
        order_numbers = {payment.id: order_number for payment, order_number, _ in payment_rows}
        payment_ids = [payment.id for payment in payments]
        refunds_by_payment: dict[UUID, list[PaymentRefund]] = defaultdict(list)
        for refund in self.db.scalars(
            select(PaymentRefund)
            .where(PaymentRefund.payment_id.in_(payment_ids))
            .order_by(
                PaymentRefund.payment_id,
                PaymentRefund.refunded_at,
                PaymentRefund.id,
            )
        ):
            refunds_by_payment[refund.payment_id].append(refund)

        student_by_payment = {
            payment.id: student_id
            for payment, _, student_id in payment_rows
            if student_id is not None
        }
        student_ids = set(student_by_payment.values())
        people = {
            person.id: person
            for person in self.db.scalars(
                select(Person).where(
                    Person.id.in_(student_ids),
                    Person.deleted_at.is_(None),
                )
            )
        }
        tokens_by_student = self.rbac.person_resource_tokens_many(
            actor_person_id=actor_person_id,
            target_person_ids=student_ids,
        )
        allowed_student_names: dict[UUID, str] = {}
        for allowed_student_id, person in people.items():
            tokens = tokens_by_student[allowed_student_id]
            has_current_relationship = (
                f"self:{allowed_student_id}" in tokens
                or f"family:{allowed_student_id}" in tokens
            )
            if has_current_relationship and decisions.decide(
                "people.read",
                tokens - {"global"},
            ).allowed:
                allowed_student_names[allowed_student_id] = person.full_name

        items: list[PortalPaymentSummary] = []
        for payment in payments:
            refunds = refunds_by_payment[payment.id]
            refunded_cents = sum(refund.amount_cents for refund in refunds)
            net_cents = payment.amount_cents - refunded_cents
            status: PortalPaymentStatus = (
                "refunded"
                if net_cents == 0
                else "partially_refunded"
                if refunded_cents > 0
                else "paid"
            )
            related_student_name: str | None = None
            related_student_id = student_by_payment.get(payment.id)
            if related_student_id is not None:
                related_student_name = allowed_student_names.get(related_student_id)

            items.append(
                PortalPaymentSummary(
                    payment_id=payment.id,
                    order_number=order_numbers[payment.id],
                    source_kind=cast(Literal["api", "manual"], payment.source_kind),
                    amount_cents=payment.amount_cents,
                    currency=cast(Literal["CNY"], payment.currency),
                    paid_at=payment.paid_at,
                    refunded_cents=refunded_cents,
                    net_cents=net_cents,
                    status=status,
                    related_student_name=related_student_name,
                    refunds=[
                        PortalRefundSummary(
                            refund_id=refund.id,
                            order_number=order_numbers[payment.id],
                            amount_cents=refund.amount_cents,
                            refunded_at=refund.refunded_at,
                            refund_type=cast(PortalRefundType, refund.refund_type),
                        )
                        for refund in refunds
                    ],
                )
            )
        return SelfPaymentsResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
        )
