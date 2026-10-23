from datetime import datetime
from types import SimpleNamespace

import pytest

from app.admissions.schedule import (
    InvalidEnrollmentSchedule,
    effective_schedule,
    validate_schedule_values,
)

START = datetime.fromisoformat("2026-08-21T09:00:00+08:00")
END = datetime.fromisoformat("2026-08-24T18:00:00+08:00")
COHORT_START = datetime.fromisoformat("2026-08-01T09:00:00+08:00")
COHORT_END = datetime.fromisoformat("2026-08-22T18:00:00+08:00")


def test_cohort_mode_uses_cohort_dates() -> None:
    mode = validate_schedule_values("cohort", None, None)
    schedule = effective_schedule(
        SimpleNamespace(schedule_mode=mode, custom_starts_at=None, custom_ends_at=None),
        SimpleNamespace(starts_at=COHORT_START, ends_at=COHORT_END),  # type: ignore[arg-type]
    )

    assert schedule.mode == "cohort"
    assert schedule.starts_at == COHORT_START
    assert schedule.ends_at == COHORT_END


def test_custom_mode_uses_individual_dates() -> None:
    schedule = effective_schedule(
        SimpleNamespace(
            schedule_mode="custom",
            custom_starts_at=START,
            custom_ends_at=END,
        ),
        SimpleNamespace(starts_at=COHORT_START, ends_at=COHORT_END),  # type: ignore[arg-type]
    )

    assert schedule.mode == "custom"
    assert schedule.starts_at == START
    assert schedule.ends_at == END


@pytest.mark.parametrize(
    ("mode", "starts_at", "ends_at"),
    [
        ("custom", None, END),
        ("custom", START, None),
        ("custom", END, START),
        ("cohort", START, END),
        ("unknown", None, None),
    ],
)
def test_invalid_schedule_values_are_rejected(
    mode: str,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> None:
    with pytest.raises(InvalidEnrollmentSchedule):
        validate_schedule_values(mode, starts_at, ends_at)
