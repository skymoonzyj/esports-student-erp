from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, BrokenBarrierError
from uuid import UUID

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from app.admissions.models import PaymentRefund
from app.revenue_share.errors import RevenueShareConflict
from app.revenue_share.models import RevenueShareLedgerEntry, RevenueShareRule
from app.revenue_share.schemas import RevenueShareRuleCreate
from app.revenue_share.service import RevenueShareService
from tests.services.test_revenue_share import (
    NOW,
    PROJECT_ID,
    _earnings,
    _payment,
    _rule,
)
from tests.services.test_revenue_share import (
    revenue_scenario as revenue_scenario_fixture,
)
from tests.services.test_revenue_share_batch_transactions import (
    committed_payments as committed_payments_fixture,
)

revenue_scenario = revenue_scenario_fixture
committed_payments = committed_payments_fixture


def _create(
    service,
    scenario,
    *,
    project_id=PROJECT_ID,
    role="additional",
    percent="60",
    start=date(2026, 1, 1),
    end=None,
    recipient=0,
):
    return service.create_rule(
        RevenueShareRuleCreate(
            project_id=project_id,
            role_kind=role,
            recipient_person_id=(
                scenario.recipients[recipient].id
                if role in {"additional", "platform_developer"}
                else None
            ),
            calculation_type="percentage",
            percentage=percent,
            effective_from=start,
            effective_to=end,
        ),
        actor_user_id=scenario.actor.id,
        request_id="rule-safety",
    )


def test_concurrent_first_automatic_rule_allows_only_one(postgres_engine, committed_payments):
    actor_id, _, _ = committed_payments
    barrier = Barrier(2)

    def hold_empty_read(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "FROM revenue_share_rules" in statement and "FOR UPDATE" in statement:
            # Without a scope lock both transactions read the empty set. With the
            # lock the first reader times out here, commits, and the second sees it.
            try:
                barrier.wait(timeout=1)
            except BrokenBarrierError:
                pass

    def submit(index):
        with Session(postgres_engine) as session:
            try:
                RevenueShareService(session).create_rule(
                    RevenueShareRuleCreate(
                        role_kind="subject_coach",
                        calculation_type="percentage",
                        percentage="25",
                        effective_from=date(3000, 1, 1),
                    ),
                    actor_user_id=actor_id,
                    request_id=f"concurrent-rule-{index}",
                )
                return "created"
            except RevenueShareConflict:
                return "conflict"

    try:
        event.listen(postgres_engine, "after_cursor_execute", hold_empty_read)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(submit, index) for index in range(2)]
                results = [future.result(timeout=20) for future in futures]
        finally:
            event.remove(postgres_engine, "after_cursor_execute", hold_empty_read)
        assert sorted(results) == ["conflict", "created"]
        with Session(postgres_engine) as check:
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(RevenueShareRule)
                    .where(
                        RevenueShareRule.created_by_user_id == actor_id,
                    )
                )
                == 1
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                delete(RevenueShareRule).where(
                    RevenueShareRule.created_by_user_id == actor_id,
                )
            )


@pytest.mark.parametrize("global_first", [True, False])
def test_effective_global_project_total_above_100_is_rejected(db, revenue_scenario, global_first):
    service = RevenueShareService(db)
    scopes = [(None, "platform_developer"), (PROJECT_ID, "additional")]
    if not global_first:
        scopes.reverse()
    _create(service, revenue_scenario, project_id=scopes[0][0], role=scopes[0][1])
    with pytest.raises(RevenueShareConflict):
        _create(service, revenue_scenario, project_id=scopes[1][0], role=scopes[1][1])
    assert db.scalar(select(func.count()).select_from(RevenueShareRule)) == 1


def test_same_role_project_override_replaces_global_percentage(db, revenue_scenario):
    service = RevenueShareService(db)
    _create(service, revenue_scenario, project_id=None)
    replacement = _create(service, revenue_scenario, percent="100")
    assert service._effective_rules(PROJECT_ID, date(2026, 8, 1)) == [replacement]


def test_disabling_override_cannot_expose_overallocated_global_rules(db, revenue_scenario):
    service = RevenueShareService(db)
    _create(service, revenue_scenario, project_id=None, role="platform_developer", percent="60")
    override = _create(service, revenue_scenario, role="platform_developer", percent="20")
    _create(service, revenue_scenario, percent="60")
    with pytest.raises(RevenueShareConflict):
        service.disable_rule(
            override.id, actor_user_id=revenue_scenario.actor.id, request_id="disable"
        )
    assert db.get(RevenueShareRule, override.id).is_active is True


def test_disjoint_date_percentages_are_not_summed_together(db, revenue_scenario):
    service = RevenueShareService(db)
    _create(service, revenue_scenario, start=date(2026, 1, 1), end=date(2026, 1, 31))
    _create(service, revenue_scenario, start=date(2026, 2, 1), end=date(2026, 2, 28))
    _create(service, revenue_scenario, percent="40", end=date(2026, 2, 28))
    assert len(service._effective_rules(PROJECT_ID, date(2026, 1, 31))) == 2
    assert len(service._effective_rules(PROJECT_ID, date(2026, 2, 1))) == 2


def test_global_project_end_date_is_inclusive(db, revenue_scenario):
    service = RevenueShareService(db)
    _create(
        service, revenue_scenario, project_id=None, role="platform_developer", end=date(2026, 1, 31)
    )
    with pytest.raises(RevenueShareConflict):
        _create(service, revenue_scenario, start=date(2026, 1, 31))
    _create(service, revenue_scenario, start=date(2026, 2, 1))


def test_expiring_override_must_not_expose_excessive_global_total(db, revenue_scenario):
    service = RevenueShareService(db)
    _create(service, revenue_scenario, project_id=None, role="platform_developer")
    _create(
        service, revenue_scenario, role="platform_developer", percent="20", end=date(2026, 1, 31)
    )
    with pytest.raises(RevenueShareConflict):
        _create(service, revenue_scenario)


@pytest.mark.parametrize(
    "basis_points, fixed_cents, expected",
    [
        (5000, None, [51, 50]),
        (2500, None, [26, 25]),
        (2500, 50, [26, 25, 50]),
        (3333, None, [34, 33]),
    ],
)
def test_percentage_pool_uses_stable_largest_remainder(
    db,
    revenue_scenario,
    basis_points,
    fixed_cents,
    expected,
):
    rules = []
    # Deliberately insert reverse UUID order at the same timestamp: a stable tie
    # must favor rule 1, independent of insertion and role grouping order.
    for index in (2, 1):
        rule = _rule(
            db,
            revenue_scenario,
            role_kind="additional",
            percentage_basis_points=basis_points,
            recipient_person_id=revenue_scenario.recipients[index - 1].id,
        )
        rule.id = UUID(int=index)
        rule.created_at = NOW
        rules.append(rule)
    if fixed_cents is not None:
        rule = _rule(
            db,
            revenue_scenario,
            role_kind="platform_developer",
            calculation_type="fixed",
            percentage_basis_points=None,
            fixed_amount_cents=fixed_cents,
            recipient_person_id=revenue_scenario.recipients[2].id,
        )
        rule.id = UUID(int=3)
        revenue_scenario.enrollment.agreed_tuition_cents = 101
    payment = _payment(db, revenue_scenario, amount_cents=101)
    service = RevenueShareService(db)
    assert service.reconcile_payment_in_transaction(payment.id).state == "allocated"
    entries = sorted(_earnings(db, payment.id), key=lambda entry: entry.rule_id.int)
    assert [entry.amount_cents for entry in entries] == expected
    ids = [entry.id for entry in entries]
    assert service.reconcile_payment_in_transaction(payment.id).state == "allocated"
    assert sorted(entry.id for entry in _earnings(db, payment.id)) == sorted(ids)
    db.add(
        PaymentRefund(
            payment_id=payment.id,
            amount_cents=101,
            refunded_at=NOW,
            refund_type="manual",
            reason="全额退费",
            created_by_user_id=revenue_scenario.actor.id,
        )
    )
    db.flush()
    service.reconcile_payment_in_transaction(payment.id)
    assert (
        db.scalar(
            select(func.sum(RevenueShareLedgerEntry.amount_cents)).where(
                RevenueShareLedgerEntry.payment_id == payment.id,
            )
        )
        == 0
    )


def test_missing_inviter_does_not_take_another_rules_rounding_cent(db, revenue_scenario):
    _rule(db, revenue_scenario, role_kind="inviter", percentage_basis_points=5000)
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        percentage_basis_points=5000,
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    payment = _payment(db, revenue_scenario, amount_cents=101)
    result = RevenueShareService(db).reconcile_payment_in_transaction(payment.id)
    assert result.state == "allocated"
    assert [entry.amount_cents for entry in _earnings(db, payment.id)] == [51]


@pytest.mark.parametrize("fixed", [True, False])
def test_genuine_overallocation_remains_pending(db, revenue_scenario, fixed):
    # Direct inserts stand for preexisting configuration, not accepted new rules.
    _rule(
        db,
        revenue_scenario,
        role_kind="additional",
        percentage_basis_points=6000,
        recipient_person_id=revenue_scenario.recipients[0].id,
    )
    if fixed:
        revenue_scenario.enrollment.agreed_tuition_cents = 101
        _rule(
            db,
            revenue_scenario,
            role_kind="platform_developer",
            calculation_type="fixed",
            percentage_basis_points=None,
            fixed_amount_cents=60,
            recipient_person_id=revenue_scenario.recipients[1].id,
        )
    else:
        _rule(
            db,
            revenue_scenario,
            role_kind="platform_developer",
            percentage_basis_points=6000,
            recipient_person_id=revenue_scenario.recipients[1].id,
        )
    payment = _payment(db, revenue_scenario, amount_cents=101)
    result = RevenueShareService(db).reconcile_payment_in_transaction(payment.id)
    assert (result.state, result.reason_code) == ("pending", "allocation_exceeds_payment")
    assert _earnings(db, payment.id) == []
