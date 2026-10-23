from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from itertools import product
from types import SimpleNamespace
from uuid import UUID

from app.revenue_share.service import RevenueShareService


class MemorySession:
    def __init__(self, earnings, refunds, adjustments=()):
        self.results = iter((earnings, refunds, adjustments))
        self.added = []

    def scalars(self, _statement):
        return next(self.results)

    def add(self, row):
        self.added.append(row)


def _earning(index: int, amount_cents: int, *, calculation_type: str = "percentage"):
    return SimpleNamespace(
        id=UUID(int=index),
        amount_cents=amount_cents,
        rule_id=UUID(int=100 + index),
        role_kind="additional",
        beneficiary_kind="person",
        beneficiary_person_id=UUID(int=200 + index),
        beneficiary_organization_id=None,
        calculation_type=calculation_type,
        percentage_basis_points_snapshot=(5000 if calculation_type == "percentage" else None),
        fixed_amount_cents_snapshot=(amount_cents if calculation_type == "fixed" else None),
    )


def _refund(index: int, amount_cents: int, occurred_at: datetime):
    return SimpleNamespace(
        id=UUID(int=1000 + index),
        amount_cents=amount_cents,
        refunded_at=occurred_at,
        created_by_user_id=UUID(int=300),
    )


def test_partial_refund_conserves_combined_earning_pool() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    payment = SimpleNamespace(id=UUID(int=10), amount_cents=2)
    earnings = [_earning(1, 1), _earning(2, 1)]
    refund = _refund(1, 1, now)
    session = MemorySession(earnings, [refund])

    RevenueShareService(session)._reconcile_refunds(payment)

    assert [(row.parent_entry_id, row.amount_cents) for row in session.added] == [(UUID(int=1), -1)]


def test_multiple_remainder_cents_are_spread_by_stable_entry_order() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    payment = SimpleNamespace(id=UUID(int=10), amount_cents=100)
    earnings = [_earning(1, 10), _earning(2, 10), _earning(3, 10)]
    refund = _refund(1, 5, now)
    session = MemorySession(earnings, [refund])

    RevenueShareService(session)._reconcile_refunds(payment)

    assert [(row.parent_entry_id, row.amount_cents) for row in session.added] == [
        (UUID(int=1), -1),
        (UUID(int=2), -1),
    ]


def test_small_cumulative_allocations_conserve_pool_caps_and_monotonicity() -> None:
    for payment_cents in range(1, 13):
        for entry_count in range(1, min(payment_cents, 4) + 1):
            for earning_amounts in product(range(1, 5), repeat=entry_count):
                if sum(earning_amounts) > payment_cents:
                    continue
                earnings = [
                    _earning(index, amount) for index, amount in enumerate(earning_amounts, start=1)
                ]
                previous = {row.id: 0 for row in earnings}
                for refunded_cents in range(1, payment_cents + 1):
                    targets = RevenueShareService._refund_reversal_targets(
                        payment_amount_cents=payment_cents,
                        cumulative_refunded_cents=refunded_cents,
                        earnings=earnings,
                        reversed_by_entry=previous,
                    )
                    expected_pool = int(
                        (
                            Decimal(sum(earning_amounts) * refunded_cents) / Decimal(payment_cents)
                        ).quantize(Decimal(1), rounding=ROUND_HALF_UP)
                    )
                    case = (payment_cents, earning_amounts, refunded_cents)
                    assert sum(targets.values()) == expected_pool, case
                    assert all(
                        previous[row.id] <= targets[row.id] <= row.amount_cents for row in earnings
                    ), case
                    assert (
                        RevenueShareService._refund_reversal_targets(
                            payment_amount_cents=payment_cents,
                            cumulative_refunded_cents=refunded_cents,
                            earnings=earnings,
                            reversed_by_entry=previous,
                        )
                        == targets
                    ), case
                    previous = targets
                assert previous == {row.id: row.amount_cents for row in earnings}


def test_nonuniform_cumulative_targets_never_rewrite_persisted_reversals() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    payment = SimpleNamespace(id=UUID(int=10), amount_cents=6)
    earnings = [_earning(1, 1), _earning(2, 1), _earning(3, 3)]
    refunds = [
        _refund(1, 3, now),
        _refund(2, 1, now),
        _refund(3, 1, now),
        _refund(4, 1, now),
    ]

    first = MemorySession(earnings, refunds[:1])
    RevenueShareService(first)._reconcile_refunds(payment)
    assert [(row.parent_entry_id, row.amount_cents) for row in first.added] == [
        (UUID(int=1), -1),
        (UUID(int=2), -1),
        (UUID(int=3), -1),
    ]

    unchanged = MemorySession(earnings, refunds[:2], first.added)
    RevenueShareService(unchanged)._reconcile_refunds(payment)
    assert unchanged.added == []

    third = MemorySession(earnings, refunds[:3], first.added)
    RevenueShareService(third)._reconcile_refunds(payment)
    assert [(row.parent_entry_id, row.amount_cents) for row in third.added] == [(UUID(int=3), -1)]

    adjustments = first.added + third.added
    full = MemorySession(earnings, refunds, adjustments)
    RevenueShareService(full)._reconcile_refunds(payment)
    assert [(row.parent_entry_id, row.amount_cents) for row in full.added] == [(UUID(int=3), -1)]
    assert sum(row.amount_cents for row in adjustments + full.added) == -5

    replay = MemorySession(earnings, refunds, adjustments + full.added)
    RevenueShareService(replay)._reconcile_refunds(payment)
    assert replay.added == []


def test_legacy_over_reversal_is_preserved_without_new_credit_rows() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    payment = SimpleNamespace(id=UUID(int=10), amount_cents=2)
    earnings = [_earning(1, 1), _earning(2, 1)]
    partial_refund = _refund(1, 1, now)
    legacy_adjustments = [
        SimpleNamespace(
            event_key=f"refund:{partial_refund.id}:{earning.id}",
            refund_id=partial_refund.id,
            parent_entry_id=earning.id,
            amount_cents=-1,
        )
        for earning in earnings
    ]

    partial = MemorySession(earnings, [partial_refund], legacy_adjustments)
    RevenueShareService(partial)._reconcile_refunds(payment)
    assert partial.added == []

    final_refund = _refund(2, 1, now)
    full = MemorySession(earnings, [partial_refund, final_refund], legacy_adjustments)
    RevenueShareService(full)._reconcile_refunds(payment)
    assert full.added == []


def test_legacy_under_reversal_appends_only_an_available_event_key() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    payment = SimpleNamespace(id=UUID(int=10), amount_cents=5)
    earnings = [_earning(1, 1), _earning(2, 3)]
    refund = _refund(1, 2, now)
    legacy_adjustment = SimpleNamespace(
        event_key=f"refund:{refund.id}:{earnings[1].id}",
        refund_id=refund.id,
        parent_entry_id=earnings[1].id,
        amount_cents=-1,
    )
    repaired = MemorySession(earnings, [refund], [legacy_adjustment])

    RevenueShareService(repaired)._reconcile_refunds(payment)

    assert [(row.event_key, row.parent_entry_id, row.amount_cents) for row in repaired.added] == [
        (f"refund:{refund.id}:{earnings[0].id}", earnings[0].id, -1)
    ]
    replay = MemorySession(earnings, [refund], [legacy_adjustment, *repaired.added])
    RevenueShareService(replay)._reconcile_refunds(payment)
    assert replay.added == []


def test_legacy_under_reversal_without_an_event_slot_stays_conservative() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    payment = SimpleNamespace(id=UUID(int=10), amount_cents=6)
    earnings = [_earning(1, 2), _earning(2, 2)]
    refund = _refund(1, 4, now)
    legacy_adjustments = [
        SimpleNamespace(
            event_key=f"refund:{refund.id}:{earning.id}",
            refund_id=refund.id,
            parent_entry_id=earning.id,
            amount_cents=-1,
        )
        for earning in earnings
    ]
    session = MemorySession(earnings, [refund], legacy_adjustments)

    RevenueShareService(session)._reconcile_refunds(payment)

    # The combined target is 3 cents, but both compatible event keys already
    # exist. Keep the append-only 2-cent legacy result instead of rewriting it.
    assert session.added == []


def test_legacy_gap_is_not_shifted_to_another_beneficiary_with_a_free_key() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    payment = SimpleNamespace(id=UUID(int=10), amount_cents=1_000)
    earnings = [
        _earning(1, 100),
        _earning(2, 100),
        _earning(3, 100),
        _earning(4, 100),
        _earning(5, 10),
    ]
    refund = _refund(1, 14, now)
    legacy_adjustments = [
        SimpleNamespace(
            event_key=f"refund:{refund.id}:{earning.id}",
            refund_id=refund.id,
            parent_entry_id=earning.id,
            amount_cents=-1,
        )
        for earning in earnings[:4]
    ]
    session = MemorySession(earnings, [refund], legacy_adjustments)

    RevenueShareService(session)._reconcile_refunds(payment)

    # The 6-cent fair pool assigns the two missing cents to the first two
    # earnings. Their event keys are occupied; earning 5's fair target is zero.
    assert session.added == []
