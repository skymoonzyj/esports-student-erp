"""Date-only supplier lease arithmetic; independent of custody and warranty."""

from calendar import monthrange
from datetime import date, timedelta
from typing import Literal

RentalPeriod = Literal["week", "month", "quarter"]


def renew_expiry(expires_on: date, period: RentalPeriod) -> date:
    try:
        if period == "week":
            return expires_on + timedelta(days=7)
        if period not in {"month", "quarter"}:
            raise ValueError("invalid rental period")
        month_index = expires_on.year * 12 + expires_on.month - 1
        year, month = divmod(month_index + (1 if period == "month" else 3), 12)
        month += 1
        return date(year, month, min(expires_on.day, monthrange(year, month)[1]))
    except (OverflowError, ValueError) as exc:
        raise ValueError("rental expiry exceeds supported calendar range") from exc
