from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.errors import ArchivedCohortLocked, RefundExceedsPaidAmount
from app.admissions.models import (
    AdmissionCase,
    Enrollment,
    PaymentRefund,
)
from app.admissions.payment_service import PaymentService
from app.admissions.schemas import ManualPaymentCreate, RefundCreate
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person, StudentProfile
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import Cohort, TeachingClass

NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class PaymentScenario:
    admin: UserAccount
    student: Person
    other_student: Person


def _person(
    db: Session,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    name: str,
    phone: str,
) -> Person:
    marker = uuid4().hex
    row = Person(
        full_name=name,
        identity_ciphertext=f"identity-{marker}",
        identity_digest=f"identity-digest-{marker}",
        phone_ciphertext=cipher.encrypt(phone, field_name="people.phone"),
        phone_digest=lookup.digest(phone),
        email=f"{marker}@example.test",
        sex="female",
        date_of_birth=date(1990, 1, 1),
    )
    db.add(row)
    db.flush()
    return row


def _account(db: Session, *, person: Person, lookup: LookupDigest, phone: str) -> UserAccount:
    row = UserAccount(
        person_id=person.id,
        login_phone_digest=lookup.digest(phone),
        email_normalized=person.email or f"{uuid4().hex}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def cipher() -> EnvelopeCipher:
    return EnvelopeCipher(bytes(range(32)))


@pytest.fixture
def lookup() -> LookupDigest:
    return LookupDigest(bytes(reversed(range(32))))


@pytest.fixture
def payment_scenario(
    db: Session,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> PaymentScenario:
    admin_person = _person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="付款管理员",
        phone="13830000001",
    )
    admin = _account(db, person=admin_person, lookup=lookup, phone="13830000001")
    student = _person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="手工付款学员",
        phone="13830000002",
    )
    other_student = _person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="另一位付款学员",
        phone="13830000003",
    )
    for index, person in enumerate((student, other_student), start=1):
        db.add(
            StudentProfile(
                person_id=person.id,
                student_number=f"MANUAL-PAY-{index}-{uuid4().hex[:8]}",
                status="active",
            )
        )
    db.commit()
    return PaymentScenario(admin=admin, student=student, other_student=other_student)


@pytest.fixture
def payment_service(
    db: Session,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> PaymentService:
    return PaymentService(db, cipher=cipher, lookup=lookup)


def _manual(
    *,
    student_id: UUID | None = None,
    case_id: UUID | None = None,
    amount: str = "5000.00",
    paid_at: datetime = NOW,
    note: str = "线下转账",
) -> ManualPaymentCreate:
    return ManualPaymentCreate(
        student_person_id=student_id,
        admission_case_id=case_id,
        amount=amount,
        paid_at=paid_at,
        note=note,
    )


def _create_enrolled_case(
    db: Session,
    *,
    scenario: PaymentScenario,
    status: str = "active",
) -> tuple[AdmissionCase, Enrollment, Cohort]:
    cohort = Cohort(
        name=f"付款营期-{uuid4().hex[:8]}",
        starts_at=NOW,
        ends_at=NOW + timedelta(days=10),
        default_check_in_at=NOW,
        default_check_out_at=NOW + timedelta(days=10),
        status=status,
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=scenario.admin.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"付款班级-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=1_000_000,
        is_active=True,
        created_by_user_id=scenario.admin.id,
    )
    db.add(teaching_class)
    case = AdmissionCase(
        student_person_id=scenario.student.id,
        status="ready",
        agreed_tuition_cents=1_000_000,
        created_by_user_id=scenario.admin.id,
    )
    db.add(case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=case.id,
        student_person_id=scenario.student.id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=1_000_000,
        status="active",
        confirmed_at=NOW,
        created_by_user_id=scenario.admin.id,
    )
    db.add(enrollment)
    db.flush()
    case.status = "enrolled"
    case.current_enrollment_id = enrollment.id
    db.commit()
    return case, enrollment, cohort


def test_manual_payment_for_student_creates_ready_case(
    payment_service: PaymentService,
    payment_scenario: PaymentScenario,
) -> None:
    result = payment_service.create_manual_payment(
        _manual(student_id=payment_scenario.student.id),
        actor_user_id=payment_scenario.admin.id,
        request_id="manual-1",
    )

    assert result.admission_case.status == "ready"
    assert result.admission_case.student_person_id == payment_scenario.student.id
    assert result.payment.amount_cents == 500_000
    assert result.payment.source_kind == "manual"
    assert result.payment.created_by_user_id == payment_scenario.admin.id


@pytest.mark.parametrize(
    ("student_id", "case_id"),
    [(None, None), (uuid4(), uuid4())],
)
def test_manual_payment_requires_exactly_one_student_or_case(
    student_id: UUID | None,
    case_id: UUID | None,
) -> None:
    with pytest.raises(ValidationError):
        _manual(student_id=student_id, case_id=case_id)


def test_installment_attaches_to_enrolled_case_without_changing_enrollment(
    payment_service: PaymentService,
    payment_scenario: PaymentScenario,
    db: Session,
) -> None:
    case, enrollment, _ = _create_enrolled_case(db, scenario=payment_scenario)

    result = payment_service.create_manual_payment(
        _manual(case_id=case.id, amount="2500.00", note="第二期学费"),
        actor_user_id=payment_scenario.admin.id,
        request_id="manual-installment",
    )

    assert result.admission_case.id == case.id
    assert result.admission_case.status == "enrolled"
    assert result.admission_case.current_enrollment_id == enrollment.id
    assert db.get(Enrollment, enrollment.id).status == "active"  # type: ignore[union-attr]


def test_refund_cannot_exceed_original_payment(
    payment_service: PaymentService,
    payment_scenario: PaymentScenario,
) -> None:
    manual = payment_service.create_manual_payment(
        _manual(student_id=payment_scenario.student.id, amount="100.00"),
        actor_user_id=payment_scenario.admin.id,
        request_id="manual-refund-source",
    )
    payment_service.create_refund(
        manual.payment.id,
        RefundCreate(amount="75.00", refunded_at=NOW, reason="首次退款"),
        actor_user_id=payment_scenario.admin.id,
        request_id="refund-1",
    )

    with pytest.raises(RefundExceedsPaidAmount) as error:
        payment_service.create_refund(
            manual.payment.id,
            RefundCreate(amount="25.01", refunded_at=NOW, reason="超额退款"),
            actor_user_id=payment_scenario.admin.id,
            request_id="refund-2",
        )

    assert error.value.code == "refund_exceeds_paid_amount"
    assert payment_service.available_refund_cents(manual.payment.id) == 2_500


def test_refund_does_not_cancel_active_enrollment(
    payment_service: PaymentService,
    payment_scenario: PaymentScenario,
    db: Session,
) -> None:
    case, enrollment, _ = _create_enrolled_case(db, scenario=payment_scenario)
    manual = payment_service.create_manual_payment(
        _manual(case_id=case.id, amount="1000.00"),
        actor_user_id=payment_scenario.admin.id,
        request_id="manual-enrolled",
    )

    payment_service.create_refund(
        manual.payment.id,
        RefundCreate(amount="1000.00", refunded_at=NOW, reason="普通全额退款"),
        actor_user_id=payment_scenario.admin.id,
        request_id="refund-enrolled",
    )

    stored_enrollment = db.get(Enrollment, enrollment.id)
    assert stored_enrollment is not None
    assert stored_enrollment.status == "active"
    assert db.get(AdmissionCase, case.id).status == "enrolled"  # type: ignore[union-attr]


def test_payment_summary_tracks_partial_paid_overpaid_and_refunded(
    payment_service: PaymentService,
    payment_scenario: PaymentScenario,
    db: Session,
) -> None:
    first = payment_service.create_manual_payment(
        _manual(student_id=payment_scenario.student.id, amount="5000.00"),
        actor_user_id=payment_scenario.admin.id,
        request_id="summary-1",
    )
    first.admission_case.agreed_tuition_cents = 1_000_000
    db.commit()

    partial = payment_service.payment_summary(first.admission_case.id)
    assert partial.paid_cents == 500_000
    assert partial.refunded_cents == 0
    assert partial.net_paid_cents == 500_000
    assert partial.agreed_tuition_cents == 1_000_000
    assert partial.due_cents == 500_000
    assert partial.overpaid_cents == 0
    assert partial.status == "partial"

    second = payment_service.create_manual_payment(
        _manual(case_id=first.admission_case.id, amount="6000.00", paid_at=NOW + timedelta(hours=1)),
        actor_user_id=payment_scenario.admin.id,
        request_id="summary-2",
    )
    overpaid = payment_service.payment_summary(first.admission_case.id)
    assert overpaid.net_paid_cents == 1_100_000
    assert overpaid.overpaid_cents == 100_000
    assert overpaid.status == "overpaid"

    payment_service.create_refund(
        second.payment.id,
        RefundCreate(amount="1000.00", refunded_at=NOW, reason="退回超付"),
        actor_user_id=payment_scenario.admin.id,
        request_id="summary-refund-overpaid",
    )
    paid = payment_service.payment_summary(first.admission_case.id)
    assert paid.net_paid_cents == 1_000_000
    assert paid.status == "paid"

    payment_service.create_refund(
        first.payment.id,
        RefundCreate(amount="5000.00", refunded_at=NOW, reason="退回第一期"),
        actor_user_id=payment_scenario.admin.id,
        request_id="summary-refund-first",
    )
    payment_service.create_refund(
        second.payment.id,
        RefundCreate(amount="5000.00", refunded_at=NOW, reason="退回第二期"),
        actor_user_id=payment_scenario.admin.id,
        request_id="summary-refund-second",
    )
    refunded = payment_service.payment_summary(first.admission_case.id)
    assert refunded.net_paid_cents == 0
    assert refunded.status == "refunded"


def test_zero_tuition_treats_positive_net_payment_as_overpaid(
    payment_service: PaymentService,
    payment_scenario: PaymentScenario,
    db: Session,
) -> None:
    manual = payment_service.create_manual_payment(
        _manual(student_id=payment_scenario.student.id, amount="100.00"),
        actor_user_id=payment_scenario.admin.id,
        request_id="zero-tuition-payment",
    )
    manual.admission_case.agreed_tuition_cents = 0
    db.commit()

    summary = payment_service.payment_summary(manual.admission_case.id)

    assert summary.due_cents == 0
    assert summary.overpaid_cents == 10_000
    assert summary.status == "overpaid"


def test_archived_cohort_blocks_installment_and_refund(
    payment_service: PaymentService,
    payment_scenario: PaymentScenario,
    db: Session,
) -> None:
    case, _, cohort = _create_enrolled_case(db, scenario=payment_scenario)
    payment = payment_service.create_manual_payment(
        _manual(case_id=case.id, amount="1000.00"),
        actor_user_id=payment_scenario.admin.id,
        request_id="before-archive",
    )
    cohort.status = "archived"
    db.commit()

    with pytest.raises(ArchivedCohortLocked):
        payment_service.create_manual_payment(
            _manual(case_id=case.id, amount="100.00"),
            actor_user_id=payment_scenario.admin.id,
            request_id="archived-installment",
        )
    with pytest.raises(ArchivedCohortLocked):
        payment_service.create_refund(
            payment.payment.id,
            RefundCreate(amount="100.00", refunded_at=NOW, reason="归档后退款"),
            actor_user_id=payment_scenario.admin.id,
            request_id="archived-refund",
        )


def test_payment_list_has_stable_pagination_and_total(
    payment_service: PaymentService,
    payment_scenario: PaymentScenario,
) -> None:
    case = payment_service.create_manual_payment(
        _manual(student_id=payment_scenario.student.id, amount="100.00", paid_at=NOW),
        actor_user_id=payment_scenario.admin.id,
        request_id="page-1",
    ).admission_case
    created_ids = []
    for index in range(1, 4):
        result = payment_service.create_manual_payment(
            _manual(
                case_id=case.id,
                amount="100.00",
                paid_at=NOW + timedelta(hours=index),
                note=f"分页付款 {index}",
            ),
            actor_user_id=payment_scenario.admin.id,
            request_id=f"page-{index + 1}",
        )
        created_ids.append(result.payment.id)

    page, total = payment_service.list_payments(page=1, page_size=2)
    next_page, next_total = payment_service.list_payments(page=2, page_size=2)

    assert total == 4
    assert next_total == 4
    assert [item.payment.id for item in page] == list(reversed(created_ids[-2:]))
    assert len(next_page) == 2
    assert {item.payment.id for item in page}.isdisjoint(
        item.payment.id for item in next_page
    )


def test_refund_audit_contains_ids_and_type_but_never_amount(
    payment_service: PaymentService,
    payment_scenario: PaymentScenario,
    db: Session,
) -> None:
    manual = payment_service.create_manual_payment(
        _manual(student_id=payment_scenario.student.id, amount="100.00"),
        actor_user_id=payment_scenario.admin.id,
        request_id="audit-payment",
    )
    refund = payment_service.create_refund(
        manual.payment.id,
        RefundCreate(amount="25.00", refunded_at=NOW, reason="审计退款"),
        actor_user_id=payment_scenario.admin.id,
        request_id="audit-refund",
    )

    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "admissions.payment.refund",
            AuditEvent.resource_id == manual.payment.id,
        )
    )
    assert event is not None
    assert event.actor_user_id == payment_scenario.admin.id
    assert event.event_metadata == {
        "payment_id": str(manual.payment.id),
        "refund_id": str(refund.id),
        "refund_type": "manual",
    }
    assert "2500" not in str(event.event_metadata)
    assert db.scalar(select(func.count()).select_from(PaymentRefund)) == 1
