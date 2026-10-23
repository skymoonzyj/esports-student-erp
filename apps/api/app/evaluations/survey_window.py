"""Calendar-month availability windows for camp evaluation surveys."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime

from app.core.time import BUSINESS_TIME_ZONE


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("survey datetime must be timezone-aware")


def _add_calendar_month(value: datetime) -> datetime:
    """Add one calendar month, clamping an invalid day to the target month end."""

    local = value.astimezone(BUSINESS_TIME_ZONE)
    year = local.year + (local.month // 12)
    month = local.month % 12 + 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class SurveyWindow:
    opens_at: datetime
    closes_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.opens_at)
        _require_aware(self.closes_at)
        if self.closes_at <= self.opens_at:
            raise ValueError("survey window must close after it opens")

    def is_open(self, occurred_at: datetime) -> bool:
        _require_aware(occurred_at)
        return self.opens_at <= occurred_at < self.closes_at


def survey_window_for_end(ended_at: datetime) -> SurveyWindow:
    """Return the inclusive-open/exclusive-close window for an actual camp end."""

    _require_aware(ended_at)
    opened = ended_at.astimezone(BUSINESS_TIME_ZONE)
    return SurveyWindow(opens_at=opened, closes_at=_add_calendar_month(opened))
