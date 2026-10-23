from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import Payment, PaymentRefund
from app.revenue_share.models import RevenueShareLedgerEntry
from app.revenue_share.service import RevenueShareService
from tests.services.test_revenue_share import NOW, RevenueShareScenario, _earnings, _payment, _rule
from tests.services.test_revenue_share import revenue_scenario as revenue_scenario_fixture

revenue_scenario = revenue_scenario_fixture


def _add_refund(
    db: Session,
    scenario: RevenueShareScenario,
    payment: Payment,
    amount_cents: int,
    *,
    day: int,
) -> PaymentRefund:
    row = PaymentRefund(
        payment_id=payment.id,
        amount_cents=amount_cents,
        refunded_at=NOW + timedelta(days=day),
        refund_type="manual",
        reason=f"部分退款守恒测试-{day}",
        created_by_user_id=scenario.actor.id,
    )
    db.add(row)
    db.flush()
    return row


def _adjustments(db: Session, payment_id) -> list[RevenueShareLedgerEntry]:
    return list(
        db.scalars(
            select(RevenueShareLedgerEntry)
            .where(
                RevenueShareLedgerEntry.payment_id == payment_id,
                RevenueShareLedgerEntry.entry_kind == "refund_adjustment",
            )
            .order_by(RevenueShareLedgerEntry.created_at, RevenueShareLedgerEntry.id)
        )
    )


def test_partial_refund_conserves_two_cent_combined_pool(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    for recipient in revenue_scenario.recipients[:2]:
        _rule(
            db,
            revenue_scenario,
            role_kind="additional",
            percentage_basis_points=5_000,
            recipient_person_id=recipient.id,
        )
    payment = _payment(db, revenue_scenario, amount_cents=2)
    service = RevenueShareService(db)
    assert service.reconcile_payment_in_transaction(payment.id).state == "allocated"
    assert sorted(row.amount_cents for row in _earnings(db, payment.id)) == [1, 1]

    _add_refund(db, revenue_scenario, payment, 1, day=1)
    service.reconcile_payment_in_transaction(payment.id)

    adjustments = _adjustments(db, payment.id)
    assert [row.amount_cents for row in adjustments] == [-1]
    assert sum(row.amount_cents for row in adjustments) == -1


def test_multi_step_nonuniform_refunds_stay_monotone_and_full_refund_zeroes_pool(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    for basis_points, recipient in zip(
        (1_667, 1_667, 5_000),
        revenue_scenario.recipients[:3],
        strict=True,
    ):
        _rule(
            db,
            revenue_scenario,
            role_kind="additional",
            percentage_basis_points=basis_points,
            recipient_person_id=recipient.id,
        )
    payment = _payment(db, revenue_scenario, amount_cents=6)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    assert sorted(row.amount_cents for row in _earnings(db, payment.id)) == [1, 1, 3]

    expected_cumulative_reversal = (3, 3, 4, 5)
    for day, (refund_cents, expected) in enumerate(
        zip((3, 1, 1, 1), expected_cumulative_reversal, strict=True),
        start=1,
    ):
        _add_refund(db, revenue_scenario, payment, refund_cents, day=day)
        service.reconcile_payment_in_transaction(payment.id)
        assert -sum(row.amount_cents for row in _adjustments(db, payment.id)) == expected

    earnings = _earnings(db, payment.id)
    reversed_by_parent = {
        earning.id: -sum(
            row.amount_cents
            for row in _adjustments(db, payment.id)
            if row.parent_entry_id == earning.id
        )
        for earning in earnings
    }
    assert all(reversed_by_parent[row.id] <= row.amount_cents for row in earnings)
    assert (
        db.scalar(
            select(func.sum(RevenueShareLedgerEntry.amount_cents)).where(
                RevenueShareLedgerEntry.payment_id == payment.id
            )
        )
        == 0
    )


def test_uneven_percentage_shares_use_hand_derived_cumulative_targets(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    for basis_points, recipient in zip(
        (1_000, 2_000, 4_000),
        revenue_scenario.recipients[:3],
        strict=True,
    ):
        _rule(
            db,
            revenue_scenario,
            role_kind="additional",
            percentage_basis_points=basis_points,
            recipient_person_id=recipient.id,
        )
    payment = _payment(db, revenue_scenario, amount_cents=10)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    earnings = _earnings(db, payment.id)
    assert sorted(row.amount_cents for row in earnings) == [1, 2, 4]

    _add_refund(db, revenue_scenario, payment, 3, day=1)
    service.reconcile_payment_in_transaction(payment.id)

    reversed_by_earning_amount = {
        earning.amount_cents: -sum(
            row.amount_cents
            for row in _adjustments(db, payment.id)
            if row.parent_entry_id == earning.id
        )
        for earning in earnings
    }
    assert reversed_by_earning_amount == {1: 0, 2: 1, 4: 1}


def test_fixed_amount_earnings_share_one_conserved_refund_pool(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    revenue_scenario.enrollment.agreed_tuition_cents = 6
    for fixed_amount, recipient in zip(
        (2, 3),
        revenue_scenario.recipients[:2],
        strict=True,
    ):
        _rule(
            db,
            revenue_scenario,
            role_kind="additional",
            calculation_type="fixed",
            percentage_basis_points=None,
            fixed_amount_cents=fixed_amount,
            recipient_person_id=recipient.id,
        )
    payment = _payment(db, revenue_scenario, amount_cents=6)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    assert sorted(row.amount_cents for row in _earnings(db, payment.id)) == [2, 3]

    _add_refund(db, revenue_scenario, payment, 3, day=1)
    service.reconcile_payment_in_transaction(payment.id)
    assert -sum(row.amount_cents for row in _adjustments(db, payment.id)) == 3

    _add_refund(db, revenue_scenario, payment, 3, day=2)
    service.reconcile_payment_in_transaction(payment.id)
    assert -sum(row.amount_cents for row in _adjustments(db, payment.id)) == 5


def test_refund_reconciliation_retry_keeps_event_keys_and_rows_idempotent(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    for recipient in revenue_scenario.recipients[:2]:
        _rule(
            db,
            revenue_scenario,
            role_kind="additional",
            percentage_basis_points=5_000,
            recipient_person_id=recipient.id,
        )
    payment = _payment(db, revenue_scenario, amount_cents=2)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    refund = _add_refund(db, revenue_scenario, payment, 1, day=1)
    service.reconcile_payment_in_transaction(payment.id)
    first = _adjustments(db, payment.id)
    assert len(first) == 1
    first_keys = [row.event_key for row in first]
    assert first_keys == [f"refund:{refund.id}:{first[0].parent_entry_id}"]

    service.reconcile_payment_in_transaction(payment.id)

    assert [row.event_key for row in _adjustments(db, payment.id)] == first_keys


def test_legacy_over_reversal_is_left_append_only_and_full_refund_stays_zero(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    for recipient in revenue_scenario.recipients[:2]:
        _rule(
            db,
            revenue_scenario,
            role_kind="additional",
            percentage_basis_points=5_000,
            recipient_person_id=recipient.id,
        )
    payment = _payment(db, revenue_scenario, amount_cents=2)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    earnings = _earnings(db, payment.id)
    partial_refund = _add_refund(db, revenue_scenario, payment, 1, day=1)
    for earning in earnings:
        db.add(
            RevenueShareLedgerEntry(
                event_key=f"refund:{partial_refund.id}:{earning.id}",
                payment_id=payment.id,
                refund_id=partial_refund.id,
                parent_entry_id=earning.id,
                rule_id=earning.rule_id,
                entry_kind="refund_adjustment",
                role_kind=earning.role_kind,
                beneficiary_kind=earning.beneficiary_kind,
                beneficiary_person_id=earning.beneficiary_person_id,
                beneficiary_organization_id=earning.beneficiary_organization_id,
                calculation_type=earning.calculation_type,
                percentage_basis_points_snapshot=earning.percentage_basis_points_snapshot,
                fixed_amount_cents_snapshot=earning.fixed_amount_cents_snapshot,
                base_amount_cents=partial_refund.amount_cents,
                amount_cents=-1,
                occurred_at=partial_refund.refunded_at,
                created_by_user_id=partial_refund.created_by_user_id,
            )
        )
    db.flush()

    service.reconcile_payment_in_transaction(payment.id)
    assert len(_adjustments(db, payment.id)) == 2
    assert sum(row.amount_cents for row in _adjustments(db, payment.id)) == -2

    _add_refund(db, revenue_scenario, payment, 1, day=2)
    service.reconcile_payment_in_transaction(payment.id)
    assert len(_adjustments(db, payment.id)) == 2
    assert (
        db.scalar(
            select(func.sum(RevenueShareLedgerEntry.amount_cents)).where(
                RevenueShareLedgerEntry.payment_id == payment.id
            )
        )
        == 0
    )


def test_legacy_under_reversal_appends_missing_pool_cent_without_rewriting_history(
    db: Session,
    revenue_scenario: RevenueShareScenario,
) -> None:
    for basis_points, recipient in zip(
        (2_000, 6_000),
        revenue_scenario.recipients[:2],
        strict=True,
    ):
        _rule(
            db,
            revenue_scenario,
            role_kind="additional",
            percentage_basis_points=basis_points,
            recipient_person_id=recipient.id,
        )
    payment = _payment(db, revenue_scenario, amount_cents=5)
    service = RevenueShareService(db)
    service.reconcile_payment_in_transaction(payment.id)
    earnings = _earnings(db, payment.id)
    assert sorted(row.amount_cents for row in earnings) == [1, 3]
    refund = _add_refund(db, revenue_scenario, payment, 2, day=1)
    large_earning = next(row for row in earnings if row.amount_cents == 3)
    legacy = RevenueShareLedgerEntry(
        event_key=f"refund:{refund.id}:{large_earning.id}",
        payment_id=payment.id,
        refund_id=refund.id,
        parent_entry_id=large_earning.id,
        rule_id=large_earning.rule_id,
        entry_kind="refund_adjustment",
        role_kind=large_earning.role_kind,
        beneficiary_kind=large_earning.beneficiary_kind,
        beneficiary_person_id=large_earning.beneficiary_person_id,
        beneficiary_organization_id=large_earning.beneficiary_organization_id,
        calculation_type=large_earning.calculation_type,
        percentage_basis_points_snapshot=large_earning.percentage_basis_points_snapshot,
        fixed_amount_cents_snapshot=large_earning.fixed_amount_cents_snapshot,
        base_amount_cents=refund.amount_cents,
        amount_cents=-1,
        occurred_at=refund.refunded_at,
        created_by_user_id=refund.created_by_user_id,
    )
    db.add(legacy)
    db.flush()
    legacy_id = legacy.id

    service.reconcile_payment_in_transaction(payment.id)

    adjustments = _adjustments(db, payment.id)
    assert len(adjustments) == 2
    assert sum(row.amount_cents for row in adjustments) == -2
    assert db.get(RevenueShareLedgerEntry, legacy_id).amount_cents == -1
    service.reconcile_payment_in_transaction(payment.id)
    assert len(_adjustments(db, payment.id)) == 2
