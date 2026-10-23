from __future__ import annotations

ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
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
)


class InvalidEvaluationTransition(ValueError):
    def __init__(self, *, source: str, target: str) -> None:
        super().__init__(f"invalid evaluation transition: {source} -> {target}")
        self.source = source
        self.target = target


def require_transition(source: str, target: str) -> str:
    if (source, target) not in ALLOWED_TRANSITIONS:
        raise InvalidEvaluationTransition(source=source, target=target)
    return target
