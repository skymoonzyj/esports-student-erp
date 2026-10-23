from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admissions.credential_service import VerifiedMachineRequest
from app.admissions.errors import (
    AdmissionCaseClosed,
    AdmissionCaseNotFound,
    AdmissionStudentNotFound,
    AdmissionVerifiedContactRequired,
    ArchivedCohortLocked,
    PaymentConflictNotFound,
    PaymentConflictNotOpen,
    PaymentImportConflict,
    PaymentNotFound,
    PaymentOperationConflict,
    PaymentSourceNotAllowed,
    RefundExceedsPaidAmount,
    ServiceAccountDisabled,
    ServiceAccountNotFound,
)
from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    Payment,
    PaymentConflict,
    PaymentImportEvent,
    PaymentRefund,
    ServiceAccount,
)
from app.admissions.schemas import ManualPaymentCreate, PaymentImportRequest, RefundCreate
from app.audit.service import AuditService
from app.auth.contact_readiness import VerifiedContactPolicy, VerifiedContactRequired
from app.auth.models import UserAccount
from app.core.transactions import write_transaction
from app.identity.models import Person, StudentProfile
from app.revenue_share.service import RevenueShareService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import Cohort

_IMPORT_PAYLOAD_FIELD = "payment_import_events.raw_payload"
_CONFLICT_PAYLOAD_FIELD = "payment_conflicts.attempted_raw_payload"


@dataclass(frozen=True)
class PaymentImportResult:
    event: PaymentImportEvent
    payment: Payment
    admission_case: AdmissionCase
    created: bool


@dataclass(frozen=True)
class ManualPaymentResult:
    payment: Payment
    admission_case: AdmissionCase


PaymentStatus = Literal["unpaid", "partial", "paid", "overpaid", "refunded"]


@dataclass(frozen=True)
class PaymentSummary:
    paid_cents: int
    refunded_cents: int
    net_paid_cents: int
    agreed_tuition_cents: int | None
    due_cents: int
    overpaid_cents: int
    status: PaymentStatus


@dataclass(frozen=True)
class PaymentListItem:
    payment: Payment
    admission_case_id: UUID
    order_number: str
    available_refund_cents: int


@dataclass(frozen=True)
class _PayerMatch:
    account_id: UUID | None
    person_id: UUID | None
    student_id: UUID | None
    ambiguous: bool


@dataclass(frozen=True)
class _ImportOutcome:
    result: PaymentImportResult | None = None
    conflict_id: UUID | None = None


class PaymentService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup

    def _lock_case_context(self, admission_case_id: UUID) -> AdmissionCase:
        enrollment_context = list(
            self.db.execute(
                select(Enrollment.id, Enrollment.cohort_id).where(
                    Enrollment.admission_case_id == admission_case_id
                )
            )
        )
        cohort_ids = sorted({row.cohort_id for row in enrollment_context})
        cohorts = (
            list(
                self.db.scalars(
                    select(Cohort)
                    .where(Cohort.id.in_(cohort_ids))
                    .order_by(Cohort.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if cohort_ids
            else []
        )
        if any(cohort.status == "archived" for cohort in cohorts):
            raise ArchivedCohortLocked("archived cohort admissions cannot be changed")
        enrollment_ids = sorted(row.id for row in enrollment_context)
        if enrollment_ids:
            list(
                self.db.scalars(
                    select(Enrollment)
                    .where(Enrollment.id.in_(enrollment_ids))
                    .order_by(Enrollment.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
        admission_case = self.db.scalar(
            select(AdmissionCase)
            .where(AdmissionCase.id == admission_case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if admission_case is None:
            raise AdmissionCaseNotFound("admission case was not found")
        if admission_case.status == "closed":
            raise AdmissionCaseClosed("closed admission case cannot receive payments")
        return admission_case

    def _lock_student(self, student_person_id: UUID) -> Person:
        student = self.db.scalar(
            select(Person)
            .join(StudentProfile, StudentProfile.person_id == Person.id)
            .where(
                Person.id == student_person_id,
                Person.deleted_at.is_(None),
            )
            .with_for_update(of=Person)
            .execution_options(populate_existing=True)
        )
        if student is None:
            raise AdmissionStudentNotFound("student was not found")
        return student

    def _require_verified_phone(self, student_person_id: UUID) -> None:
        try:
            VerifiedContactPolicy(self.db).require_verified_contact_for_person(student_person_id)
        except VerifiedContactRequired as exc:
            raise AdmissionVerifiedContactRequired(
                "student requires a verified email or phone"
            ) from exc

    def _lock_manual_operation(self, kind: str, actor_user_id: UUID, operation_key: UUID) -> None:
        digest = hashlib.sha256(f"admissions:{kind}:{actor_user_id}:{operation_key}".encode()).digest()
        self.db.execute(select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], "big", signed=True))))

    @staticmethod
    def _manual_fingerprint(input: ManualPaymentCreate | RefundCreate, *, payment_id: UUID | None = None) -> str:
        payload = input.model_dump(mode="json", exclude={"idempotency_key", "amount"})
        payload["amount_cents"] = input.amount_cents
        if isinstance(input, ManualPaymentCreate):
            payload["paid_at"] = input.paid_at.astimezone(UTC).isoformat()
        else:
            payload["payment_id"] = str(payment_id)
            payload["refunded_at"] = input.refunded_at.astimezone(UTC).isoformat()
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    def create_manual_payment(
        self,
        input: ManualPaymentCreate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> ManualPaymentResult:
        with write_transaction(self.db):
            self._lock_manual_operation("payment", actor_user_id, input.idempotency_key)
            fingerprint = self._manual_fingerprint(input)
            existing = self.db.scalar(
                select(Payment).where(
                    Payment.created_by_user_id == actor_user_id,
                    Payment.idempotency_key == input.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise PaymentOperationConflict("manual payment retry changed")
                case = self.db.get(AdmissionCase, self._case_id_for_payment(existing.id))
                if case is None:
                    raise AdmissionCaseNotFound("admission case was not found")
                return ManualPaymentResult(existing, case)
            if input.admission_case_id is not None:
                admission_case = self._lock_case_context(input.admission_case_id)
                student_id = admission_case.student_person_id
                if student_id is not None:
                    self._lock_student(student_id)
                    if admission_case.application_source != "self_service":
                        self._require_verified_phone(student_id)
            else:
                if input.student_person_id is None:  # pragma: no cover - schema invariant
                    raise ValueError("manual payment target is required")
                student = self._lock_student(input.student_person_id)
                student_id = student.id
                self._require_verified_phone(student_id)
                admission_case = AdmissionCase(
                    student_person_id=student.id,
                    status="ready",
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
                self.db.add(admission_case)
                self.db.flush()
            payer_account_id = self.db.scalar(
                select(UserAccount.id).where(UserAccount.person_id == student_id)
            )
            payment = Payment(
                source_kind="manual",
                source_system=None,
                external_order_id=None,
                payer_account_id=payer_account_id,
                payer_person_id=student_id,
                amount_cents=input.amount_cents,
                currency="CNY",
                paid_at=input.paid_at,
                manual_note=input.note,
                idempotency_key=input.idempotency_key,
                request_fingerprint=fingerprint,
                created_by_user_id=actor_user_id,
            )
            self.db.add(payment)
            self.db.flush()
            self.db.add(
                AdmissionCasePayment(
                    admission_case_id=admission_case.id,
                    payment_id=payment.id,
                    attached_by_user_id=actor_user_id,
                )
            )
            self.db.flush()
            RevenueShareService(self.db).reconcile_payment_in_transaction(payment.id)
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="admissions.payment.create_manual",
                resource_type="payment",
                resource_id=payment.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "admission_case_id": str(admission_case.id),
                    "payment_id": str(payment.id),
                },
            )
            result = ManualPaymentResult(payment, admission_case)
        return result

    def _case_id_for_payment(self, payment_id: UUID) -> UUID:
        admission_case_id = self.db.scalar(
            select(AdmissionCasePayment.admission_case_id).where(
                AdmissionCasePayment.payment_id == payment_id
            )
        )
        if admission_case_id is None:
            raise PaymentNotFound("payment was not found")
        return admission_case_id

    def create_refund(
        self,
        payment_id: UUID,
        input: RefundCreate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> PaymentRefund:
        admission_case_id = self._case_id_for_payment(payment_id)
        with write_transaction(self.db):
            self._lock_manual_operation("refund", actor_user_id, input.idempotency_key)
            fingerprint = self._manual_fingerprint(input, payment_id=payment_id)
            existing = self.db.scalar(
                select(PaymentRefund).where(
                    PaymentRefund.created_by_user_id == actor_user_id,
                    PaymentRefund.idempotency_key == input.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise PaymentOperationConflict("manual refund retry changed")
                return existing
            self._lock_case_context(admission_case_id)
            payment = self.db.scalar(
                select(Payment).where(Payment.id == payment_id).with_for_update()
                .execution_options(populate_existing=True)
            )
            if payment is None:
                raise PaymentNotFound("payment was not found")
            refunded_cents = self.db.scalar(
                select(func.coalesce(func.sum(PaymentRefund.amount_cents), 0)).where(
                    PaymentRefund.payment_id == payment_id
                )
            )
            refunded_cents = int(refunded_cents or 0)
            if refunded_cents + input.amount_cents > payment.amount_cents:
                raise RefundExceedsPaidAmount("refund exceeds the original payment")
            refund = PaymentRefund(
                payment_id=payment.id,
                withdrawal_id=None,
                amount_cents=input.amount_cents,
                refunded_at=input.refunded_at,
                refund_type="manual",
                reason=input.reason,
                idempotency_key=input.idempotency_key,
                request_fingerprint=fingerprint,
                created_by_user_id=actor_user_id,
            )
            self.db.add(refund)
            self.db.flush()
            RevenueShareService(self.db).reconcile_payment_in_transaction(payment.id)
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="admissions.payment.refund",
                resource_type="payment",
                resource_id=payment.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "payment_id": str(payment.id),
                    "refund_id": str(refund.id),
                    "refund_type": "manual",
                },
            )
        return refund

    def available_refund_cents(self, payment_id: UUID) -> int:
        payment = self.db.get(Payment, payment_id)
        if payment is None:
            raise PaymentNotFound("payment was not found")
        refunded_cents = self.db.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount_cents), 0)).where(
                PaymentRefund.payment_id == payment_id
            )
        )
        return payment.amount_cents - int(refunded_cents or 0)

    def payment_summary(self, admission_case_id: UUID) -> PaymentSummary:
        admission_case = self.db.get(AdmissionCase, admission_case_id)
        if admission_case is None:
            raise AdmissionCaseNotFound("admission case was not found")
        paid_cents = int(
            self.db.scalar(
                select(func.coalesce(func.sum(Payment.amount_cents), 0))
                .join(
                    AdmissionCasePayment,
                    AdmissionCasePayment.payment_id == Payment.id,
                )
                .where(AdmissionCasePayment.admission_case_id == admission_case_id)
            )
            or 0
        )
        refunded_cents = int(
            self.db.scalar(
                select(func.coalesce(func.sum(PaymentRefund.amount_cents), 0))
                .join(Payment, Payment.id == PaymentRefund.payment_id)
                .join(
                    AdmissionCasePayment,
                    AdmissionCasePayment.payment_id == Payment.id,
                )
                .where(AdmissionCasePayment.admission_case_id == admission_case_id)
            )
            or 0
        )
        net_paid_cents = paid_cents - refunded_cents
        tuition = admission_case.agreed_tuition_cents
        due_cents = max((tuition or 0) - net_paid_cents, 0)
        tuition_for_overpayment = tuition if tuition is not None else net_paid_cents
        overpaid_cents = max(net_paid_cents - tuition_for_overpayment, 0)
        if paid_cents == 0:
            status: PaymentStatus = "unpaid"
        elif net_paid_cents == 0:
            status = "refunded"
        elif tuition is None or net_paid_cents == tuition:
            status = "paid"
        elif net_paid_cents < tuition:
            status = "partial"
        else:
            status = "overpaid"
        return PaymentSummary(
            paid_cents=paid_cents,
            refunded_cents=refunded_cents,
            net_paid_cents=net_paid_cents,
            agreed_tuition_cents=tuition,
            due_cents=due_cents,
            overpaid_cents=overpaid_cents,
            status=status,
        )

    def list_payments(
        self,
        *,
        source_kind: str | None = None,
        source_system: str | None = None,
        order_number: str | None = None,
        payer_phone_digest: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        visible_class_ids: frozenset[UUID] | None = None,
        excluded_class_ids: frozenset[UUID] = frozenset(),
        page: int,
        page_size: int,
    ) -> tuple[list[PaymentListItem], int]:
        if page < 1 or page_size < 1:
            raise ValueError("page and page size must be positive")
        base = (
            select(Payment, AdmissionCase.id, AdmissionCase.order_number)
            .join(AdmissionCasePayment, AdmissionCasePayment.payment_id == Payment.id)
            .join(AdmissionCase, AdmissionCase.id == AdmissionCasePayment.admission_case_id)
        )
        if visible_class_ids is not None:
            visible_case_ids = select(Enrollment.admission_case_id).where(
                Enrollment.class_id.in_(visible_class_ids)
            )
            base = base.where(
                AdmissionCasePayment.admission_case_id.in_(visible_case_ids)
            )
        elif excluded_class_ids:
            excluded_case_ids = select(Enrollment.admission_case_id).where(
                Enrollment.class_id.in_(excluded_class_ids)
            )
            base = base.where(
                AdmissionCasePayment.admission_case_id.not_in(excluded_case_ids)
            )
        if source_kind is not None:
            base = base.where(Payment.source_kind == source_kind)
        if source_system is not None:
            base = base.where(Payment.source_system == source_system.strip())
        if order_number is not None and order_number.strip():
            base = base.where(AdmissionCase.order_number == order_number.strip().upper())
        if payer_phone_digest is not None:
            base = base.join(Person, Person.id == Payment.payer_person_id).where(
                Person.phone_digest == payer_phone_digest
            )
        if date_from is not None:
            base = base.where(Payment.paid_at >= date_from)
        if date_to is not None:
            base = base.where(Payment.paid_at < date_to)
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = self.db.execute(
            base.order_by(Payment.paid_at.desc(), Payment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            PaymentListItem(
                payment=payment,
                admission_case_id=admission_case_id,
                order_number=case_order_number,
                available_refund_cents=self.available_refund_cents(payment.id),
            )
            for payment, admission_case_id, case_order_number in rows
        ]
        return items, total

    def list_conflicts(
        self,
        *,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PaymentConflict], int]:
        if page < 1 or page_size < 1:
            raise ValueError("page and page size must be positive")
        query = select(PaymentConflict)
        if status is not None:
            query = query.where(PaymentConflict.status == status)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        conflicts = list(
            self.db.scalars(
                query.order_by(
                    PaymentConflict.created_at.desc(),
                    PaymentConflict.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return conflicts, total

    def resolve_conflict(
        self,
        conflict_id: UUID,
        *,
        action: str,
        note: str,
        actor_user_id: UUID,
        request_id: str,
    ) -> PaymentConflict:
        normalized_note = note.strip()
        if action not in {"resolved", "dismissed"} or not normalized_note:
            raise ValueError("conflict resolution action and note are required")
        with write_transaction(self.db):
            conflict = self.db.scalar(
                select(PaymentConflict)
                .where(PaymentConflict.id == conflict_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if conflict is None:
                raise PaymentConflictNotFound("payment conflict was not found")
            if conflict.status != "open":
                raise PaymentConflictNotOpen("payment conflict is already resolved")
            conflict.status = action
            conflict.resolution_note = normalized_note
            conflict.resolved_by_user_id = actor_user_id
            conflict.resolved_at = func.now()
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="admissions.payment_conflict.resolve",
                resource_type="payment_conflict",
                resource_id=conflict.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={},
            )
        return conflict

    @staticmethod
    def _payload_digest(input: PaymentImportRequest) -> str:
        canonical = json.dumps(
            input.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _decode_raw_body(raw_body: bytes) -> str:
        try:
            return raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("payment import body must be UTF-8 JSON") from exc

    def _require_source(
        self,
        *,
        service_account_id: UUID,
        source_system: str,
    ) -> ServiceAccount:
        account = self.db.scalar(
            select(ServiceAccount)
            .where(ServiceAccount.id == service_account_id)
            .with_for_update()
        )
        if account is None:
            raise ServiceAccountNotFound("service account was not found")
        if not account.is_active:
            raise ServiceAccountDisabled("service account is disabled")
        if account.source_system != source_system:
            raise PaymentSourceNotAllowed("credential cannot import for this source")
        return account

    def _match_payer(self, phone_digest: str) -> _PayerMatch:
        matched_people = list(
            self.db.scalars(
                select(Person)
                .where(Person.phone_digest == phone_digest, Person.deleted_at.is_(None))
                .order_by(Person.id)
            )
        )
        person_ids = {person.id for person in matched_people}
        if not person_ids:
            return _PayerMatch(None, None, None, False)
        if len(person_ids) > 1:
            return _PayerMatch(None, None, None, True)

        person_id = next(iter(person_ids))
        matched_account = self.db.scalar(
            select(UserAccount).where(UserAccount.person_id == person_id)
        )
        student_id = self.db.scalar(
            select(StudentProfile.person_id).where(StudentProfile.person_id == person_id)
        )
        return _PayerMatch(
            matched_account.id if matched_account is not None else None,
            person_id,
            student_id,
            False,
        )

    def _load_result(
        self,
        event: PaymentImportEvent,
        *,
        created: bool,
    ) -> PaymentImportResult:
        if event.payment_id is None:
            raise RuntimeError("payment import event has no payment")
        payment = self.db.get(Payment, event.payment_id)
        case_payment = self.db.scalar(
            select(AdmissionCasePayment).where(
                AdmissionCasePayment.payment_id == event.payment_id
            )
        )
        if payment is None or case_payment is None:
            raise RuntimeError("payment import result is incomplete")
        admission_case = self.db.get(AdmissionCase, case_payment.admission_case_id)
        if admission_case is None:
            raise RuntimeError("payment import admission case is missing")
        return PaymentImportResult(event, payment, admission_case, created)

    def _existing_events(self, input: PaymentImportRequest) -> list[PaymentImportEvent]:
        return list(
            self.db.scalars(
                select(PaymentImportEvent)
                .where(
                    or_(
                        (
                            PaymentImportEvent.source_system == input.source_system
                        )
                        & (
                            PaymentImportEvent.external_order_id
                            == input.external_order_id
                        ),
                        PaymentImportEvent.idempotency_key == input.idempotency_key,
                    )
                )
                .order_by(PaymentImportEvent.id)
                .with_for_update()
            )
        )

    def _append_conflict(
        self,
        *,
        original: PaymentImportEvent,
        conflict_type: str,
        payload_digest: str,
        raw_body_text: str,
    ) -> UUID:
        conflict = PaymentConflict(
            original_import_event_id=original.id,
            conflict_type=conflict_type,
            attempted_payload_digest=payload_digest,
            attempted_raw_payload_ciphertext=self.cipher.encrypt(
                raw_body_text,
                field_name=_CONFLICT_PAYLOAD_FIELD,
            ),
            status="open",
        )
        self.db.add(conflict)
        self.db.flush()
        return conflict.id

    def _resolve_existing(
        self,
        *,
        input: PaymentImportRequest,
        payload_digest: str,
        raw_body_text: str,
    ) -> _ImportOutcome | None:
        existing = self._existing_events(input)
        if not existing:
            return None
        exact = next(
            (event for event in existing if event.payload_digest == payload_digest),
            None,
        )
        if exact is not None and all(
            event.id == exact.id or event.payload_digest == payload_digest
            for event in existing
        ):
            return _ImportOutcome(result=self._load_result(exact, created=False))

        source_order = next(
            (
                event
                for event in existing
                if event.source_system == input.source_system
                and event.external_order_id == input.external_order_id
            ),
            None,
        )
        original = source_order or existing[0]
        conflict_type = (
            "external_order_payload_mismatch"
            if source_order is not None
            else "idempotency_payload_mismatch"
        )
        return _ImportOutcome(
            conflict_id=self._append_conflict(
                original=original,
                conflict_type=conflict_type,
                payload_digest=payload_digest,
                raw_body_text=raw_body_text,
            )
        )

    def _create_import(
        self,
        *,
        input: PaymentImportRequest,
        machine: VerifiedMachineRequest,
        phone_digest: str,
        payload_digest: str,
        raw_body_text: str,
    ) -> _ImportOutcome:
        payer = self._match_payer(phone_digest)
        payment = Payment(
            source_kind="api",
            source_system=input.source_system,
            external_order_id=input.external_order_id,
            payer_account_id=payer.account_id,
            payer_person_id=payer.person_id,
            amount_cents=input.amount_cents,
            currency=input.currency,
            paid_at=input.paid_at,
            manual_note=None,
            created_by_user_id=None,
        )
        self.db.add(payment)
        self.db.flush()
        admission_case = AdmissionCase(
            student_person_id=payer.student_id,
            status="ready" if payer.student_id is not None else "pending_match",
            created_by_user_id=None,
            updated_by_user_id=None,
        )
        self.db.add(admission_case)
        self.db.flush()
        self.db.add(
            AdmissionCasePayment(
                admission_case_id=admission_case.id,
                payment_id=payment.id,
                attached_by_user_id=None,
            )
        )
        event = PaymentImportEvent(
            service_account_id=machine.service_account_id,
            source_system=input.source_system,
            external_order_id=input.external_order_id,
            idempotency_key=input.idempotency_key,
            payer_phone_digest=phone_digest,
            payload_digest=payload_digest,
            raw_payload_ciphertext=self.cipher.encrypt(
                raw_body_text,
                field_name=_IMPORT_PAYLOAD_FIELD,
            ),
            processing_status=("processed" if payer.student_id is not None else "pending_match"),
            payment_id=payment.id,
        )
        self.db.add(event)
        self.db.flush()
        if payer.ambiguous:
            event.processing_status = "conflict"
            conflict_id = self._append_conflict(
                original=event,
                conflict_type="ambiguous_payer",
                payload_digest=payload_digest,
                raw_body_text=raw_body_text,
            )
            event.conflict_id = conflict_id
            return _ImportOutcome(conflict_id=conflict_id)
        return _ImportOutcome(
            result=PaymentImportResult(event, payment, admission_case, True)
        )

    def _import_in_transaction(
        self,
        *,
        input: PaymentImportRequest,
        machine: VerifiedMachineRequest,
        phone_digest: str,
        payload_digest: str,
        raw_body_text: str,
    ) -> _ImportOutcome:
        self._require_source(
            service_account_id=machine.service_account_id,
            source_system=input.source_system,
        )
        existing = self._resolve_existing(
            input=input,
            payload_digest=payload_digest,
            raw_body_text=raw_body_text,
        )
        if existing is not None:
            return existing
        return self._create_import(
            input=input,
            machine=machine,
            phone_digest=phone_digest,
            payload_digest=payload_digest,
            raw_body_text=raw_body_text,
        )

    def _audit_machine_import(
        self,
        outcome: _ImportOutcome,
        *,
        machine: VerifiedMachineRequest,
        request_id: str,
    ) -> None:
        metadata = {
            "service_account_id": str(machine.service_account_id),
            "credential_id": str(machine.credential_id),
        }
        if outcome.result is not None:
            result = outcome.result
            metadata.update(
                {
                    "import_event_id": str(result.event.id),
                    "payment_id": str(result.payment.id),
                }
            )
            AuditService(self.db).append(
                actor_user_id=None,
                action="admissions.payment_import.process",
                resource_type="payment_import_event",
                resource_id=result.event.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata=metadata,
            )
            return
        if outcome.conflict_id is None:  # pragma: no cover - exhaustive outcome guard
            raise RuntimeError("payment import audit has no business outcome")
        conflict = self.db.get(PaymentConflict, outcome.conflict_id)
        if conflict is None:  # pragma: no cover - transaction owns the conflict row
            raise RuntimeError("payment import conflict disappeared before audit")
        event = self.db.get(PaymentImportEvent, conflict.original_import_event_id)
        if event is None:  # pragma: no cover - protected by the conflict foreign key
            raise RuntimeError("payment import event disappeared before audit")
        metadata.update(
            {
                "payment_conflict_id": str(conflict.id),
                "import_event_id": str(event.id),
            }
        )
        if event.payment_id is not None:
            metadata["payment_id"] = str(event.payment_id)
        AuditService(self.db).append(
            actor_user_id=None,
            action="admissions.payment_import.process",
            resource_type="payment_conflict",
            resource_id=conflict.id,
            purpose=None,
            outcome="failed",
            request_id=request_id,
            metadata=metadata,
        )

    def import_payment(
        self,
        input: PaymentImportRequest,
        *,
        machine: VerifiedMachineRequest,
        request_id: str = "payment-import-service",
    ) -> PaymentImportResult:
        phone_digest = self.lookup.digest(input.payer_phone)
        payload_digest = self._payload_digest(input)
        raw_body_text = self._decode_raw_body(machine.raw_body)
        try:
            with write_transaction(self.db):
                outcome = self._import_in_transaction(
                    input=input,
                    machine=machine,
                    phone_digest=phone_digest,
                    payload_digest=payload_digest,
                    raw_body_text=raw_body_text,
                )
                if outcome.result is not None:
                    RevenueShareService(self.db).reconcile_payment_in_transaction(
                        outcome.result.payment.id
                    )
                self._audit_machine_import(
                    outcome,
                    machine=machine,
                    request_id=request_id,
                )
        except IntegrityError:
            with write_transaction(self.db):
                self._require_source(
                    service_account_id=machine.service_account_id,
                    source_system=input.source_system,
                )
                recovered = self._resolve_existing(
                    input=input,
                    payload_digest=payload_digest,
                    raw_body_text=raw_body_text,
                )
                if recovered is None:
                    raise
                outcome = recovered
                if outcome.result is not None:
                    RevenueShareService(self.db).reconcile_payment_in_transaction(
                        outcome.result.payment.id
                    )
                self._audit_machine_import(
                    outcome,
                    machine=machine,
                    request_id=request_id,
                )
        if outcome.conflict_id is not None:
            raise PaymentImportConflict(outcome.conflict_id)
        if outcome.result is None:  # pragma: no cover - exhaustive outcome guard
            raise RuntimeError("payment import produced no result")
        return outcome.result
