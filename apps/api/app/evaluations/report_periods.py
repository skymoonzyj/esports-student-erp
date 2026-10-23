from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.core.time import BUSINESS_TIME_ZONE

ReportKind = Literal["daily", "weekly"]


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("report datetime must be timezone-aware")


def _local_date(value: datetime) -> date:
    _require_aware(value)
    return value.astimezone(BUSINESS_TIME_ZONE).date()


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=BUSINESS_TIME_ZONE)


def _day_end(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=BUSINESS_TIME_ZONE)


@dataclass(frozen=True)
class ReportPeriod:
    kind: ReportKind
    sequence: int
    starts_on: date
    ends_on: date

    def __post_init__(self) -> None:
        if self.kind not in {"daily", "weekly"}:
            raise ValueError("unsupported report kind")
        if self.sequence < 1:
            raise ValueError("report sequence must be positive")
        if self.ends_on < self.starts_on:
            raise ValueError("report period end must not precede its start")
        days = (self.ends_on - self.starts_on).days + 1
        if self.kind == "daily" and days != 1:
            raise ValueError("daily report periods must contain one day")
        if self.kind == "weekly" and not 1 <= days <= 7:
            raise ValueError("weekly report periods must contain one to seven days")


def _cohort_dates(*, starts_at: datetime, ends_at: datetime) -> tuple[date, date]:
    _require_aware(starts_at)
    _require_aware(ends_at)
    if ends_at < starts_at:
        raise ValueError("cohort end must follow its start")
    return _local_date(starts_at), _local_date(ends_at)


def daily_periods(
    *,
    starts_at: datetime,
    ends_at: datetime,
    occurred_at: datetime,
) -> tuple[ReportPeriod, ...]:
    starts_on, ends_on = _cohort_dates(starts_at=starts_at, ends_at=ends_at)
    occurred_on = _local_date(occurred_at)
    visible_end = min(ends_on, occurred_on)
    if visible_end < starts_on:
        return ()
    return tuple(
        ReportPeriod(
            kind="daily",
            sequence=offset + 1,
            starts_on=starts_on + timedelta(days=offset),
            ends_on=starts_on + timedelta(days=offset),
        )
        for offset in range((visible_end - starts_on).days + 1)
    )


def weekly_periods(
    *,
    starts_at: datetime,
    ends_at: datetime,
    occurred_at: datetime,
) -> tuple[ReportPeriod, ...]:
    starts_on, ends_on = _cohort_dates(starts_at=starts_at, ends_at=ends_at)
    occurred_on = _local_date(occurred_at)
    if occurred_on < starts_on:
        return ()
    visible_start_limit = min(ends_on, occurred_on)
    periods: list[ReportPeriod] = []
    period_start = starts_on
    sequence = 1
    while period_start <= visible_start_limit:
        period_end = min(period_start + timedelta(days=6), ends_on)
        periods.append(
            ReportPeriod(
                kind="weekly",
                sequence=sequence,
                starts_on=period_start,
                ends_on=period_end,
            )
        )
        sequence += 1
        period_start += timedelta(days=7)
    return tuple(periods)


def submission_opens_at(period: ReportPeriod) -> datetime:
    if period.kind == "daily":
        return _day_start(period.starts_on)
    return _day_start(period.ends_on + timedelta(days=1))


def submission_deadline(
    period: ReportPeriod,
    *,
    cohort_ends_at: datetime,
) -> datetime:
    cohort_ends_on = _local_date(cohort_ends_at)
    if period.ends_on > cohort_ends_on:
        raise ValueError("report period extends beyond cohort end")
    if period.kind == "daily":
        return _day_end(cohort_ends_on + timedelta(days=3))
    if period.ends_on == cohort_ends_on:
        return _day_end(cohort_ends_on + timedelta(days=7))
    return _day_end(min(period.ends_on + timedelta(days=7), cohort_ends_on))


def correction_deadline(rejected_at: datetime) -> datetime:
    rejected_on = _local_date(rejected_at)
    return _day_end(rejected_on + timedelta(days=3))
