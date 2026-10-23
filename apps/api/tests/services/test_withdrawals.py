from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.admissions.errors import (
    AdmissionsServiceError,
    ArchivedCohortLocked,
    EnrollmentNotActive,
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
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person
from app.teaching.models import Cohort, TeachingClass

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 8, 6, 9, 0, tzinfo=SHANGHAI)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
OLDER_PAYMENT_ID = UUID("10000000-0000-4000-8000-000000000001")
NEWER_PAYMENT_ID = UUID("f0000000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class WithdrawalScenario:
    db: Session
    admin: UserAccount
    student: Person
    cohort: Cohort
    teaching_class: TeachingClass
    admission_case: AdmissionCase
    enrollment: Enrollment
    older: Payment
    newer: Payment


def _person(db: Session, *, name: str) -> Person:
    marker = uuid4().hex
    person = Person(
        full_name=name,
        identity_ciphertext=f"withdrawal-identity-{marker}",
        identity_digest=f"withdrawal-identity-digest-{marker}",
        phone_ciphertext=None,
        phone_digest=None,
        email=f"withdrawal-{marker}@example.test",
        sex="female",
        date_of_birth=date(2000, 1, 1),
    )
    db.add(person)
    db.flush()
    return person


def _account(db: Session, *, person: Person) -> UserAccount:
    marker = uuid4().hex
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"withdrawal-phone-{marker}",
        email_normalized=person.email or f"withdrawal-{marker}@example.test",
        password_hash="withdrawal-test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    return account


def _payment(
    db: Session,
    *,
    payment_id: UUID,
    actor_user_id: UUID,
    student_person_id: UUID,
    amount_cents: int,
    paid_at: datetime,
) -> Payment:
    payment = Payment(
        id=payment_id,
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=None,
        payer_person_id=student_person_id,
        amount_cents=amount_cents,
        currency="CNY",
        paid_at=paid_at,
        manual_note="退营测试分期付款",
        created_by_user_id=actor_user_id,
    )
    db.add(payment)
    db.flush()
    return payment


@pytest.fixture
def withdrawal_scenario(db: Session) -> WithdrawalScenario:
    admin_person = _person(db, name="退营退款管理员")
    admin = _account(db, person=admin_person)
    student = _person(db, name="退营退款学员")
    cohort = Cohort(
        name=f"退营退款营期-{uuid4().hex[:8]}",
        starts_at=datetime(2026, 8, 1, 0, 0, tzinfo=SHANGHAI),
        ends_at=datetime(2026, 8, 11, 0, 0, tzinfo=SHANGHAI),
        default_check_in_at=datetime(2026, 8, 1, 8, 0, tzinfo=SHANGHAI),
        default_check_out_at=datetime(2026, 8, 10, 18, 0, tzinfo=SHANGHAI),
        status="active",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admin.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"退营退款班级-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=200_000,
        is_active=True,
        created_by_user_id=admin.id,
    )
    db.add(teaching_class)
    admission_case = AdmissionCase(
        student_person_id=student.id,
        status="ready",
        agreed_tuition_cents=200_000,
        created_by_user_id=admin.id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=student.id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=200_000,
        status="active",
        confirmed_at=datetime(2026, 7, 20, 10, 0, tzinfo=SHANGHAI),
        created_by_user_id=admin.id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    older = _payment(
        db,
        payment_id=OLDER_PAYMENT_ID,
        actor_user_id=admin.id,
        student_person_id=student.id,
        amount_cents=70_000,
        paid_at=datetime(2026, 7, 1, 10, 0, tzinfo=SHANGHAI),
    )
    newer = _payment(
        db,
        payment_id=NEWER_PAYMENT_ID,
        actor_user_id=admin.id,
        student_person_id=student.id,
        amount_cents=50_000,
        paid_at=datetime(2026, 7, 10, 10, 0, tzinfo=SHANGHAI),
    )
    db.add_all(
        [
            AdmissionCasePayment(
                admission_case_id=admission_case.id,
                payment_id=older.id,
                attached_by_user_id=admin.id,
            ),
            AdmissionCasePayment(
                admission_case_id=admission_case.id,
                payment_id=newer.id,
                attached_by_user_id=admin.id,
            ),
        ]
    )
    db.commit()
    return WithdrawalScenario(
        db=db,
        admin=admin,
        student=student,
        cohort=cohort,
        teaching_class=teaching_class,
        admission_case=admission_case,
        enrollment=enrollment,
        older=older,
        newer=newer,
    )


def _service(db: Session) -> Any:
    from app.admissions.withdrawal_service import WithdrawalService

    return WithdrawalService(db)


def _preview_request(
    *,
    withdrawal_date: date = date(2026, 8, 6),
    policy: str = "prorated_remaining_days",
) -> Any:
    from app.admissions.schemas import WithdrawalPreviewRequest

    return WithdrawalPreviewRequest(
        withdrawal_date=withdrawal_date,
        policy=policy,
    )


def _confirm_request(preview: Any, *, reason: str = "家庭安排变化") -> Any:
    from app.admissions.schemas import WithdrawalConfirmRequest

    return WithdrawalConfirmRequest(
        withdrawal_date=preview.withdrawal_date,
        policy=preview.policy,
        total_days=preview.total_days,
        remaining_days=preview.remaining_days,
        agreed_tuition_cents=preview.agreed_tuition_cents,
        available_paid_cents=preview.available_paid_cents,
        refund_cents=preview.refund_cents,
        allocations=preview.allocations,
        reason=reason,
    )


def test_preview_returns_natural_day_snapshot_and_recent_first_split(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario

    preview = _service(scenario.db).preview(
        scenario.enrollment.id,
        _preview_request(),
    )

    assert preview.withdrawal_date == date(2026, 8, 6)
    assert preview.policy == "prorated_remaining_days"
    assert preview.total_days == 10
    assert preview.remaining_days == 4
    assert preview.agreed_tuition_cents == 200_000
    assert preview.available_paid_cents == 120_000
    assert preview.refund_cents == 80_000
    assert [(item.payment_id, item.amount_cents) for item in preview.allocations] == [
        (scenario.newer.id, 50_000),
        (scenario.older.id, 30_000),
    ]


def test_preview_before_start_refunds_the_full_proportional_snapshot(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario

    preview = _service(scenario.db).preview(
        scenario.enrollment.id,
        _preview_request(withdrawal_date=date(2026, 7, 30)),
    )

    assert (preview.total_days, preview.remaining_days) == (10, 10)
    assert preview.refund_cents == 120_000


def test_full_remaining_paid_ignores_the_withdrawal_day_ratio(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario

    preview = _service(scenario.db).preview(
        scenario.enrollment.id,
        _preview_request(
            withdrawal_date=date(2026, 8, 10),
            policy="full_remaining_paid",
        ),
    )

    assert preview.remaining_days == 0
    assert preview.refund_cents == 120_000


def test_prorated_preview_on_the_end_day_has_nothing_to_refund(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario

    with pytest.raises(AdmissionsServiceError) as error:
        _service(scenario.db).preview(
            scenario.enrollment.id,
            _preview_request(withdrawal_date=date(2026, 8, 10)),
        )

    assert error.value.code == "withdrawal_nothing_to_refund"


def test_preview_rejects_when_every_payment_was_already_refunded(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario
    for payment in (scenario.older, scenario.newer):
        scenario.db.add(
            PaymentRefund(
                payment_id=payment.id,
                withdrawal_id=None,
                amount_cents=payment.amount_cents,
                refunded_at=NOW,
                refund_type="manual",
                reason="此前已全额退款",
                created_by_user_id=scenario.admin.id,
            )
        )
    scenario.db.commit()

    with pytest.raises(AdmissionsServiceError) as error:
        _service(scenario.db).preview(
            scenario.enrollment.id,
            _preview_request(policy="full_remaining_paid"),
        )

    assert error.value.code == "withdrawal_nothing_to_refund"


def test_split_ties_use_payment_uuid_descending(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario
    scenario.older.paid_at = scenario.newer.paid_at
    scenario.db.commit()

    preview = _service(scenario.db).preview(
        scenario.enrollment.id,
        _preview_request(),
    )

    assert [item.payment_id for item in preview.allocations] == [
        NEWER_PAYMENT_ID,
        OLDER_PAYMENT_ID,
    ]


def test_changed_payment_after_preview_rejects_confirmation_without_writes(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario
    service = _service(scenario.db)
    preview = service.preview(scenario.enrollment.id, _preview_request())
    scenario.db.add(
        PaymentRefund(
            payment_id=scenario.newer.id,
            withdrawal_id=None,
            amount_cents=100,
            refunded_at=NOW,
            refund_type="manual",
            reason="预览后发生退款",
            created_by_user_id=scenario.admin.id,
        )
    )
    scenario.db.commit()

    with pytest.raises(AdmissionsServiceError) as error:
        service.confirm(
            scenario.enrollment.id,
            _confirm_request(preview),
            actor_user_id=scenario.admin.id,
            request_id="withdrawal-confirm-stale",
        )

    assert error.value.code == "withdrawal_refund_changed"
    scenario.db.expire_all()
    enrollment = scenario.db.get(Enrollment, scenario.enrollment.id)
    admission_case = scenario.db.get(AdmissionCase, scenario.admission_case.id)
    assert enrollment is not None and enrollment.status == "active"
    assert admission_case is not None and admission_case.status == "enrolled"
    assert scenario.db.scalar(select(func.count()).select_from(EnrollmentWithdrawal)) == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("withdrawal_date", date(2026, 8, 5)),
        ("policy", "full_remaining_paid"),
        ("total_days", 11),
        ("remaining_days", 3),
        ("agreed_tuition_cents", 199_999),
        ("available_paid_cents", 119_999),
        ("refund_cents", 79_999),
        (
            "allocations",
            [
                {"payment_id": NEWER_PAYMENT_ID, "amount_cents": 49_999},
                {"payment_id": OLDER_PAYMENT_ID, "amount_cents": 30_001},
            ],
        ),
    ],
)
def test_confirmation_requires_exact_preview_snapshot(
    withdrawal_scenario: WithdrawalScenario,
    field: str,
    replacement: object,
) -> None:
    from app.admissions.schemas import WithdrawalConfirmRequest

    scenario = withdrawal_scenario
    service = _service(scenario.db)
    preview = service.preview(scenario.enrollment.id, _preview_request())
    values = _confirm_request(preview).model_dump()
    values[field] = replacement

    with pytest.raises(AdmissionsServiceError) as error:
        service.confirm(
            scenario.enrollment.id,
            WithdrawalConfirmRequest(**values),
            actor_user_id=scenario.admin.id,
            request_id=f"withdrawal-mismatch-{field}",
        )

    assert error.value.code == "withdrawal_refund_changed"

    scenario.db.expire_all()
    stored = scenario.db.get(Enrollment, scenario.enrollment.id)
    assert stored is not None and stored.status == "active"
    assert scenario.db.scalar(select(func.count()).select_from(EnrollmentWithdrawal)) == 0


def _normalized(statement: str) -> str:
    return " ".join(statement.lower().split())


def test_confirmation_refunds_atomically_reopens_case_and_uses_global_lock_order(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario
    service = _service(scenario.db)
    preview = service.preview(scenario.enrollment.id, _preview_request())
    statements: list[str] = []
    bind = scenario.db.get_bind()
    assert isinstance(bind, Connection)

    def record_statement(
        _connection: Connection,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = _normalized(statement)
        if normalized.startswith("select") and " for update" in normalized:
            statements.append(normalized)

    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        withdrawal = service.confirm(
            scenario.enrollment.id,
            _confirm_request(preview),
            actor_user_id=scenario.admin.id,
            request_id="withdrawal-confirm-success",
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)

    scenario.db.expire_all()
    enrollment = scenario.db.get(Enrollment, scenario.enrollment.id)
    admission_case = scenario.db.get(AdmissionCase, scenario.admission_case.id)
    stored_withdrawal = scenario.db.get(EnrollmentWithdrawal, withdrawal.id)
    assert stored_withdrawal is not None
    assert stored_withdrawal.refund_policy == "prorated_remaining_days"
    assert stored_withdrawal.withdrawal_date == date(2026, 8, 6)
    assert stored_withdrawal.total_days_snapshot == 10
    assert stored_withdrawal.remaining_days_snapshot == 4
    assert stored_withdrawal.agreed_tuition_cents_snapshot == 200_000
    assert stored_withdrawal.available_paid_cents_snapshot == 120_000
    assert stored_withdrawal.calculated_refund_cents == 80_000
    assert stored_withdrawal.reason == "家庭安排变化"
    assert stored_withdrawal.confirmed_by_user_id == scenario.admin.id
    assert enrollment is not None
    assert enrollment.status == "cancelled"
    assert enrollment.cancelled_at is not None
    assert admission_case is not None
    assert admission_case.status == "ready"
    assert admission_case.current_enrollment_id is None
    assert admission_case.agreed_tuition_cents is None
    refunds = list(
        scenario.db.scalars(
            select(PaymentRefund)
            .where(PaymentRefund.withdrawal_id == withdrawal.id)
            .order_by(PaymentRefund.payment_id.desc())
        )
    )
    assert [(item.payment_id, item.amount_cents, item.refund_type) for item in refunds] == [
        (scenario.newer.id, 50_000, "withdrawal_prorated"),
        (scenario.older.id, 30_000, "withdrawal_prorated"),
    ]
    assert all(item.reason == "家庭安排变化" for item in refunds)
    assert all(item.created_by_user_id == scenario.admin.id for item in refunds)
    lifecycle_event = scenario.db.scalar(
        select(EnrollmentEvent).where(
            EnrollmentEvent.enrollment_id == scenario.enrollment.id,
            EnrollmentEvent.event_type == "withdrawal_refunded",
        )
    )
    assert lifecycle_event is not None
    assert (lifecycle_event.old_status, lifecycle_event.new_status) == (
        "active",
        "cancelled",
    )
    assert lifecycle_event.old_class_id == scenario.teaching_class.id
    assert lifecycle_event.new_class_id is None
    assert lifecycle_event.reason == "家庭安排变化"
    assert lifecycle_event.request_id == "withdrawal-confirm-success"
    audit = scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "admissions.enrollment.withdrawal_refund",
            AuditEvent.resource_id == scenario.enrollment.id,
        )
    )
    assert audit is not None
    assert audit.event_metadata == {
        "student_person_id": str(scenario.student.id),
        "admission_case_id": str(scenario.admission_case.id),
        "enrollment_id": str(scenario.enrollment.id),
        "cohort_id": str(scenario.cohort.id),
        "class_id": str(scenario.teaching_class.id),
        "refund_type": "withdrawal_prorated",
    }

    cohort_lock = next(
        index for index, statement in enumerate(statements) if " from cohorts " in statement
    )
    class_lock = next(
        index
        for index, statement in enumerate(statements)
        if " from teaching_classes " in statement
    )
    enrollment_lock = next(
        index
        for index, statement in enumerate(statements)
        if " from enrollments " in statement
    )
    case_lock = next(
        index
        for index, statement in enumerate(statements)
        if " from admission_cases " in statement
    )
    payment_lock = next(
        index for index, statement in enumerate(statements) if " from payments " in statement
    )
    assert cohort_lock < class_lock < enrollment_lock < case_lock < payment_lock
    assert "order by payments.id" in statements[payment_lock]


def test_second_confirmation_is_rejected_without_duplicate_refunds(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario
    service = _service(scenario.db)
    preview = service.preview(scenario.enrollment.id, _preview_request())
    input = _confirm_request(preview)
    service.confirm(
        scenario.enrollment.id,
        input,
        actor_user_id=scenario.admin.id,
        request_id="withdrawal-first-confirm",
    )

    with pytest.raises(EnrollmentNotActive) as error:
        service.confirm(
            scenario.enrollment.id,
            input,
            actor_user_id=scenario.admin.id,
            request_id="withdrawal-second-confirm",
        )

    assert error.value.code == "enrollment_not_active"
    assert scenario.db.scalar(select(func.count()).select_from(EnrollmentWithdrawal)) == 1
    assert scenario.db.scalar(
        select(func.count()).select_from(PaymentRefund).where(
            PaymentRefund.withdrawal_id.is_not(None)
        )
    ) == 2


def test_full_remaining_confirmation_uses_the_full_withdrawal_refund_type(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario
    service = _service(scenario.db)
    preview = service.preview(
        scenario.enrollment.id,
        _preview_request(
            withdrawal_date=date(2026, 8, 10),
            policy="full_remaining_paid",
        ),
    )

    withdrawal = service.confirm(
        scenario.enrollment.id,
        _confirm_request(preview),
        actor_user_id=scenario.admin.id,
        request_id="withdrawal-full-confirm",
    )

    refunds = list(
        scenario.db.scalars(
            select(PaymentRefund).where(PaymentRefund.withdrawal_id == withdrawal.id)
        )
    )
    assert sum(item.amount_cents for item in refunds) == 120_000
    assert {item.refund_type for item in refunds} == {"withdrawal_full"}


def test_confirm_rejects_archived_cohort_without_writes(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario
    service = _service(scenario.db)
    preview = service.preview(scenario.enrollment.id, _preview_request())
    scenario.cohort.status = "archived"
    scenario.db.commit()

    with pytest.raises(ArchivedCohortLocked) as error:
        service.confirm(
            scenario.enrollment.id,
            _confirm_request(preview),
            actor_user_id=scenario.admin.id,
            request_id="withdrawal-archived",
        )

    assert error.value.code == "archived_cohort_locked"
    assert scenario.db.scalar(select(func.count()).select_from(EnrollmentWithdrawal)) == 0


def test_refund_insert_failure_rolls_back_withdrawal_and_cancellation(
    withdrawal_scenario: WithdrawalScenario,
) -> None:
    scenario = withdrawal_scenario
    service = _service(scenario.db)
    preview = service.preview(scenario.enrollment.id, _preview_request())

    def fail_after_refund_insert(
        _mapper: object,
        _connection: Connection,
        target: PaymentRefund,
    ) -> None:
        if target.withdrawal_id is not None:
            raise RuntimeError("injected withdrawal refund failure")

    event.listen(PaymentRefund, "after_insert", fail_after_refund_insert)
    try:
        with pytest.raises(RuntimeError, match="injected withdrawal refund failure"):
            service.confirm(
                scenario.enrollment.id,
                _confirm_request(preview),
                actor_user_id=scenario.admin.id,
                request_id="withdrawal-injected-failure",
            )
    finally:
        event.remove(PaymentRefund, "after_insert", fail_after_refund_insert)

    scenario.db.expire_all()
    enrollment = scenario.db.get(Enrollment, scenario.enrollment.id)
    admission_case = scenario.db.get(AdmissionCase, scenario.admission_case.id)
    assert enrollment is not None and enrollment.status == "active"
    assert enrollment.cancelled_at is None
    assert admission_case is not None and admission_case.status == "enrolled"
    assert admission_case.current_enrollment_id == scenario.enrollment.id
    assert admission_case.agreed_tuition_cents == 200_000
    assert scenario.db.scalar(select(func.count()).select_from(EnrollmentWithdrawal)) == 0
    assert scenario.db.scalar(
        select(func.count()).select_from(PaymentRefund).where(
            PaymentRefund.withdrawal_id.is_not(None)
        )
    ) == 0
