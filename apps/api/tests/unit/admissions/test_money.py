from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.admissions.money import (
    InvalidMoney,
    InvalidRefundAllocation,
    InvalidWithdrawalCalculation,
    RefundablePayment,
    RefundAllocation,
    calculate_withdrawal,
    cohort_participation_dates,
    parse_cny,
    split_refund,
)
from app.core.time import BUSINESS_TIME_ZONE


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.01", 1),
        ("1", 100),
        ("12800.00", 1_280_000),
        ("36800.05", 3_680_005),
    ],
)
def test_parse_cny_uses_integer_cents(raw: str, expected: int) -> None:
    assert parse_cny(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-money",
        "CNY 100.00",
        "NaN",
        "Infinity",
        "0",
        "-0.01",
        "1.001",
    ],
)
def test_parse_cny_rejects_malformed_non_positive_or_sub_cent_values(raw: str) -> None:
    with pytest.raises(InvalidMoney):
        parse_cny(raw)


def test_prorated_refund_uses_remaining_natural_days_and_half_up() -> None:
    preview = calculate_withdrawal(
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 8, 10),
        withdrawal_on=date(2026, 8, 6),
        agreed_tuition_cents=10_001,
        available_paid_cents=20_000,
        policy="prorated_remaining_days",
    )

    assert preview.total_days == 10
    assert preview.remaining_days == 4
    assert preview.agreed_tuition_cents == 10_001
    assert preview.available_paid_cents == 20_000
    assert preview.refund_cents == 4_000


def test_prorated_refund_is_capped_by_available_payment() -> None:
    preview = calculate_withdrawal(
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 8, 10),
        withdrawal_on=date(2026, 8, 2),
        agreed_tuition_cents=10_000,
        available_paid_cents=2_000,
        policy="prorated_remaining_days",
    )

    assert preview.refund_cents == 2_000


def test_prorated_refund_does_not_refund_overpayment_above_theoretical_amount() -> None:
    preview = calculate_withdrawal(
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 8, 10),
        withdrawal_on=date(2026, 8, 5),
        agreed_tuition_cents=10_000,
        available_paid_cents=20_000,
        policy="prorated_remaining_days",
    )

    assert preview.refund_cents == 5_000


def test_full_remaining_paid_refunds_all_available_payment() -> None:
    preview = calculate_withdrawal(
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 8, 10),
        withdrawal_on=date(2026, 8, 9),
        agreed_tuition_cents=10_000,
        available_paid_cents=12_345,
        policy="full_remaining_paid",
    )

    assert preview.remaining_days == 1
    assert preview.refund_cents == 12_345


def test_pre_start_withdrawal_caps_remaining_days_at_full_cohort() -> None:
    preview = calculate_withdrawal(
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 8, 10),
        withdrawal_on=date(2026, 7, 20),
        agreed_tuition_cents=10_000,
        available_paid_cents=10_000,
        policy="prorated_remaining_days",
    )

    assert preview.total_days == 10
    assert preview.remaining_days == 10
    assert preview.refund_cents == 10_000


def test_withdrawal_on_or_after_end_has_no_remaining_days() -> None:
    preview = calculate_withdrawal(
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 8, 10),
        withdrawal_on=date(2026, 8, 10),
        agreed_tuition_cents=10_000,
        available_paid_cents=10_000,
        policy="prorated_remaining_days",
    )

    assert preview.remaining_days == 0
    assert preview.refund_cents == 0


@pytest.mark.parametrize(
    ("starts_on", "ends_on", "agreed_tuition_cents", "available_paid_cents", "policy"),
    [
        (date(2026, 8, 2), date(2026, 8, 1), 10_000, 10_000, "prorated_remaining_days"),
        (date(2026, 8, 1), date(2026, 8, 2), -1, 10_000, "prorated_remaining_days"),
        (date(2026, 8, 1), date(2026, 8, 2), 10_000, -1, "prorated_remaining_days"),
        (date(2026, 8, 1), date(2026, 8, 2), 10_000, 10_000, "unsupported"),
    ],
)
def test_withdrawal_rejects_invalid_inputs(
    starts_on: date,
    ends_on: date,
    agreed_tuition_cents: int,
    available_paid_cents: int,
    policy: str,
) -> None:
    with pytest.raises(InvalidWithdrawalCalculation):
        calculate_withdrawal(
            starts_on=starts_on,
            ends_on=ends_on,
            withdrawal_on=date(2026, 8, 1),
            agreed_tuition_cents=agreed_tuition_cents,
            available_paid_cents=available_paid_cents,
            policy=policy,  # type: ignore[arg-type]
        )


def test_refund_split_uses_most_recent_payment_first() -> None:
    assert split_refund(
        12_000,
        [
            RefundablePayment(UUID(int=1), datetime(2026, 1, 1, tzinfo=UTC), 10_000),
            RefundablePayment(UUID(int=2), datetime(2026, 2, 1, tzinfo=UTC), 5_000),
        ],
    ) == (
        RefundAllocation(UUID(int=2), 5_000),
        RefundAllocation(UUID(int=1), 7_000),
    )


def test_refund_split_uses_id_as_stable_tie_breaker_and_skips_zero_balance() -> None:
    paid_at = datetime(2026, 2, 1, tzinfo=UTC)

    assert split_refund(
        6_000,
        [
            RefundablePayment(UUID(int=1), paid_at, 5_000),
            RefundablePayment(UUID(int=3), paid_at, 0),
            RefundablePayment(UUID(int=2), paid_at, 5_000),
        ],
    ) == (
        RefundAllocation(UUID(int=2), 5_000),
        RefundAllocation(UUID(int=1), 1_000),
    )


@pytest.mark.parametrize(
    ("requested_cents", "payments"),
    [
        (0, []),
        (-1, []),
        (
            10_001,
            [RefundablePayment(UUID(int=1), datetime(2026, 1, 1, tzinfo=UTC), 10_000)],
        ),
        (
            1,
            [RefundablePayment(UUID(int=1), datetime(2026, 1, 1, tzinfo=UTC), -1)],
        ),
        (
            1,
            [
                RefundablePayment(
                    UUID(int=1),
                    datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None),
                    1,
                )
            ],
        ),
    ],
)
def test_refund_split_rejects_invalid_or_unfunded_allocations(
    requested_cents: int,
    payments: list[RefundablePayment],
) -> None:
    with pytest.raises(InvalidRefundAllocation):
        split_refund(requested_cents, payments)


def test_default_midnight_end_maps_to_previous_participation_date() -> None:
    assert cohort_participation_dates(
        datetime(2026, 8, 1, 0, 0, tzinfo=BUSINESS_TIME_ZONE),
        datetime(2026, 8, 8, 0, 0, tzinfo=BUSINESS_TIME_ZONE),
    ) == (date(2026, 8, 1), date(2026, 8, 7))


def test_non_midnight_end_keeps_its_shanghai_natural_date() -> None:
    assert cohort_participation_dates(
        datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
        datetime(2026, 8, 7, 16, 0, 1, tzinfo=UTC),
    ) == (date(2026, 8, 1), date(2026, 8, 8))


def test_cohort_participation_dates_require_aware_ordered_datetimes() -> None:
    with pytest.raises(InvalidWithdrawalCalculation):
        cohort_participation_dates(
            datetime(2026, 8, 1, tzinfo=UTC).replace(tzinfo=None),
            datetime(2026, 8, 8, tzinfo=BUSINESS_TIME_ZONE),
        )
    with pytest.raises(InvalidWithdrawalCalculation):
        cohort_participation_dates(
            datetime(2026, 8, 8, tzinfo=BUSINESS_TIME_ZONE),
            datetime(2026, 8, 1, tzinfo=BUSINESS_TIME_ZONE),
        )
