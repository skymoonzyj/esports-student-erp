from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.esports_assessments.catalog import get_task
from app.esports_assessments.errors import AssessmentConflict
from app.esports_assessments.lifecycle import require_submission_elapsed


def test_submission_at_62999_milliseconds_is_too_early() -> None:
    created_at = datetime(2026, 9, 23, 2, tzinfo=UTC)

    with pytest.raises(AssessmentConflict) as error:
        require_submission_elapsed(
            created_at,
            get_task("pc-fps", "precision", catalog_version="pc-fps-v3"),
            created_at + timedelta(milliseconds=62_999),
        )

    assert error.value.code == "esports_result_too_early"


def test_submission_at_63_seconds_is_allowed() -> None:
    created_at = datetime(2026, 9, 23, 2, tzinfo=UTC)

    require_submission_elapsed(
        created_at,
        get_task("pc-fps", "precision", catalog_version="pc-fps-v3"),
        created_at + timedelta(seconds=63),
    )


@pytest.mark.parametrize("naive_field", ["created_at", "now"])
def test_submission_timing_rejects_naive_datetimes(naive_field: str) -> None:
    created_at = datetime(2026, 9, 23, 2, tzinfo=UTC)
    now = created_at + timedelta(seconds=63)
    if naive_field == "created_at":
        created_at = created_at.replace(tzinfo=None)
    else:
        now = now.replace(tzinfo=None)

    with pytest.raises(ValueError, match=rf"^{naive_field} must be timezone-aware$"):
        require_submission_elapsed(
            created_at, get_task("pc-fps", "precision", catalog_version="pc-fps-v3"), now
        )
