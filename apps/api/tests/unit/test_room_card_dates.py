from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "payload",
    [
        {"issued_on": "2026-11-05", "expires_on": None, "expected_version": 0},
        {"issued_on": None, "expires_on": "2026-11-05", "expected_version": 0},
        {"issued_on": "2026-11-06", "expires_on": "2026-11-05", "expected_version": 0},
        {"issued_on": "0000-01-01", "expires_on": "2026-11-05", "expected_version": 0},
        {"issued_on": "2026-11-05", "expires_on": "10000-01-01", "expected_version": 0},
    ],
)
def test_invalid_room_card_dates_are_rejected(payload):
    from app.housing.room_card_schemas import RoomCardSave

    with pytest.raises(ValidationError):
        RoomCardSave.model_validate(payload)


def test_room_card_clear_and_equal_date_and_extreme_years_are_valid():
    from app.housing.room_card_schemas import RoomCardSave

    assert RoomCardSave(issued_on=None, expires_on=None, expected_version=1).expires_on is None
    equal = RoomCardSave(
        issued_on=date(2026, 11, 5), expires_on=date(2026, 11, 5), expected_version=0
    )
    assert equal.expires_on == date(2026, 11, 5)
    extremes = RoomCardSave(issued_on=date.min, expires_on=date.max, expected_version=0)
    assert extremes.issued_on == date(1, 1, 1)


def test_missing_version_is_retained_for_conflict_response():
    from app.housing.room_card_schemas import RoomCardSave

    assert RoomCardSave(issued_on=None, expires_on=None).expected_version is None


def test_reminder_business_day_and_refresh_cross_china_midnight():
    from app.portal.room_card_reminders import reminder_clock

    business_date, cutoff, refresh = reminder_clock(datetime(2026, 11, 5, 15, 59, tzinfo=UTC))
    assert business_date == date(2026, 11, 5)
    assert cutoff == date(2026, 11, 12)
    assert refresh.isoformat() == "2026-11-06T00:00:00+08:00"
    business_date, cutoff, refresh = reminder_clock(datetime(2026, 11, 5, 16, tzinfo=UTC))
    assert business_date == date(2026, 11, 6)
    assert cutoff == date(2026, 11, 13)


def test_reminder_clock_rejects_naive_datetimes():
    from app.portal.room_card_reminders import reminder_clock

    with pytest.raises(ValueError):
        reminder_clock(datetime(2026, 11, 5))  # noqa: DTZ001 - intentionally invalid input
