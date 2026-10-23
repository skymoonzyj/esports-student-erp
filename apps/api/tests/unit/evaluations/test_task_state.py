import pytest

from app.evaluations.task_state import (
    ALLOWED_TRANSITIONS,
    InvalidEvaluationTransition,
    require_transition,
)

ALLOWED = {
    ("uploaded", "identifying"),
    ("identifying", "pending_match"),
    ("identifying", "queued"),
    ("identifying", "manual_input"),
    ("pending_match", "queued"),
    ("pending_match", "manual_input"),
    ("queued", "processing"),
    ("processing", "needs_review"),
    ("processing", "failed"),
    ("failed", "queued"),
    ("failed", "manual_input"),
    ("failed", "archived"),
    ("manual_input", "needs_review"),
    ("needs_review", "published"),
    ("needs_review", "rejected"),
    ("rejected", "correction_pending"),
    ("rejected", "archived"),
    ("correction_pending", "needs_review"),
    ("correction_pending", "queued"),
    ("archived", "correction_pending"),
    ("archived", "queued"),
    ("published", "revision_draft"),
    ("revision_draft", "queued"),
    ("revision_draft", "manual_input"),
}


@pytest.mark.parametrize(("source", "target"), sorted(ALLOWED))
def test_all_documented_task_transitions_are_allowed(
    source: str,
    target: str,
) -> None:
    assert require_transition(source, target) == target


def test_transition_registry_contains_only_documented_edges() -> None:
    assert ALLOWED_TRANSITIONS == frozenset(ALLOWED)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("processing", "published"),
        ("uploaded", "queued"),
        ("published", "manual_input"),
        ("unknown", "queued"),
    ],
)
def test_undocumented_task_transitions_are_rejected(
    source: str,
    target: str,
) -> None:
    with pytest.raises(InvalidEvaluationTransition) as captured:
        require_transition(source, target)

    assert captured.value.source == source
    assert captured.value.target == target
