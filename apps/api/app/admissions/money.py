from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from app.core.time import BUSINESS_TIME_ZONE

RefundPolicy = Literal["prorated_remaining_days", "full_remaining_paid"]


class InvalidMoney(ValueError):
    pass


class InvalidWithdrawalCalculation(ValueError):
    pass


class InvalidRefundAllocation(ValueError):
    pass


@dataclass(frozen=True)
class RefundablePayment:
    payment_id: UUID
    paid_at: datetime
    refundable_cents: int


@dataclass(frozen=True)
class RefundAllocation:
    payment_id: UUID
    amount_cents: int


@dataclass(frozen=True)
class WithdrawalCalculation:
    total_days: int
    remaining_days: int
    agreed_tuition_cents: int
    available_paid_cents: int
    refund_cents: int


def parse_cny(value: str) -> int:
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError):
        raise InvalidMoney(
            "CNY amount must be positive with at most two decimals"
        ) from None

    exponent = decimal.as_tuple().exponent
    if (
        not decimal.is_finite()
        or decimal <= 0
        or not isinstance(exponent, int)
        or exponent < -2
    ):
        raise InvalidMoney("CNY amount must be positive with at most two decimals")
    return int((decimal * 100).to_integral_exact())


def calculate_withdrawal(
    *,
    starts_on: date,
    ends_on: date,
    withdrawal_on: date,
    agreed_tuition_cents: int,
    available_paid_cents: int,
    policy: RefundPolicy,
) -> WithdrawalCalculation:
    if ends_on < starts_on:
        raise InvalidWithdrawalCalculation("cohort end date cannot precede its start date")
    if agreed_tuition_cents < 0 or available_paid_cents < 0:
        raise InvalidWithdrawalCalculation("withdrawal amounts cannot be negative")
    if policy not in ("prorated_remaining_days", "full_remaining_paid"):
        raise InvalidWithdrawalCalculation("unsupported refund policy")

    total_days = (ends_on - starts_on).days + 1
    remaining_days = min(max((ends_on - withdrawal_on).days, 0), total_days)
    theoretical_refund = (
        Decimal(agreed_tuition_cents) * remaining_days / total_days
    ).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    refund_cents = (
        available_paid_cents
        if policy == "full_remaining_paid"
        else min(int(theoretical_refund), available_paid_cents)
    )
    return WithdrawalCalculation(
        total_days=total_days,
        remaining_days=remaining_days,
        agreed_tuition_cents=agreed_tuition_cents,
        available_paid_cents=available_paid_cents,
        refund_cents=refund_cents,
    )


def split_refund(
    requested_cents: int,
    payments: list[RefundablePayment],
) -> tuple[RefundAllocation, ...]:
    if requested_cents <= 0:
        raise InvalidRefundAllocation("refund amount must be positive")
    if any(payment.refundable_cents < 0 for payment in payments):
        raise InvalidRefundAllocation("refundable balances cannot be negative")
    if any(payment.paid_at.utcoffset() is None for payment in payments):
        raise InvalidRefundAllocation("payment timestamps must be timezone-aware")

    remaining_cents = requested_cents
    allocations: list[RefundAllocation] = []
    for payment in sorted(
        payments,
        key=lambda candidate: (candidate.paid_at, candidate.payment_id.int),
        reverse=True,
    ):
        allocation_cents = min(remaining_cents, payment.refundable_cents)
        if allocation_cents > 0:
            allocations.append(RefundAllocation(payment.payment_id, allocation_cents))
            remaining_cents -= allocation_cents
        if remaining_cents == 0:
            return tuple(allocations)

    raise InvalidRefundAllocation("refund amount exceeds refundable payment balances")


def cohort_participation_dates(
    starts_at: datetime,
    ends_at: datetime,
) -> tuple[date, date]:
    if starts_at.utcoffset() is None or ends_at.utcoffset() is None:
        raise InvalidWithdrawalCalculation("cohort datetimes must be timezone-aware")
    if ends_at <= starts_at:
        raise InvalidWithdrawalCalculation("cohort end time must follow its start time")

    local_start = starts_at.astimezone(BUSINESS_TIME_ZONE)
    local_end = ends_at.astimezone(BUSINESS_TIME_ZONE)
    if (
        local_end.hour == 0
        and local_end.minute == 0
        and local_end.second == 0
        and local_end.microsecond == 0
    ):
        local_end -= timedelta(microseconds=1)
    return local_start.date(), local_end.date()
