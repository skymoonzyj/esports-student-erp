from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    Payment,
    PaymentRefund,
)
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person
from app.revenue_share.errors import (
    RevenueShareSettlementExceedsBalance,
    RevenueShareSettlementOperationConflict,
    RevenueShareSettlementReferenceConflict,
)
from app.revenue_share.models import (
    ReferralCode,
    RevenueShareLedgerEntry,
    RevenueShareOrganization,
    RevenueShareReconcileQueue,
    RevenueShareRule,
    RevenueShareSettlement,
    StudentReferralBinding,
)
from app.revenue_share.schemas import RevenueShareSettlementCreate
from app.revenue_share.service import RevenueShareService
from app.teaching.models import CoachAssignment, Cohort, TeachingClass

NOW = datetime(2026, 8, 15, 4, tzinfo=UTC)
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class RevenueShareScenario:
    actor: UserAccount
    student: Person
    recipients: tuple[Person, Person, Person, Person]
    admission_case: AdmissionCase
    enrollment: Enrollment
    teaching_class: TeachingClass


def _person(db: Session, name: str) -> Person:
    marker = uuid4().hex
    row = Person(
        full_name=name,
        identity_ciphertext=f"revenue-identity-{marker}",
        identity_digest=f"revenue-identity-digest-{marker}",
        phone_ciphertext=None,
        phone_digest=None,
        email=None,
        sex="male",
        date_of_birth=date(1990, 1, 1),
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def revenue_scenario(db: Session) -> RevenueShareScenario:
    actor_person = _person(db, "分成测试管理员")
    actor = UserAccount(
        person_id=actor_person.id,
        login_phone_digest=f"revenue-{uuid4().hex}",
        email_normalized=f"revenue-{uuid4().hex}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(actor)
    db.flush()
    student = _person(db, "分成测试学员")
    recipients = tuple(_person(db, name) for name in ("主教练甲", "主教练乙", "助教甲", "助教乙"))
    cohort = Cohort(
        name=f"分成测试营期-{uuid4().hex[:8]}",
        starts_at=NOW,
        ends_at=NOW + timedelta(days=21),
        default_check_in_at=NOW,
        default_check_out_at=NOW + timedelta(days=21),
        status="active",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=actor.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=f"分成测试班-{uuid4().hex[:8]}",
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=100_000,
        is_active=True,
        created_by_user_id=actor.id,
    )
    db.add(teaching_class)
    admission_case = AdmissionCase(
        student_person_id=student.id,
        status="ready",
        agreed_tuition_cents=100_000,
        created_by_user_id=actor.id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=student.id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=100_000,
        status="active",
        confirmed_at=NOW,
        created_by_user_id=actor.id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    db.flush()
    return RevenueShareScenario(
        actor=actor,
        student=student,
        recipients=recipients,
        admission_case=admission_case,
        enrollment=enrollment,
        teaching_class=teaching_class,
    )


def _rule(
    db: Session,
    scenario: RevenueShareScenario,
    *,
    role_kind: str,
    calculation_type: str = "percentage",
    percentage_basis_points: int | None = 1_000,
    fixed_amount_cents: int | None = None,
    recipient_person_id: UUID | None = None,
) -> RevenueShareRule:
    row = RevenueShareRule(
        project_id=PROJECT_ID,
        role_kind=role_kind,
        recipient_person_id=recipient_person_id,
        recipient_organization_id=None,
        label=None,
        calculation_type=calculation_type,
        percentage_basis_points=percentage_basis_points,
        fixed_amount_cents=fixed_amount_cents,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        is_active=True,
        created_by_user_id=scenario.actor.id,
    )
    db.add(row)
    db.flush()
    return row


def _payment(
    db: Session,
    scenario: RevenueShareScenario,
    *,
    amount_cents: int = 100_000,
    paid_at: datetime = NOW,
) -> Payment:
    row = Payment(
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=None,
        payer_person_id=scenario.student.id,
        amount_cents=amount_cents,
        currency="CNY",
        paid_at=paid_at,
        manual_note="分成服务测试付款",
        created_by_user_id=scenario.actor.id,
    )
    db.add(row)
    db.flush()
    db.add(
        AdmissionCasePayment(
            admission_case_id=scenario.admission_case.id,
            payment_id=row.id,
            attached_by_user_id=scenario.actor.id,
        )
    )
    db.flush()
    return row


def _earnings(db: Session, payment_id: UUID) -> list[RevenueShareLedgerEntry]:
    return list(
        db.scalars(
            select(RevenueShareLedgerEntry)
            .where(
                RevenueShareLedgerEntry.payment_id == payment_id,
                RevenueShareLedgerEntry.entry_kind == "earning",
            )
            .order_by(RevenueShareLedgerEntry.amount_cents, RevenueShareLedgerEntry.id)
        )
    )


def _funded_settlement(db: Session, scenario: RevenueShareScenario) -> RevenueShareSettlementCreate:
    recipient = scenario.recipients[0]
    _rule(db, scenario, role_kind="additional", recipient_person_id=recipient.id)
    payment = _payment(db, scenario)
    RevenueShareService(db).reconcile_payment_in_transaction(payment.id)
    return RevenueShareSettlementCreate(
        idempotency_key=uuid4(),
        beneficiary_kind="person",
        beneficiary_id=recipient.id,
        amount_cents=5_000,
        settled_on=date(2026, 8, 16),
        reference="BANK-00001",
    )


def test_settlement_retry_returns_original_amount_and_single_audit(
    db: Session, revenue_scenario: RevenueShareScenario
) -> None:
    input = _funded_settlement(db, revenue_scenario)
    service = RevenueShareService(db)
    first = service.create_settlement(
        input, actor_user_id=revenue_scenario.actor.id, request_id="first"
    )
    replay = service.create_settlement(
        input, actor_user_id=revenue_scenario.actor.id, request_id="retry"
    )
    assert replay.id == first.id
    assert service.balance_for("person", input.beneficiary_id).settled_cents == 5_000
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.resource_id == first.id,
                AuditEvent.action == "revenue_share.settlement.create",
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "change",
    [
        {"amount_cents": 4_000},
        {"reference": "BANK-CHANGED"},
        {"note": "修改备注"},
        {"settled_on": date(2026, 8, 17)},
        {"beneficiary_id": uuid4()},
    ],
)
def test_settlement_reused_operation_rejects_changed_payload(
    db: Session, revenue_scenario: RevenueShareScenario, change: dict[str, object]
) -> None:
    input = _funded_settlement(db, revenue_scenario)
    service = RevenueShareService(db)
    service.create_settlement(input, actor_user_id=revenue_scenario.actor.id, request_id="first")
    with pytest.raises(RevenueShareSettlementOperationConflict):
        service.create_settlement(
            input.model_copy(update=change),
            actor_user_id=revenue_scenario.actor.id,
            request_id="changed-retry",
        )
    assert service.balance_for("person", input.beneficiary_id).settled_cents == 5_000


def test_settlement_duplicate_reference_rejected_across_operations_and_operators(
    db: Session, revenue_scenario: RevenueShareScenario
) -> None:
    input = _funded_settlement(db, revenue_scenario)
    service = RevenueShareService(db)
    other_actor = UserAccount(
        person_id=revenue_scenario.recipients[1].id,
        email_normalized=f"finance-{uuid4().hex}@example.test",
        password_hash="unused",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(other_actor)
    db.flush()
    other_actor_id = other_actor.id
    service.create_settlement(input, actor_user_id=revenue_scenario.actor.id, request_id="first")
    with pytest.raises(RevenueShareSettlementReferenceConflict):
        service.create_settlement(
            input.model_copy(update={"idempotency_key": uuid4(), "reference": "bank- 00001"}),
            actor_user_id=other_actor_id,
            request_id="other-operator",
        )
    assert service.balance_for("person", input.beneficiary_id).settled_cents == 5_000


def test_settlement_replay_works_after_all_balance_is_settled(
    db: Session, revenue_scenario: RevenueShareScenario
) -> None:
    input = _funded_settlement(db, revenue_scenario).model_copy(update={"amount_cents": 10_000})
    service = RevenueShareService(db)
    first = service.create_settlement(
        input, actor_user_id=revenue_scenario.actor.id, request_id="first"
    )
    assert service.balance_for("person", input.beneficiary_id).outstanding_cents == 0
    assert (
        service.create_settlement(
            input, actor_user_id=revenue_scenario.actor.id, request_id="retry"
        ).id
        == first.id
    )
    with pytest.raises(RevenueShareSettlementExceedsBalance):
        service.create_settlement(
            input.model_copy(update={"idempotency_key": uuid4(), "reference": "BANK-00002"}),
            actor_user_id=revenue_scenario.actor.id,
            request_id="new-overdraw",
        )


def test_settlement_and_operation_key_roll_back_when_audit_fails(
    db: Session, revenue_scenario: RevenueShareScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    input = _funded_settlement(db, revenue_scenario)
    db.commit()
    actor_id = revenue_scenario.actor.id
    service = RevenueShareService(db)

    def fail_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(service, "_audit", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            service.create_settlement(input, actor_user_id=actor_id, request_id="failed")
    assert (
        db.scalar(
            select(func.count())
            .select_from(RevenueShareSettlement)
            .where(RevenueShareSettlement.idempotency_key == input.idempotency_key)
        )
        == 0
    )
    row = service.create_settlement(input, actor_user_id=actor_id, request_id="retry")
    assert row.amount_cents == 5_000


def test_percentage_rule_allocates_from_payment_amount(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    recipient = revenue_scenario.recipients[0]
    _rule(
        db,
        revenue_scenario,
        role_kind="platform_developer",
        recipient_person_id=recipient.id,
    )
    payment = _payment(db, revenue_scenario)

    result = RevenueShareService(db).reconcile_payment_in_transaction(payment.id)

    entries = _earnings(db, payment.id)
    assert result.state == "allocated"
    assert [(entry.beneficiary_person_id, entry.amount_cents) for entry in entries] == [
        (recipient.id, 10_000)
    ]
    assert entries[0].percentage_basis_points_snapshot == 1_000


def test_fixed_rule_never_exceeds_total_when_installments_round_up(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    revenue_scenario.enrollment.agreed_tuition_cents = 3
    rule = _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        calculation_type="fixed",
        percentage_basis_points=None,
        fixed_amount_cents=2,
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    payments = [_payment(db, revenue_scenario, amount_cents=1) for _ in range(3)]
    service = RevenueShareService(db)

    for payment in payments:
        assert service.reconcile_payment_in_transaction(payment.id).state == "allocated"

    allocated = db.scalar(
        select(func.coalesce(func.sum(RevenueShareLedgerEntry.amount_cents), 0)).where(
            RevenueShareLedgerEntry.rule_id == rule.id,
            RevenueShareLedgerEntry.entry_kind == "earning",
        )
    )
    assert allocated == 2


def test_fixed_share_capacity_is_restored_after_full_refund_and_repayment(
    db: Session, revenue_scenario: RevenueShareScenario,
) -> None:
    rule = _rule(
        db, revenue_scenario, role_kind="additional", calculation_type="fixed",
        percentage_basis_points=None, fixed_amount_cents=20_000,
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    service = RevenueShareService(db)
    original = _payment(db, revenue_scenario, amount_cents=100_000)
    service.reconcile_payment_in_transaction(original.id)
    db.add(PaymentRefund(
        payment_id=original.id, amount_cents=100_000, refunded_at=NOW,
        refund_type="manual", reason="原收款全额退回后补款", created_by_user_id=revenue_scenario.actor.id,
    ))
    db.flush()
    service.reconcile_payment_in_transaction(original.id)
    db.flush()
    assert db.scalar(select(func.sum(RevenueShareLedgerEntry.amount_cents)).where(
        RevenueShareLedgerEntry.rule_id == rule.id,
    )) == 0
    replacement = _payment(db, revenue_scenario, amount_cents=100_000)
    service.reconcile_payment_in_transaction(replacement.id)
    db.flush()
    assert sum(entry.amount_cents for entry in _earnings(db, replacement.id)) == 20_000
    assert db.scalar(select(func.sum(RevenueShareLedgerEntry.amount_cents)).where(
        RevenueShareLedgerEntry.rule_id == rule.id,
    )) == 20_000


@pytest.mark.parametrize("same_business_date", [False, True])
def test_later_refund_preserves_existing_rounding_when_backdated_or_same_day(
    db: Session, revenue_scenario: RevenueShareScenario, same_business_date: bool,
) -> None:
    _rule(
        db, revenue_scenario, role_kind="additional", percentage_basis_points=3333,
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    payment = _payment(db, revenue_scenario, amount_cents=10_000)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    first_id = UUID(int=uuid4().int | 1)
    first_date = NOW + timedelta(days=2)
    first = PaymentRefund(
        id=first_id, payment_id=payment.id, amount_cents=5000,
        refunded_at=first_date, created_at=NOW, refund_type="manual",
        reason="首笔退款", created_by_user_id=revenue_scenario.actor.id,
    )
    db.add(first)
    db.flush()
    service.reconcile_payment_in_transaction(payment.id)
    db.flush()
    first_adjustment = db.scalar(select(RevenueShareLedgerEntry).where(
        RevenueShareLedgerEntry.refund_id == first.id,
    ))
    assert first_adjustment is not None and first_adjustment.amount_cents == -1667

    later = PaymentRefund(
        id=UUID(int=first_id.int - 1), payment_id=payment.id, amount_cents=5000,
        refunded_at=first_date if same_business_date else NOW + timedelta(days=1),
        created_at=NOW, refund_type="manual", reason="后登记但日期更早或相同的退款",
        created_by_user_id=revenue_scenario.actor.id,
    )
    db.add(later)
    db.flush()
    service.reconcile_payment_in_transaction(payment.id)
    db.flush()
    later_adjustment = db.scalar(select(RevenueShareLedgerEntry).where(
        RevenueShareLedgerEntry.refund_id == later.id,
    ))
    assert later_adjustment is not None and later_adjustment.amount_cents == -1666
    db.refresh(first_adjustment)
    assert first_adjustment.amount_cents == -1667
    service.reconcile_payment_in_transaction(payment.id)
    db.flush()
    entries = list(db.scalars(select(RevenueShareLedgerEntry).where(
        RevenueShareLedgerEntry.payment_id == payment.id,
    )))
    assert len(entries) == 3
    assert sum(entry.amount_cents for entry in entries) == 0


def test_assistant_share_is_split_evenly(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    assistants = revenue_scenario.recipients[2:]
    for assistant in assistants:
        db.add(
            CoachAssignment(
                class_id=revenue_scenario.teaching_class.id,
                coach_person_id=assistant.id,
                assignment_type="assistant",
                valid_from=NOW - timedelta(days=1),
                valid_until=None,
                created_by_user_id=revenue_scenario.actor.id,
            )
        )
    _rule(db, revenue_scenario, role_kind="assistant")
    payment = _payment(db, revenue_scenario, amount_cents=100_000)

    RevenueShareService(db).reconcile_payment_in_transaction(payment.id)

    assert [entry.amount_cents for entry in _earnings(db, payment.id)] == [5_000, 5_000]


def test_coach_is_resolved_at_payment_time_not_current_time(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    historical, future = revenue_scenario.recipients[:2]
    db.add_all(
        [
            CoachAssignment(
                class_id=revenue_scenario.teaching_class.id,
                coach_person_id=historical.id,
                assignment_type="primary",
                valid_from=NOW - timedelta(days=10),
                valid_until=None,
                ended_at=NOW + timedelta(days=1),
                ended_by_user_id=revenue_scenario.actor.id,
                end_reason="付款后完成交接",
                created_by_user_id=revenue_scenario.actor.id,
            ),
            CoachAssignment(
                class_id=revenue_scenario.teaching_class.id,
                coach_person_id=future.id,
                assignment_type="primary",
                valid_from=NOW + timedelta(days=1),
                valid_until=None,
                created_by_user_id=revenue_scenario.actor.id,
            ),
        ]
    )
    _rule(db, revenue_scenario, role_kind="subject_coach")
    payment = _payment(db, revenue_scenario)

    RevenueShareService(db).reconcile_payment_in_transaction(payment.id)

    assert [entry.beneficiary_person_id for entry in _earnings(db, payment.id)] == [historical.id]


def test_inviter_rule_uses_bound_organization_snapshot(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    organization = RevenueShareOrganization(
        name=f"邀请机构-{uuid4().hex[:8]}",
        is_active=True,
        created_by_user_id=revenue_scenario.actor.id,
    )
    db.add(organization)
    db.flush()
    code = ReferralCode(
        code=f"ORG-{uuid4().hex[:8].upper()}",
        label="机构邀请",
        source_kind="organization",
        referrer_person_id=None,
        referrer_organization_id=organization.id,
        is_active=True,
        created_by_user_id=revenue_scenario.actor.id,
    )
    db.add(code)
    db.flush()
    db.add(
        StudentReferralBinding(
            student_person_id=revenue_scenario.student.id,
            referral_code_id=code.id,
            registration_application_id=None,
            source_kind="organization",
            referrer_person_id=None,
            referrer_organization_id=organization.id,
            bound_at=NOW,
            created_by_user_id=revenue_scenario.actor.id,
        )
    )
    _rule(db, revenue_scenario, role_kind="inviter")
    payment = _payment(db, revenue_scenario)

    RevenueShareService(db).reconcile_payment_in_transaction(payment.id)

    entry = _earnings(db, payment.id)[0]
    assert entry.beneficiary_kind == "organization"
    assert entry.beneficiary_organization_id == organization.id


def test_inviter_rule_allocates_confirmed_payment_to_bound_person(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    inviter = revenue_scenario.recipients[0]
    code = ReferralCode(
        code=f"PERSON-{uuid4().hex[:8].upper()}",
        label="个人邀请",
        source_kind="person",
        referrer_person_id=inviter.id,
        referrer_organization_id=None,
        is_active=True,
        created_by_user_id=revenue_scenario.actor.id,
    )
    db.add(code)
    db.flush()
    db.add(
        StudentReferralBinding(
            student_person_id=revenue_scenario.student.id,
            referral_code_id=code.id,
            registration_application_id=None,
            source_kind="person",
            referrer_person_id=inviter.id,
            referrer_organization_id=None,
            bound_at=NOW,
            created_by_user_id=revenue_scenario.actor.id,
        )
    )
    _rule(db, revenue_scenario, role_kind="inviter")
    payment = _payment(db, revenue_scenario, amount_cents=100_000)

    result = RevenueShareService(db).reconcile_payment_in_transaction(payment.id)

    entry = _earnings(db, payment.id)[0]
    assert result.state == "allocated"
    assert entry.beneficiary_kind == "person"
    assert entry.beneficiary_person_id == inviter.id
    assert entry.amount_cents == 10_000


def test_refund_after_settlement_creates_negative_outstanding_balance(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    recipient = revenue_scenario.recipients[0]
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        recipient_person_id=recipient.id,
    )
    payment = _payment(db, revenue_scenario, amount_cents=10_000)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    db.add(
        RevenueShareSettlement(
            beneficiary_kind="person",
            beneficiary_person_id=recipient.id,
            beneficiary_organization_id=None,
            amount_cents=1_000,
            settled_on=date(2026, 8, 16),
            reference="TEST-SETTLED",
            note=None,
            created_by_user_id=revenue_scenario.actor.id,
        )
    )
    refund = PaymentRefund(
        payment_id=payment.id,
        withdrawal_id=None,
        amount_cents=payment.amount_cents,
        refunded_at=NOW + timedelta(days=2),
        refund_type="manual",
        reason="全额退款测试",
        created_by_user_id=revenue_scenario.actor.id,
    )
    db.add(refund)
    db.flush()

    service.reconcile_payment_in_transaction(payment.id)

    adjustments = list(
        db.scalars(
            select(RevenueShareLedgerEntry).where(
                RevenueShareLedgerEntry.payment_id == payment.id,
                RevenueShareLedgerEntry.entry_kind == "refund_adjustment",
            )
        )
    )
    balance = service.balance_for("person", recipient.id)
    assert [entry.amount_cents for entry in adjustments] == [-1_000]
    assert (balance.earned_cents, balance.settled_cents, balance.outstanding_cents) == (
        0,
        1_000,
        -1_000,
    )


def test_existing_payment_keeps_original_rule_snapshot(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    recipient = revenue_scenario.recipients[0]
    old_rule = _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        recipient_person_id=recipient.id,
    )
    payment = _payment(db, revenue_scenario)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    old_rule.is_active = False
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        percentage_basis_points=5_000,
        recipient_person_id=recipient.id,
    )

    service.reconcile_payment_in_transaction(payment.id)

    entries = _earnings(db, payment.id)
    assert len(entries) == 1
    assert (entries[0].amount_cents, entries[0].percentage_basis_points_snapshot) == (
        10_000,
        1_000,
    )


def test_incremental_batches_skip_allocated_and_bound_new_arrivals(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    payments = [
        _payment(db, revenue_scenario, paid_at=NOW + timedelta(seconds=i)) for i in range(5)
    ]
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payments[0].id)
    db.commit()
    actor_id = revenue_scenario.actor.id
    first = service.reconcile_batch(batch_size=2, actor_user_id=actor_id, request_id="batch-first")
    assert (first.processed, first.allocated, first.has_more) == (2, 2, True)
    later = _payment(db, revenue_scenario, paid_at=NOW + timedelta(days=1))
    db.commit()
    second = service.reconcile_batch(
        batch_size=2, cursor=first.next_cursor, actor_user_id=actor_id, request_id="batch-second"
    )
    assert (second.processed, second.allocated, second.has_more) == (2, 2, False)
    assert db.get(RevenueShareReconcileQueue, later.id) is not None
    third = service.reconcile_batch(actor_user_id=actor_id, request_id="batch-new")
    assert (third.processed, third.allocated, third.has_more) == (1, 1, False)
    assert service.reconcile_batch(actor_user_id=actor_id, request_id="batch-empty").processed == 0
    assert db.scalar(select(func.count()).select_from(RevenueShareLedgerEntry)) == 6
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "revenue_share.reconcile_all")
        )
        == 5
    )


def test_pending_batch_advances_and_later_rule_can_allocate_zero_earnings(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    service = RevenueShareService(db)
    payments = [
        _payment(db, revenue_scenario, amount_cents=1, paid_at=NOW + timedelta(seconds=i))
        for i in range(3)
    ]
    db.commit()
    actor_id = revenue_scenario.actor.id
    first = service.reconcile_batch(batch_size=2, actor_user_id=actor_id, request_id="pending-1")
    second = service.reconcile_batch(
        batch_size=2, cursor=first.next_cursor, actor_user_id=actor_id, request_id="pending-2"
    )
    assert (first.pending, first.has_more, second.pending, second.has_more) == (2, True, 1, False)
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    db.commit()
    assert service.reconcile_batch(actor_user_id=actor_id, request_id="zero-earning").allocated == 3
    assert db.scalar(select(func.count()).select_from(RevenueShareReconcileQueue)) == 3
    assert all(not _earnings(db, payment.id) for payment in payments)
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        percentage_basis_points=10_000,
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    db.commit()
    assert service.reconcile_batch(actor_user_id=actor_id, request_id="filled-rule").allocated == 3
    assert db.scalar(select(func.count()).select_from(RevenueShareReconcileQueue)) == 0
    assert [sum(e.amount_cents for e in _earnings(db, p.id)) for p in payments] == [1, 1, 1]


def test_backdated_refund_requeues_only_affected_payment(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    payment = _payment(db, revenue_scenario)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    db.commit()
    assert db.get(RevenueShareReconcileQueue, payment.id) is None
    db.add(
        PaymentRefund(
            payment_id=payment.id,
            amount_cents=50_000,
            refunded_at=NOW + timedelta(days=1),
            refund_type="manual",
            reason="补录过去退款",
            created_by_user_id=revenue_scenario.actor.id,
        )
    )
    db.commit()
    assert db.get(RevenueShareReconcileQueue, payment.id) is not None
    result = service.reconcile_batch(actor_user_id=revenue_scenario.actor.id, request_id="refund")
    assert result.processed == 1
    assert service.balance_for("person", revenue_scenario.recipients[0].id).earned_cents == 5_000
    assert db.get(RevenueShareReconcileQueue, payment.id) is None
    assert (
        service.reconcile_batch(
            actor_user_id=revenue_scenario.actor.id, request_id="refund-retry"
        ).processed
        == 0
    )
    assert db.scalar(select(func.count()).select_from(RevenueShareLedgerEntry)) == 2


def test_reconciliation_audit_failure_rolls_back_earning_and_keeps_queue(
    db: Session,
    revenue_scenario: RevenueShareScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    payment = _payment(db, revenue_scenario)
    db.commit()
    payment_id, actor_id = payment.id, revenue_scenario.actor.id

    def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(RevenueShareService, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="simulated audit failure"):
        RevenueShareService(db).reconcile_batch(actor_user_id=actor_id, request_id="audit-fail")
    assert not _earnings(db, payment_id)
    assert db.get(RevenueShareReconcileQueue, payment_id) is not None


def test_many_refunds_use_bounded_queries_and_keep_rounding_and_idempotency(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    payment = _payment(db, revenue_scenario)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    for index in range(20):
        db.add(
            PaymentRefund(
                payment_id=payment.id,
                amount_cents=1000,
                refunded_at=NOW + timedelta(days=index + 1),
                refund_type="manual",
                reason="分笔退款",
                created_by_user_id=revenue_scenario.actor.id,
            )
        )
    db.flush()
    payment_id = payment.id
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        service.reconcile_payment_in_transaction(payment_id)
        db.flush()
        assert len(statements) <= 10
        statements.clear()
        service.reconcile_payment_in_transaction(payment_id)
        db.flush()
        assert len(statements) <= 10
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert db.scalar(select(func.count()).select_from(RevenueShareLedgerEntry)) == 21
    assert service.balance_for("person", revenue_scenario.recipients[0].id).earned_cents == 8000


def test_personal_ledger_uses_bounded_stable_cursor_and_owner_scoped_balance(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    payment = _payment(db, revenue_scenario)
    owner, other = revenue_scenario.recipients[:2]
    for recipient, count in ((owner, 7), (other, 3)):
        for _ in range(count):
            db.add(
                RevenueShareLedgerEntry(
                    event_key=f"page:{uuid4()}",
                    payment_id=payment.id,
                    entry_kind="earning",
                    role_kind="additional",
                    beneficiary_kind="person",
                    beneficiary_person_id=recipient.id,
                    calculation_type="percentage",
                    percentage_basis_points_snapshot=1000,
                    base_amount_cents=100_000,
                    amount_cents=1000,
                    occurred_at=NOW,
                    created_by_user_id=revenue_scenario.actor.id,
                )
            )
    db.commit()
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.upper())

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        service = RevenueShareService(db)
        balance = service.balance_for("person", owner.id)
        assert balance.earned_cents == 7000
        assert not any("GROUP BY" in statement for statement in statements)
        assert all(
            "WHERE" in statement and "BENEFICIARY_PERSON_ID" in statement
            for statement in statements
            if "SUM(" in statement
        )
        seen: list[UUID] = []
        cursor = None
        while True:
            page = service.self_ledger(owner.id, page_size=3, cursor=cursor)
            assert page.total == 7
            assert len(page.items) <= 3
            assert all(item.beneficiary_person_id == owner.id for item in page.items)
            seen.extend(item.id for item in page.items)
            if not page.has_more:
                break
            cursor = page.next_cursor
        assert len(seen) == len(set(seen)) == 7
        assert seen == sorted(seen, reverse=True)
        item_queries = [s for s in statements if "REVENUE_SHARE_LEDGER_ENTRIES.EVENT_KEY" in s]
        assert item_queries and all("LIMIT" in s and "OFFSET" not in s for s in item_queries)
    finally:
        event.remove(bind, "before_cursor_execute", capture)
