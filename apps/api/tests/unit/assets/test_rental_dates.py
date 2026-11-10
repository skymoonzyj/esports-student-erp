from datetime import date

import pytest
from pydantic import ValidationError

from app.asset_management.schemas import AssetCreate


@pytest.mark.parametrize(
    ("original", "period", "expected"),
    [
        (date(2027, 1, 31), "month", date(2027, 2, 28)),
        (date(2028, 1, 31), "month", date(2028, 2, 29)),
        (date(2026, 11, 30), "quarter", date(2027, 2, 28)),
        (date(2026, 1, 1), "week", date(2026, 1, 8)),
    ],
)
def test_calendar_renewal_clamps_month_end(original, period, expected):
    from app.asset_management.rental_dates import renew_expiry

    assert renew_expiry(original, period) == expected


@pytest.mark.parametrize("period", ["week", "month", "quarter"])
def test_calendar_overflow_is_rejected(period):
    from app.asset_management.rental_dates import renew_expiry

    with pytest.raises(ValueError):
        renew_expiry(date(9999, 12, 31), period)


def test_legacy_creation_remains_unconfirmed():
    assert AssetCreate(name="legacy").model_dump().get("ownership_type", "MISSING") is None


@pytest.mark.parametrize(
    "fields",
    [
        {"ownership_type": "rented"},
        {"ownership_type": "owned", "acquisition_method": "purchase"},
        {
            "ownership_type": "owned",
            "acquisition_method": "purchase",
            "rental_started_on": "2026-01-01",
        },
        {"ownership_type": "owned", "acquisition_method": "other"},
        {
            "ownership_type": "owned",
            "acquisition_method": "other",
            "acquisition_note": "gift",
            "acquisition_channel": "online",
        },
        {"rental_started_on": "2026-01-01"},
        {
            "ownership_type": "rented",
            "rental_started_on": "2026-02-01",
            "rental_expires_on": "2026-01-01",
        },
    ],
)
def test_inconsistent_ownership_is_rejected(fields):
    with pytest.raises(ValidationError):
        AssetCreate(name="invalid", **fields)


def test_rented_schema_accepts_date_only_lease():
    value = AssetCreate(
        name="rental",
        ownership_type="rented",
        rental_started_on="2026-01-01",
        rental_expires_on="2026-01-31",
    )
    assert value.rental_expires_on == date(2026, 1, 31)
