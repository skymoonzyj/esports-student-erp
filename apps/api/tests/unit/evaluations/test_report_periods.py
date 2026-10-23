from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest

from app.core.time import BUSINESS_TIME_ZONE
from app.evaluations.report_periods import (
    ReportPeriod,
    correction_deadline,
    daily_periods,
    submission_deadline,
    submission_opens_at,
    weekly_periods,
)


def shanghai(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=BUSINESS_TIME_ZONE)


COHORT_START = shanghai("2026-08-01T10:00:00")
COHORT_END = shanghai("2026-08-21T18:00:00")


def test_daily_periods_use_shanghai_dates_and_never_include_a_future_day() -> None:
    periods = daily_periods(
        starts_at=COHORT_START,
        ends_at=COHORT_END,
        occurred_at=datetime(2026, 8, 2, 16, tzinfo=UTC),
    )

    assert periods == (
        ReportPeriod("daily", 1, date(2026, 8, 1), date(2026, 8, 1)),
        ReportPeriod("daily", 2, date(2026, 8, 2), date(2026, 8, 2)),
        ReportPeriod("daily", 3, date(2026, 8, 3), date(2026, 8, 3)),
    )


def test_daily_periods_include_leap_day_and_stop_at_cohort_end() -> None:
    periods = daily_periods(
        starts_at=shanghai("2028-02-28T09:00:00"),
        ends_at=shanghai("2028-03-01T17:00:00"),
        occurred_at=shanghai("2028-03-05T12:00:00"),
    )

    assert [period.starts_on for period in periods] == [
        date(2028, 2, 28),
        date(2028, 2, 29),
        date(2028, 3, 1),
    ]


def test_no_period_is_available_before_its_local_start_date() -> None:
    assert daily_periods(
        starts_at=COHORT_START,
        ends_at=COHORT_END,
        occurred_at=shanghai("2026-07-31T23:59:59"),
    ) == ()
    assert weekly_periods(
        starts_at=COHORT_START,
        ends_at=COHORT_END,
        occurred_at=shanghai("2026-07-31T23:59:59"),
    ) == ()


@pytest.mark.parametrize(
    ("ends_at", "expected"),
    [
        (
            shanghai("2026-08-21T18:00:00"),
            [
                (date(2026, 8, 1), date(2026, 8, 7)),
                (date(2026, 8, 8), date(2026, 8, 14)),
                (date(2026, 8, 15), date(2026, 8, 21)),
            ],
        ),
        (
            shanghai("2026-08-08T18:00:00"),
            [
                (date(2026, 8, 1), date(2026, 8, 7)),
                (date(2026, 8, 8), date(2026, 8, 8)),
            ],
        ),
        (
            shanghai("2026-08-20T18:00:00"),
            [
                (date(2026, 8, 1), date(2026, 8, 7)),
                (date(2026, 8, 8), date(2026, 8, 14)),
                (date(2026, 8, 15), date(2026, 8, 20)),
            ],
        ),
    ],
)
def test_weekly_periods_anchor_to_the_cohort_date_and_keep_a_short_final_week(
    ends_at: datetime,
    expected: list[tuple[date, date]],
) -> None:
    periods = weekly_periods(
        starts_at=COHORT_START,
        ends_at=ends_at,
        occurred_at=shanghai("2026-09-01T12:00:00"),
    )

    assert [(period.starts_on, period.ends_on) for period in periods] == expected
    assert [period.sequence for period in periods] == list(range(1, len(expected) + 1))


def test_weekly_periods_expose_only_started_periods_for_draft_work() -> None:
    periods = weekly_periods(
        starts_at=COHORT_START,
        ends_at=COHORT_END,
        occurred_at=shanghai("2026-08-09T12:00:00"),
    )

    assert [period.sequence for period in periods] == [1, 2]


def test_daily_submission_opens_at_local_day_start() -> None:
    period = ReportPeriod("daily", 5, date(2026, 8, 5), date(2026, 8, 5))

    assert submission_opens_at(period) == shanghai("2026-08-05T00:00:00")


def test_weekly_submission_opens_only_after_the_period_has_ended() -> None:
    period = ReportPeriod("weekly", 1, date(2026, 8, 1), date(2026, 8, 7))

    assert submission_opens_at(period) == shanghai("2026-08-08T00:00:00")


def test_daily_first_submission_deadline_is_three_local_days_after_camp_end() -> None:
    period = ReportPeriod("daily", 1, date(2026, 8, 1), date(2026, 8, 1))

    assert submission_deadline(period, cohort_ends_at=COHORT_END) == shanghai(
        "2026-08-24T23:59:59.999999"
    )


def test_weekly_deadlines_use_the_next_period_end_and_seven_days_for_the_last() -> None:
    periods = weekly_periods(
        starts_at=COHORT_START,
        ends_at=COHORT_END,
        occurred_at=shanghai("2026-09-01T12:00:00"),
    )

    assert [submission_deadline(period, cohort_ends_at=COHORT_END) for period in periods] == [
        shanghai("2026-08-14T23:59:59.999999"),
        shanghai("2026-08-21T23:59:59.999999"),
        shanghai("2026-08-28T23:59:59.999999"),
    ]


def test_weekly_deadline_uses_a_short_next_period_end() -> None:
    cohort_end = shanghai("2026-08-08T18:00:00")
    first, last = weekly_periods(
        starts_at=COHORT_START,
        ends_at=cohort_end,
        occurred_at=shanghai("2026-09-01T12:00:00"),
    )

    assert submission_deadline(first, cohort_ends_at=cohort_end) == shanghai(
        "2026-08-08T23:59:59.999999"
    )
    assert submission_deadline(last, cohort_ends_at=cohort_end) == shanghai(
        "2026-08-15T23:59:59.999999"
    )


def test_rejection_starts_a_three_local_natural_day_correction_window() -> None:
    rejected_at = datetime(2026, 8, 21, 23, 30, tzinfo=UTC)

    assert correction_deadline(rejected_at) == shanghai("2026-08-25T23:59:59.999999")


@pytest.mark.parametrize(
    "call",
    [
        lambda: daily_periods(
            starts_at=COHORT_START.replace(tzinfo=None),
            ends_at=COHORT_END,
            occurred_at=COHORT_START,
        ),
        lambda: weekly_periods(
            starts_at=COHORT_START,
            ends_at=COHORT_END.replace(tzinfo=None),
            occurred_at=COHORT_START,
        ),
        lambda: daily_periods(
            starts_at=COHORT_START,
            ends_at=COHORT_END,
            occurred_at=COHORT_START.replace(tzinfo=None),
        ),
        lambda: submission_deadline(
            ReportPeriod("daily", 1, date(2026, 8, 1), date(2026, 8, 1)),
            cohort_ends_at=COHORT_END.replace(tzinfo=None),
        ),
        lambda: correction_deadline(COHORT_END.replace(tzinfo=None)),
    ],
)
def test_report_period_functions_reject_naive_datetimes(
    call: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        call()


def test_report_periods_reject_an_inverted_cohort() -> None:
    with pytest.raises(ValueError, match="cohort end"):
        daily_periods(
            starts_at=COHORT_END,
            ends_at=COHORT_START,
            occurred_at=COHORT_END,
        )


@pytest.mark.parametrize(
    ("kind", "sequence", "starts_on", "ends_on"),
    [
        ("daily", 0, date(2026, 8, 1), date(2026, 8, 1)),
        ("daily", 1, date(2026, 8, 2), date(2026, 8, 1)),
        ("daily", 1, date(2026, 8, 1), date(2026, 8, 2)),
        ("weekly", 1, date(2026, 8, 1), date(2026, 8, 8)),
    ],
)
def test_report_period_rejects_invalid_shape(
    kind: str,
    sequence: int,
    starts_on: date,
    ends_on: date,
) -> None:
    with pytest.raises(ValueError):
        ReportPeriod(kind, sequence, starts_on, ends_on)  # type: ignore[arg-type]
