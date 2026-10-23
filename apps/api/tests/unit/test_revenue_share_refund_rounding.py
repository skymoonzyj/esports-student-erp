from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.revenue_share.service import RevenueShareService


@pytest.mark.parametrize("same_date", [False, True])
def test_new_refund_uses_persisted_reversal_even_when_it_sorts_before_previous(same_date):
    payment = SimpleNamespace(id=uuid4(), amount_cents=10_000)
    earning = SimpleNamespace(
        id=uuid4(), amount_cents=3333, rule_id=uuid4(), role_kind="platform_developer",
        beneficiary_kind="person", beneficiary_person_id=uuid4(), beneficiary_organization_id=None,
        calculation_type="percentage", percentage_basis_points_snapshot=3333, fixed_amount_cents_snapshot=None,
    )
    now = datetime(2026, 9, 11, tzinfo=UTC)
    first_refund = SimpleNamespace(
        id=UUID(int=2), amount_cents=5000, refunded_at=now, created_by_user_id=uuid4(),
    )
    new_refund = SimpleNamespace(
        id=UUID(int=1), amount_cents=5000,
        refunded_at=now if same_date else now - timedelta(days=1),
        created_by_user_id=first_refund.created_by_user_id,
    )

    class MemorySession:
        def __init__(self, refunds, adjustments):
            self.results = iter(([earning], refunds, adjustments))
            self.added = []

        def scalars(self, _statement):
            return next(self.results)

        def add(self, item):
            self.added.append(item)

    initial = MemorySession([first_refund], [])
    RevenueShareService(initial)._reconcile_refunds(payment)
    assert [entry.amount_cents for entry in initial.added] == [-1667]
    changed_order = MemorySession([new_refund, first_refund], initial.added)
    RevenueShareService(changed_order)._reconcile_refunds(payment)
    assert [entry.amount_cents for entry in changed_order.added] == [-1666]
    adjustments = initial.added + changed_order.added
    assert sum(entry.amount_cents for entry in adjustments) == -earning.amount_cents
    replay = MemorySession([first_refund, new_refund], adjustments)
    RevenueShareService(replay)._reconcile_refunds(payment)
    assert replay.added == []
