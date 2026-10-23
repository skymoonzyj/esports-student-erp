from datetime import date

from app.identity.readiness import is_minor


def test_minor_is_computed_at_reference_date() -> None:
    assert is_minor(date(2010, 7, 27), date(2026, 7, 26)) is True
    assert is_minor(date(2008, 7, 26), date(2026, 7, 26)) is False


def test_eighteenth_birthday_is_not_minor() -> None:
    assert is_minor(date(2008, 7, 26), date(2026, 7, 25)) is True
    assert is_minor(date(2008, 7, 26), date(2026, 7, 26)) is False


def test_leap_day_uses_february_28_for_eighteenth_anniversary() -> None:
    assert is_minor(date(2008, 2, 29), date(2026, 2, 27)) is True
    assert is_minor(date(2008, 2, 29), date(2026, 2, 28)) is False
