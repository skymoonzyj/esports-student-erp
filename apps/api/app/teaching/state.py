from __future__ import annotations

from datetime import datetime
from typing import Literal

CohortStatus = Literal["draft", "open", "upcoming", "active", "completed", "archived"]
TransitionTrigger = Literal["manual", "scheduler", "request_reconciliation"]
AutomationIssue = Literal[
    "auto_start_transition_blocked",
    "auto_complete_transition_blocked",
]

ALLOWED_TRANSITIONS: frozenset[tuple[CohortStatus, CohortStatus]] = frozenset(
    {
        ("draft", "open"),
        ("open", "upcoming"),
        ("upcoming", "open"),
        ("open", "active"),
        ("upcoming", "active"),
        ("active", "completed"),
        ("completed", "archived"),
    }
)


class InvalidCohortTransition(ValueError):
    def __init__(self, *, current: CohortStatus, target: CohortStatus) -> None:
        super().__init__(f"invalid cohort transition: {current} -> {target}")
        self.current = current
        self.target = target


def _require_aware(*values: datetime) -> None:
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise ValueError("cohort datetimes must include an offset")


def require_transition(current: CohortStatus, target: CohortStatus) -> None:
    if (current, target) not in ALLOWED_TRANSITIONS:
        raise InvalidCohortTransition(current=current, target=target)


def due_transition(
    *,
    status: CohortStatus,
    starts_at: datetime,
    ends_at: datetime,
    auto_start_enabled: bool,
    auto_complete_enabled: bool,
    now: datetime,
) -> CohortStatus | None:
    _require_aware(starts_at, ends_at, now)
    if auto_start_enabled and status in {"open", "upcoming"} and now >= starts_at:
        return "active"
    if auto_complete_enabled and status == "active" and now >= ends_at:
        return "completed"
    return None


def automation_issues(
    *,
    status: CohortStatus,
    starts_at: datetime,
    ends_at: datetime,
    auto_start_enabled: bool,
    auto_complete_enabled: bool,
    now: datetime,
) -> list[AutomationIssue]:
    _require_aware(starts_at, ends_at, now)
    issues: list[AutomationIssue] = []
    if auto_start_enabled and status == "draft" and now >= starts_at:
        issues.append("auto_start_transition_blocked")
    if (
        auto_complete_enabled
        and status in {"draft", "open", "upcoming"}
        and now >= ends_at
    ):
        issues.append("auto_complete_transition_blocked")
    return issues
