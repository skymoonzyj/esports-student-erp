from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.teaching.state import (
    ALLOWED_TRANSITIONS,
    InvalidCohortTransition,
    automation_issues,
    due_transition,
    require_transition,
)

STATUSES = ("draft", "open", "upcoming", "active", "completed", "archived")
ALLOWED = {
    ("draft", "open"),
    ("open", "upcoming"),
    ("upcoming", "open"),
    ("open", "active"),
    ("upcoming", "active"),
    ("active", "completed"),
    ("completed", "archived"),
}
NOW = datetime(2026, 8, 10, 8, tzinfo=UTC)
STARTS_AT = NOW - timedelta(hours=1)
ENDS_AT = NOW + timedelta(hours=1)


def test_allowed_transition_constant_matches_the_approved_matrix() -> None:
    assert ALLOWED_TRANSITIONS == frozenset(ALLOWED)


@pytest.mark.parametrize(
    ("current", "target"),
    [(current, target) for current in STATUSES for target in STATUSES],
)
def test_transition_matrix_allows_only_approved_adjacent_pairs(
    current,
    target,
) -> None:
    if (current, target) in ALLOWED:
        require_transition(current, target)
        return

    with pytest.raises(InvalidCohortTransition) as error:
        require_transition(current, target)

    assert error.value.current == current
    assert error.value.target == target


@pytest.mark.parametrize("status", ["open", "upcoming"])
def test_due_auto_start_advances_open_or_upcoming_to_active(status) -> None:
    assert due_transition(
        status=status,
        starts_at=STARTS_AT,
        ends_at=ENDS_AT,
        auto_start_enabled=True,
        auto_complete_enabled=True,
        now=NOW,
    ) == "active"


def test_due_auto_complete_advances_active_to_completed() -> None:
    assert due_transition(
        status="active",
        starts_at=STARTS_AT,
        ends_at=NOW - timedelta(minutes=1),
        auto_start_enabled=True,
        auto_complete_enabled=True,
        now=NOW,
    ) == "completed"


@pytest.mark.parametrize(
    ("status", "auto_start_enabled", "auto_complete_enabled"),
    [
        ("open", False, True),
        ("upcoming", False, True),
        ("active", True, False),
        ("draft", True, True),
        ("completed", True, True),
        ("archived", True, True),
    ],
)
def test_due_transition_does_not_jump_or_ignore_disabled_flags(
    status,
    auto_start_enabled,
    auto_complete_enabled,
) -> None:
    assert due_transition(
        status=status,
        starts_at=STARTS_AT,
        ends_at=NOW - timedelta(minutes=1),
        auto_start_enabled=auto_start_enabled,
        auto_complete_enabled=auto_complete_enabled,
        now=NOW,
    ) is None


@pytest.mark.parametrize("naive_field", ["starts_at", "ends_at", "now"])
def test_state_functions_reject_naive_datetimes(naive_field: str) -> None:
    values = {
        "starts_at": STARTS_AT,
        "ends_at": ENDS_AT,
        "now": NOW,
    }
    values[naive_field] = values[naive_field].replace(tzinfo=None)

    with pytest.raises(ValueError, match="include an offset"):
        due_transition(
            status="open",
            auto_start_enabled=True,
            auto_complete_enabled=True,
            **values,
        )
    with pytest.raises(ValueError, match="include an offset"):
        automation_issues(
            status="open",
            auto_start_enabled=True,
            auto_complete_enabled=True,
            **values,
        )


def test_draft_due_for_both_automations_reports_both_blockers() -> None:
    assert automation_issues(
        status="draft",
        starts_at=STARTS_AT,
        ends_at=NOW - timedelta(minutes=1),
        auto_start_enabled=True,
        auto_complete_enabled=True,
        now=NOW,
    ) == [
        "auto_start_transition_blocked",
        "auto_complete_transition_blocked",
    ]


@pytest.mark.parametrize("status", ["open", "upcoming"])
def test_non_active_cohort_past_end_reports_completion_blocker(status) -> None:
    assert automation_issues(
        status=status,
        starts_at=STARTS_AT,
        ends_at=NOW - timedelta(minutes=1),
        auto_start_enabled=False,
        auto_complete_enabled=True,
        now=NOW,
    ) == ["auto_complete_transition_blocked"]


@pytest.mark.parametrize(
    ("status", "starts_at", "ends_at", "auto_start", "auto_complete"),
    [
        ("draft", NOW + timedelta(hours=1), ENDS_AT, True, True),
        ("draft", STARTS_AT, NOW - timedelta(minutes=1), False, False),
        ("active", STARTS_AT, ENDS_AT, True, True),
        ("completed", STARTS_AT, NOW - timedelta(minutes=1), True, True),
        ("archived", STARTS_AT, NOW - timedelta(minutes=1), True, True),
    ],
)
def test_resolved_disabled_or_not_due_cohorts_have_no_automation_warning(
    status,
    starts_at,
    ends_at,
    auto_start,
    auto_complete,
) -> None:
    assert automation_issues(
        status=status,
        starts_at=starts_at,
        ends_at=ends_at,
        auto_start_enabled=auto_start,
        auto_complete_enabled=auto_complete,
        now=NOW,
    ) == []
