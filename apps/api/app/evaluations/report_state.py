from __future__ import annotations


class InvalidReportTransition(ValueError):
    def __init__(self, *, source: str, target: str) -> None:
        super().__init__(f"invalid report transition: {source} -> {target}")
        self.source = source
        self.target = target


ALLOWED_REPORT_TRANSITIONS = frozenset(
    {
        ("draft", "pending_review"),
        ("pending_review", "published"),
        ("pending_review", "rejected"),
        ("rejected", "correction_draft"),
        ("correction_draft", "pending_review"),
        ("published", "revision_pending_review"),
        ("revision_pending_review", "published"),
        ("revision_pending_review", "rejected"),
    }
)


def require_report_transition(source: str, target: str) -> str:
    if (source, target) not in ALLOWED_REPORT_TRANSITIONS:
        raise InvalidReportTransition(source=source, target=target)
    return target
