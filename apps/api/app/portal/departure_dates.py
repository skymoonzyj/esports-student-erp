"""Calendar-date semantics shared by departure reminder queries and responses."""

from datetime import date, datetime, time, timedelta
from typing import Literal

from app.core.time import BUSINESS_TIME_ZONE, business_date_at

DepartureWindow = Literal["today", "three_days"]


def _business_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("departure datetime must be timezone-aware")
    return value.astimezone(BUSINESS_TIME_ZONE)


def housing_departure_date(value: datetime) -> date:
    """Housing planned checkout timestamps keep their displayed Shanghai date."""
    return business_date_at(_business_datetime(value))


def enrollment_departure_date(value: datetime) -> date:
    """Match exclusiveEndToDateInput, including its second-level midnight check."""
    local = _business_datetime(value)
    displayed_date = local.date()
    # The existing web helper checks hours/minutes/seconds, not milliseconds.
    if local.hour == 0 and local.minute == 0 and local.second == 0:
        return displayed_date - timedelta(days=1)
    return displayed_date


def departure_window_dates(now: datetime, window: DepartureWindow) -> tuple[date, date]:
    """Return inclusive bounds: today, or today through the day after tomorrow."""
    today = housing_departure_date(now)
    if window == "today":
        return today, today
    if window == "three_days":
        return today, today + timedelta(days=2)
    raise ValueError("unsupported departure window")


def next_business_midnight(now: datetime) -> datetime:
    tomorrow = housing_departure_date(now) + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=BUSINESS_TIME_ZONE)
