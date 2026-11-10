from datetime import date, datetime

import pytest

from app.portal.departure_dates import (
    departure_window_dates,
    enrollment_departure_date,
    housing_departure_date,
    next_business_midnight,
)


def test_midnight_end_is_previous_day_but_housing_is_not() -> None:
    value = datetime.fromisoformat("2026-10-24T00:00:00+08:00")
    assert enrollment_departure_date(value) == date(2026, 10, 23)
    assert housing_departure_date(value) == date(2026, 10, 24)


def test_legacy_non_midnight_end_keeps_display_date() -> None:
    value = datetime.fromisoformat("2026-10-24T18:00:00+08:00")
    assert enrollment_departure_date(value) == date(2026, 10, 24)


@pytest.mark.parametrize(
    ("timestamp", "housing_date", "teaching_date"),
    [
        ("2026-10-23T16:00:00Z", "2026-10-24", "2026-10-23"),
        ("2026-11-01T00:00:00+08:00", "2026-11-01", "2026-10-31"),
        ("2027-01-01T00:00:00+08:00", "2027-01-01", "2026-12-31"),
        ("2028-03-01T00:00:00+08:00", "2028-03-01", "2028-02-29"),
        ("2028-02-29T00:00:00+08:00", "2028-02-29", "2028-02-28"),
        ("2026-10-24T00:00:01+08:00", "2026-10-24", "2026-10-24"),
        ("2026-10-24T00:01:00+08:00", "2026-10-24", "2026-10-24"),
        ("2026-10-24T01:00:00+08:00", "2026-10-24", "2026-10-24"),
        ("2026-10-24T00:00:00.001+08:00", "2026-10-24", "2026-10-23"),
        ("2026-10-23T16:00:00.999Z", "2026-10-24", "2026-10-23"),
        ("2026-10-24T00:00:00.999999+08:00", "2026-10-24", "2026-10-23"),
        ("2026-10-23T15:59:59.999Z", "2026-10-23", "2026-10-23"),
    ],
)
def test_dates_match_frontend_display_boundaries(
    timestamp: str, housing_date: str, teaching_date: str
) -> None:
    value = datetime.fromisoformat(timestamp)
    assert housing_departure_date(value).isoformat() == housing_date
    assert enrollment_departure_date(value).isoformat() == teaching_date


@pytest.mark.parametrize(
    ("timestamp", "expected_start", "expected_end"),
    [
        ("2026-10-23T16:00:00Z", "2026-10-24", "2026-10-26"),
        ("2026-10-31T23:59:59+08:00", "2026-10-31", "2026-11-02"),
        ("2026-12-31T18:00:00+08:00", "2026-12-31", "2027-01-02"),
        ("2028-02-28T18:00:00+08:00", "2028-02-28", "2028-03-01"),
    ],
)
def test_windows_include_today_through_two_days_later(
    timestamp: str, expected_start: str, expected_end: str
) -> None:
    now = datetime.fromisoformat(timestamp)
    start, end = departure_window_dates(now, "three_days")
    assert start.isoformat() == expected_start
    assert end.isoformat() == expected_end
    assert departure_window_dates(now, "today") == (start, start)


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-10-23T16:00:00Z", "2026-10-25T00:00:00+08:00"),
        ("2026-10-31T23:59:59.999999+08:00", "2026-11-01T00:00:00+08:00"),
        ("2026-12-31T18:00:00+08:00", "2027-01-01T00:00:00+08:00"),
        ("2028-02-28T18:00:00+08:00", "2028-02-29T00:00:00+08:00"),
    ],
)
def test_next_midnight_is_a_future_shanghai_midnight(timestamp: str, expected: str) -> None:
    now = datetime.fromisoformat(timestamp)
    result = next_business_midnight(now)
    assert result.isoformat() == expected
    assert result > now


def test_all_date_helpers_reject_naive_timestamps() -> None:
    naive = datetime(2026, 10, 24)  # noqa: DTZ001 - deliberately invalid input
    with pytest.raises(ValueError, match="timezone-aware"):
        housing_departure_date(naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        enrollment_departure_date(naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        departure_window_dates(naive, "today")
    with pytest.raises(ValueError, match="timezone-aware"):
        next_business_midnight(naive)


def test_unknown_date_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="window"):
        departure_window_dates(datetime.fromisoformat("2026-10-24T00:00:00+08:00"), "week")  # type: ignore[arg-type]
