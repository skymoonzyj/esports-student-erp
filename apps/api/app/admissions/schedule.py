from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.teaching.models import Cohort

EnrollmentScheduleMode = Literal["cohort", "custom"]


class InvalidEnrollmentSchedule(ValueError):
    """Raised when an enrollment's individual participation window is invalid."""


@dataclass(frozen=True)
class EnrollmentSchedule:
    mode: EnrollmentScheduleMode
    starts_at: datetime
    ends_at: datetime


def validate_schedule_values(
    mode: str,
    custom_starts_at: datetime | None,
    custom_ends_at: datetime | None,
) -> EnrollmentScheduleMode:
    if mode not in ("cohort", "custom"):
        raise InvalidEnrollmentSchedule("unsupported enrollment schedule mode")
    if mode == "cohort":
        if custom_starts_at is not None or custom_ends_at is not None:
            raise InvalidEnrollmentSchedule(
                "cohort schedule cannot include custom dates"
            )
        return "cohort"
    if custom_starts_at is None or custom_ends_at is None:
        raise InvalidEnrollmentSchedule(
            "custom schedule requires both start and end times"
        )
    if custom_starts_at.tzinfo is None or custom_starts_at.utcoffset() is None:
        raise InvalidEnrollmentSchedule("custom start time must include an offset")
    if custom_ends_at.tzinfo is None or custom_ends_at.utcoffset() is None:
        raise InvalidEnrollmentSchedule("custom end time must include an offset")
    if custom_ends_at <= custom_starts_at:
        raise InvalidEnrollmentSchedule("custom end time must follow its start time")
    return "custom"


def effective_schedule(enrollment: object, cohort: Cohort) -> EnrollmentSchedule:
    mode = getattr(enrollment, "schedule_mode", None) or "cohort"
    custom_starts_at = getattr(enrollment, "custom_starts_at", None)
    custom_ends_at = getattr(enrollment, "custom_ends_at", None)
    normalized_mode = validate_schedule_values(
        mode,
        custom_starts_at,
        custom_ends_at,
    )
    if normalized_mode == "custom":
        # validate_schedule_values guarantees both values are present.
        assert custom_starts_at is not None
        assert custom_ends_at is not None
        return EnrollmentSchedule(
            mode="custom",
            starts_at=custom_starts_at,
            ends_at=custom_ends_at,
        )
    return EnrollmentSchedule(
        mode="cohort",
        starts_at=cohort.starts_at,
        ends_at=cohort.ends_at,
    )
