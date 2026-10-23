import pytest

from app.evaluations.report_state import (
    ALLOWED_REPORT_TRANSITIONS,
    InvalidReportTransition,
    require_report_transition,
)

ALLOWED = {
    ("draft", "pending_review"),
    ("pending_review", "published"),
    ("pending_review", "rejected"),
    ("rejected", "correction_draft"),
    ("correction_draft", "pending_review"),
    ("published", "revision_pending_review"),
    ("revision_pending_review", "published"),
    ("revision_pending_review", "rejected"),
}


def test_report_transition_registry_matches_the_approved_workflow() -> None:
    assert ALLOWED_REPORT_TRANSITIONS == frozenset(ALLOWED)


@pytest.mark.parametrize(("source", "target"), sorted(ALLOWED))
def test_approved_report_transitions_are_allowed(source: str, target: str) -> None:
    assert require_report_transition(source, target) == target


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("draft", "published"),
        ("pending_review", "revision_pending_review"),
        ("rejected", "published"),
        ("published", "draft"),
        ("unknown", "pending_review"),
    ],
)
def test_undocumented_report_transitions_are_rejected(source: str, target: str) -> None:
    with pytest.raises(InvalidReportTransition) as captured:
        require_report_transition(source, target)

    assert captured.value.source == source
    assert captured.value.target == target
