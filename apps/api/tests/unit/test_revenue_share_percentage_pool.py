from datetime import UTC, datetime, timedelta
from itertools import permutations
from uuid import UUID

import pytest

from app.revenue_share.models import RevenueShareRule
from app.revenue_share.service import RevenueShareService


@pytest.mark.parametrize(
    "payment,basis_points,expected",
    [
        (101, (5000, 5000), (51, 50)),
        (101, (2500, 2500), (26, 25)),
        (101, (3333, 3333, 3334), (34, 33, 34)),
        (1, (2500, 2500), (1, 0)),
        (1, (100, 100), (0, 0)),
        (999, (100, 100), (10, 10)),
        (101, (), ()),
    ],
)
def test_percentage_pool_has_exact_hand_calculated_totals_in_any_rule_order(
    payment,
    basis_points,
    expected,
):
    rules = [
        RevenueShareRule(
            id=UUID(int=index + 1),
            percentage_basis_points=points,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for index, points in enumerate(basis_points)
    ]
    for ordered in permutations(rules):
        amounts = RevenueShareService._percentage_amounts(payment, list(ordered))
        assert tuple(amounts[rule.id] for rule in rules) == expected


def test_percentage_pool_remainder_tie_prefers_older_rule_before_uuid():
    first = RevenueShareRule(
        id=UUID(int=2), percentage_basis_points=5000, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    second = RevenueShareRule(
        id=UUID(int=1),
        percentage_basis_points=5000,
        created_at=first.created_at + timedelta(seconds=1),
    )
    assert RevenueShareService._percentage_amounts(101, [second, first]) == {
        first.id: 51,
        second.id: 50,
    }
