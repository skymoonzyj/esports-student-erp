from datetime import datetime

import pytest

from app.evaluations.survey_window import (
    SurveyWindow,
    survey_window_for_end,
)


def aware(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.mark.parametrize(
    ("ended_at", "deadline"),
    [
        ("2026-01-31T18:30:00+08:00", "2026-02-28T18:30:00+08:00"),
        ("2028-01-31T18:30:00+08:00", "2028-02-29T18:30:00+08:00"),
        ("2026-04-30T10:00:00+08:00", "2026-05-30T10:00:00+08:00"),
    ],
)
def test_calendar_month_deadline_clamps_to_last_day(ended_at: str, deadline: str) -> None:
    result = survey_window_for_end(aware(ended_at))
    assert result == SurveyWindow(
        opens_at=aware(ended_at),
        closes_at=aware(deadline),
    )


def test_window_preserves_local_time_and_offset_for_shanghai_end() -> None:
    result = survey_window_for_end(aware("2026-08-12T23:45:10+08:00"))
    assert result.opens_at.isoformat() == "2026-08-12T23:45:10+08:00"
    assert result.closes_at.isoformat() == "2026-09-12T23:45:10+08:00"


def test_window_membership_is_open_inclusive_and_close_exclusive() -> None:
    result = survey_window_for_end(aware("2026-08-12T10:00:00+08:00"))
    assert result.is_open(aware("2026-08-12T10:00:00+08:00"))
    assert result.is_open(aware("2026-09-12T09:59:59+08:00"))
    assert not result.is_open(aware("2026-09-12T10:00:00+08:00"))
    assert not result.is_open(aware("2026-08-12T09:59:59+08:00"))


def test_window_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        survey_window_for_end(datetime.fromisoformat("2026-08-12T10:00:00"))

    result = survey_window_for_end(aware("2026-08-12T10:00:00+08:00"))
    with pytest.raises(ValueError, match="timezone-aware"):
        result.is_open(datetime.fromisoformat("2026-08-12T10:00:00"))
