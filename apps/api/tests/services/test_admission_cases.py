from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.admissions.case_service import AdmissionCaseService
from app.admissions.errors import (
    AdmissionPaymentRequired,
    AdmissionStudentNotFound,
    ArchivedCohortLocked,
    CapacityOverrideReasonRequired,
    ClassCapacityReached,
    ClassInactive,
    CohortNotOpen,
    DuplicateCohortEnrollment,
    MinorGuardianRequired,
    TuitionOverrideReasonRequired,
)
from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    EnrollmentEvent,
    Payment,
)
from app.admissions.payment_service import PaymentService
from app.admissions.schemas import ConfirmEnrollment, ManualPaymentCreate, RefundCreate
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person, StudentProfile
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import Cohort, TeachingClass

NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


def _person(
    db: Session,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    name: str,
    phone: str,
    birth_date: date,
    student: bool,
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
        date_of_birth=birth_date,
    )
    db.add(row)
    db.flush()
    if student:
        db.add(
            StudentProfile(
                person_id=row.id,
                student_number=f"ADMIT-{uuid4().hex[:12]}",
                status="active",
            )
        )
    return row


def _account(
    db: Session,
    *,
    person: Person,
    lookup: LookupDigest,
    phone: str,
) -> UserAccount:
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


@dataclass(frozen=True)
class AdmissionScenario:
    db: Session
    admin: UserAccount
    adult_student: Person
    other_student: Person
    minor_student: Person
    non_student: Person
    cohort: Cohort
    teaching_class: TeachingClass
    payments: PaymentService

    def ready_case(
        self,
        student: Person | None = None,
        *,
        amount: str = "1000.00",
    ) -> AdmissionCase:
        target = student or self.adult_student
        return self.payments.create_manual_payment(
            ManualPaymentCreate(
                student_person_id=target.id,
                admission_case_id=None,
                amount=amount,
                paid_at=NOW,
                note="确认报名测试付款",
            ),
            actor_user_id=self.admin.id,
            request_id=f"ready-{uuid4().hex}",
        ).admission_case

    def pending_paid_case(self) -> AdmissionCase:
        case = self.ready_case(self.minor_student)
        case.student_person_id = None
        case.status = "pending_match"
        self.db.commit()
        return case


@pytest.fixture
def admission_scenario(db: Session) -> AdmissionScenario:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    admin_person = _person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="报名管理员",
        phone="13840000001",
        birth_date=date(1980, 1, 1),
        student=False,
    )
    admin = _account(db, person=admin_person, lookup=lookup, phone="13840000001")
    adult = _person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="成年报名学员",
        phone="13840000002",
        birth_date=date(2000, 1, 1),
        student=True,
    )
    other = _person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="另一位报名学员",
        phone="13840000003",
        birth_date=date(2001, 1, 1),
        student=True,
    )
    minor = _person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="未成年报名学员",
        phone="13840000004",
        birth_date=date(2012, 1, 1),
        student=True,
    )
    non_student = _person(
        db,
        cipher=cipher,
        lookup=lookup,
        name="非学员人员",
        phone="13840000005",
        birth_date=date(1990, 1, 1),
        student=False,
    )
    cohort = Cohort(
        name=f"报名营期-{uuid4().hex[:8]}",
        starts_at=NOW,
        ends_at=NOW + timedelta(days=10),
        default_check_in_at=NOW,
        default_check_out_at=NOW + timedelta(days=10),
        status="open",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admin.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"报名班级-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=2,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=admin.id,
    )
    db.add(teaching_class)
    db.commit()
    payments = PaymentService(db, cipher=cipher, lookup=lookup)
    return AdmissionScenario(
        db,
        admin,
        adult,
        other,
        minor,
        non_student,
        cohort,
        teaching_class,
        payments,
    )


@pytest.fixture
def case_service(db: Session) -> AdmissionCaseService:
    return AdmissionCaseService(db)


def test_pending_payment_can_match_minor_but_guardian_blocks_confirmation(
    admission_scenario: AdmissionScenario,
    case_service: AdmissionCaseService,
) -> None:
    case = admission_scenario.pending_paid_case()

    matched = case_service.match_student(
        case.id,
        admission_scenario.minor_student.id,
        actor_user_id=admission_scenario.admin.id,
    )

    assert matched.status == "ready"
    with pytest.raises(MinorGuardianRequired) as error:
        case_service.confirm_enrollment(
            case.id,
            ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
            actor_user_id=admission_scenario.admin.id,
            request_id="confirm-minor",
        )
    assert error.value.code == "minor_guardian_required"


def test_match_rejects_non_student_and_deleted_student(
    admission_scenario: AdmissionScenario,
    case_service: AdmissionCaseService,
) -> None:
    case = admission_scenario.pending_paid_case()
    with pytest.raises(AdmissionStudentNotFound):
        case_service.match_student(
            case.id,
            admission_scenario.non_student.id,
            actor_user_id=admission_scenario.admin.id,
        )

    admission_scenario.minor_student.deleted_at = NOW
    admission_scenario.db.commit()
    with pytest.raises(AdmissionStudentNotFound):
        case_service.match_student(
            case.id,
            admission_scenario.minor_student.id,
            actor_user_id=admission_scenario.admin.id,
        )


def test_zero_net_payment_blocks_confirmation(
    admission_scenario: AdmissionScenario,
    case_service: AdmissionCaseService,
) -> None:
    manual = admission_scenario.payments.create_manual_payment(
        ManualPaymentCreate(
            student_person_id=admission_scenario.adult_student.id,
            admission_case_id=None,
            amount="1000.00",
            paid_at=NOW,
            note="随后全额退款",
        ),
        actor_user_id=admission_scenario.admin.id,
        request_id="confirm-refunded-payment",
    )
    admission_scenario.payments.create_refund(
        manual.payment.id,
        RefundCreate(amount="1000.00", refunded_at=NOW, reason="全额退款"),
        actor_user_id=admission_scenario.admin.id,
        request_id="confirm-full-refund",
    )

    with pytest.raises(AdmissionPaymentRequired) as error:
        case_service.confirm_enrollment(
            manual.admission_case.id,
            ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
            actor_user_id=admission_scenario.admin.id,
            request_id="confirm-no-payment",
        )
    assert error.value.code == "admission_payment_required"


def test_cohort_must_be_open_and_class_active(
    admission_scenario: AdmissionScenario,
    case_service: AdmissionCaseService,
) -> None:
    case = admission_scenario.ready_case()
    admission_scenario.cohort.status = "upcoming"
    admission_scenario.db.commit()
    with pytest.raises(CohortNotOpen):
        case_service.confirm_enrollment(
            case.id,
            ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
            actor_user_id=admission_scenario.admin.id,
            request_id="cohort-not-open",
        )

    admission_scenario.cohort.status = "open"
    admission_scenario.teaching_class.is_active = False
    admission_scenario.db.commit()
    with pytest.raises(ClassInactive):
        case_service.confirm_enrollment(
            case.id,
            ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
            actor_user_id=admission_scenario.admin.id,
            request_id="class-inactive",
        )


def test_archived_case_history_blocks_direct_confirmation(
    admission_scenario: AdmissionScenario,
    case_service: AdmissionCaseService,
) -> None:
    admission_case = admission_scenario.ready_case()
    archived_cohort = Cohort(
        name=f"归档报名历史-{uuid4().hex[:8]}",
        starts_at=NOW - timedelta(days=40),
        ends_at=NOW - timedelta(days=30),
        default_check_in_at=NOW - timedelta(days=40),
        default_check_out_at=NOW - timedelta(days=30),
        status="archived",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admission_scenario.admin.id,
    )
    admission_scenario.db.add(archived_cohort)
    admission_scenario.db.flush()
    archived_class = TeachingClass(
        cohort_id=archived_cohort.id,
        project_id=PROJECT_ID,
        name=f"归档历史班级-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=admission_scenario.admin.id,
    )
    admission_scenario.db.add(archived_class)
    admission_scenario.db.flush()
    admission_scenario.db.add(
        Enrollment(
            admission_case_id=admission_case.id,
            student_person_id=admission_scenario.adult_student.id,
            cohort_id=archived_cohort.id,
            class_id=archived_class.id,
            agreed_tuition_cents=100_000,
            status="cancelled",
            confirmed_at=NOW - timedelta(days=40),
            cancelled_at=NOW - timedelta(days=35),
            created_by_user_id=admission_scenario.admin.id,
        )
    )
    admission_scenario.db.commit()

    with pytest.raises(ArchivedCohortLocked):
        case_service.confirm_enrollment(
            admission_case.id,
            ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
            actor_user_id=admission_scenario.admin.id,
            request_id="archived-history-confirm",
        )


def test_default_tuition_confirmation_is_atomic_and_audited(
    admission_scenario: AdmissionScenario,
    case_service: AdmissionCaseService,
) -> None:
    case = admission_scenario.ready_case()

    enrollment = case_service.confirm_enrollment(
        case.id,
        ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
        actor_user_id=admission_scenario.admin.id,
        request_id="confirm-default-tuition",
    )

    stored_case = admission_scenario.db.get(AdmissionCase, case.id)
    assert stored_case is not None
    assert enrollment.status == "active"
    assert enrollment.agreed_tuition_cents == 100_000
    assert stored_case.status == "enrolled"
    assert stored_case.current_enrollment_id == enrollment.id
    assert stored_case.agreed_tuition_cents == 100_000
    assert stored_case.tuition_override_reason is None
    events = list(
        admission_scenario.db.scalars(
            select(EnrollmentEvent).where(EnrollmentEvent.enrollment_id == enrollment.id)
        )
    )
    assert [event.event_type for event in events] == ["confirmed"]
    audit = admission_scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "admissions.enrollment.confirm",
            AuditEvent.resource_id == enrollment.id,
        )
    )
    assert audit is not None
    assert audit.event_metadata == {
        "student_person_id": str(admission_scenario.adult_student.id),
        "admission_case_id": str(case.id),
        "enrollment_id": str(enrollment.id),
        "cohort_id": str(admission_scenario.cohort.id),
        "class_id": str(admission_scenario.teaching_class.id),
    }


def test_changed_tuition_requires_reason_and_preserves_snapshot(
    admission_scenario: AdmissionScenario,
    case_service: AdmissionCaseService,
) -> None:
    case = admission_scenario.ready_case()
    with pytest.raises(TuitionOverrideReasonRequired):
        case_service.confirm_enrollment(
            case.id,
            ConfirmEnrollment(
                class_id=admission_scenario.teaching_class.id,
                agreed_tuition_cents=80_000,
            ),
            actor_user_id=admission_scenario.admin.id,
            request_id="tuition-reason-missing",
        )

    enrollment = case_service.confirm_enrollment(
        case.id,
        ConfirmEnrollment(
            class_id=admission_scenario.teaching_class.id,
            agreed_tuition_cents=80_000,
            tuition_override_reason="校长审批优惠",
        ),
        actor_user_id=admission_scenario.admin.id,
        request_id="tuition-approved",
    )

    assert enrollment.agreed_tuition_cents == 80_000
    case = admission_scenario.db.get(AdmissionCase, case.id)
    assert case is not None
    assert case.agreed_tuition_cents == 80_000
    assert case.tuition_override_reason == "校长审批优惠"


def test_full_class_requires_permission_and_reason_independently(
    admission_scenario: AdmissionScenario,
    case_service: AdmissionCaseService,
) -> None:
    admission_scenario.teaching_class.capacity = 1
    admission_scenario.db.commit()
    first = admission_scenario.ready_case(admission_scenario.adult_student)
    case_service.confirm_enrollment(
        first.id,
        ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
        actor_user_id=admission_scenario.admin.id,
        request_id="capacity-first",
    )
    second = admission_scenario.ready_case(admission_scenario.other_student)

    with pytest.raises(ClassCapacityReached):
        case_service.confirm_enrollment(
            second.id,
            ConfirmEnrollment(
                class_id=admission_scenario.teaching_class.id,
                capacity_override_reason="仅填写原因不能授权",
            ),
            actor_user_id=admission_scenario.admin.id,
            request_id="capacity-denied",
            capacity_override_allowed=False,
        )
    with pytest.raises(CapacityOverrideReasonRequired):
        case_service.confirm_enrollment(
            second.id,
            ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
            actor_user_id=admission_scenario.admin.id,
            request_id="capacity-reason-missing",
            capacity_override_allowed=True,
        )

    enrollment = case_service.confirm_enrollment(
        second.id,
        ConfirmEnrollment(
            class_id=admission_scenario.teaching_class.id,
            capacity_override_reason="校长审批",
        ),
        actor_user_id=admission_scenario.admin.id,
        request_id="capacity-approved",
        capacity_override_allowed=True,
    )

    assert enrollment.capacity_override_reason == "校长审批"
    event_types = set(
        admission_scenario.db.scalars(
            select(EnrollmentEvent.event_type).where(
                EnrollmentEvent.enrollment_id == enrollment.id
            )
        )
    )
    assert event_types == {"confirmed", "capacity_overridden"}


def test_duplicate_active_enrollment_in_same_cohort_is_rejected(
    admission_scenario: AdmissionScenario,
    case_service: AdmissionCaseService,
) -> None:
    first = admission_scenario.ready_case()
    case_service.confirm_enrollment(
        first.id,
        ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
        actor_user_id=admission_scenario.admin.id,
        request_id="duplicate-first",
    )
    duplicate = admission_scenario.ready_case()

    with pytest.raises(DuplicateCohortEnrollment) as error:
        case_service.confirm_enrollment(
            duplicate.id,
            ConfirmEnrollment(class_id=admission_scenario.teaching_class.id),
            actor_user_id=admission_scenario.admin.id,
            request_id="duplicate-second",
        )
    assert error.value.code == "duplicate_cohort_enrollment"


def test_concurrent_final_seat_claims_allow_only_one(
    postgres_engine: Engine,
) -> None:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    setup_ids: dict[str, object] = {}
    with Session(postgres_engine) as setup:
        admin_person = _person(
            setup,
            cipher=cipher,
            lookup=lookup,
            name="并发报名管理员",
            phone="13849999991",
            birth_date=date(1980, 1, 1),
            student=False,
        )
        admin = _account(setup, person=admin_person, lookup=lookup, phone="13849999991")
        students = [
            _person(
                setup,
                cipher=cipher,
                lookup=lookup,
                name=f"并发报名学员 {index}",
                phone=f"1384999999{index}",
                birth_date=date(2000 + index, 1, 1),
                student=True,
            )
            for index in (2, 3)
        ]
        cohort = Cohort(
            name=f"并发报名营期-{uuid4().hex[:8]}",
            starts_at=NOW,
            ends_at=NOW + timedelta(days=10),
            default_check_in_at=NOW,
            default_check_out_at=NOW + timedelta(days=10),
            status="open",
            auto_start_enabled=False,
            auto_complete_enabled=False,
            created_by_user_id=admin.id,
        )
        setup.add(cohort)
        setup.flush()
        teaching_class = TeachingClass(
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name=f"并发报名班级-{uuid4().hex[:8]}",
            class_type="standard",
            professional_team_id=None,
            capacity=1,
            default_tuition_cents=100_000,
            is_active=True,
            created_by_user_id=admin.id,
        )
        setup.add(teaching_class)
        setup.commit()
        payments = PaymentService(setup, cipher=cipher, lookup=lookup)
        case_ids = [
            payments.create_manual_payment(
                ManualPaymentCreate(
                    student_person_id=student.id,
                    admission_case_id=None,
                    amount="1000.00",
                    paid_at=NOW,
                    note="并发名额付款",
                ),
                actor_user_id=admin.id,
                request_id=f"concurrent-payment-{index}",
            ).admission_case.id
            for index, student in enumerate(students)
        ]
        setup_ids = {
            "admin_person_id": admin_person.id,
            "admin_id": admin.id,
            "student_ids": [student.id for student in students],
            "cohort_id": cohort.id,
            "class_id": teaching_class.id,
            "case_ids": case_ids,
        }

    barrier = threading.Barrier(2)
    successes: list[UUID] = []
    failures: list[BaseException] = []

    def claim(case_id: UUID) -> None:
        try:
            with Session(postgres_engine) as worker:
                barrier.wait(timeout=5)
                enrollment = AdmissionCaseService(worker).confirm_enrollment(
                    case_id,
                    ConfirmEnrollment(class_id=setup_ids["class_id"]),  # type: ignore[arg-type]
                    actor_user_id=setup_ids["admin_id"],  # type: ignore[arg-type]
                    request_id=f"concurrent-claim-{case_id}",
                )
                successes.append(enrollment.id)
        except (ClassCapacityReached, SQLAlchemyError) as exc:
            failures.append(exc)

    workers = [threading.Thread(target=claim, args=(case_id,)) for case_id in case_ids]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    try:
        assert all(not worker.is_alive() for worker in workers)
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], ClassCapacityReached)
        with Session(postgres_engine) as verification:
            assert verification.scalar(
                select(func.count()).select_from(Enrollment).where(
                    Enrollment.class_id == setup_ids["class_id"],
                    Enrollment.status == "active",
                )
            ) == 1
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            try:
                cleanup.execute(
                    delete(AuditEvent).where(
                        AuditEvent.actor_user_id == setup_ids["admin_id"]
                    )
                )
            finally:
                cleanup.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
            cleanup.execute(
                update(AdmissionCase)
                .where(AdmissionCase.id.in_(case_ids))
                .values(status="ready", current_enrollment_id=None)
            )
            cleanup.execute(
                delete(EnrollmentEvent).where(
                    EnrollmentEvent.enrollment_id.in_(successes)
                )
            )
            cleanup.execute(delete(Enrollment).where(Enrollment.id.in_(successes)))
            cleanup.execute(
                delete(AdmissionCasePayment).where(
                    AdmissionCasePayment.admission_case_id.in_(case_ids)
                )
            )
            cleanup.execute(
                delete(Payment).where(
                    Payment.created_by_user_id == setup_ids["admin_id"]
                )
            )
            cleanup.execute(delete(AdmissionCase).where(AdmissionCase.id.in_(case_ids)))
            cleanup.execute(
                delete(TeachingClass).where(TeachingClass.id == setup_ids["class_id"])
            )
            cleanup.execute(delete(Cohort).where(Cohort.id == setup_ids["cohort_id"]))
            cleanup.execute(
                delete(StudentProfile).where(
                    StudentProfile.person_id.in_(setup_ids["student_ids"])
                )
            )
            cleanup.execute(
                delete(UserAccount).where(UserAccount.id == setup_ids["admin_id"])
            )
            cleanup.execute(
                delete(Person).where(
                    Person.id.in_(
                        [setup_ids["admin_person_id"], *setup_ids["student_ids"]]
                    )
                )
            )
