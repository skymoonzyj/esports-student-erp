"""Small non-PostgreSQL RED checks for Task A's pure authorization contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar, cast
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.evaluations import survey_access, survey_service
from app.evaluations.access import EvaluationResource
from app.evaluations.errors import CampSurveyResourceNotFound
from app.evaluations.survey_access import CampSurveyAccessService

ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000001")
STUDENT_ID = UUID("20000000-0000-4000-8000-000000000001")
CYCLE_ID = UUID("30000000-0000-4000-8000-000000000001")
COHORT_ID = UUID("40000000-0000-4000-8000-000000000001")
ENROLLMENT_ID = UUID("50000000-0000-4000-8000-000000000001")
CLASS_ID = UUID("60000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("70000000-0000-4000-8000-000000000001")


class _Db:
    def __init__(self, *values: object) -> None:
        self.values = list(values)

    def scalar(self, _statement: object) -> object:
        return self.values.pop(0)


class _Decision:
    allowed = False


class _EvaluationAccess:
    resources: ClassVar[list[object]] = []

    def __init__(self, _db: object) -> None:
        pass

    def decide(self, **kwargs: object) -> _Decision:
        self.resources.append(kwargs["resource"])
        return _Decision()


def test_respondent_resolution_denies_when_live_cycle_center_resource_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cycle = SimpleNamespace(
        id=CYCLE_ID,
        student_person_id=STUDENT_ID,
        cohort_id=COHORT_ID,
        enrollment_id=ENROLLMENT_ID,
        class_id=CLASS_ID,
    )
    account = SimpleNamespace(id=ACCOUNT_ID, person_id=STUDENT_ID, status="active")
    monkeypatch.setattr(
        survey_access,
        "EvaluationAccessService",
        _EvaluationAccess,
        raising=False,
    )
    _EvaluationAccess.resources = []
    service = CampSurveyAccessService(
        cast(
            Session,
            _Db(account, cycle, SimpleNamespace(person_id=STUDENT_ID), PROJECT_ID),
        )
    )

    with pytest.raises(CampSurveyResourceNotFound):
        service.resolve_respondent(
            cycle_id=CYCLE_ID,
            account_id=ACCOUNT_ID,
            occurred_at=datetime(2026, 8, 13, 8, tzinfo=UTC),
        )

    assert _EvaluationAccess.resources
    resource = cast(EvaluationResource, _EvaluationAccess.resources[-1])
    assert resource.student_person_id == STUDENT_ID
    assert resource.cohort_id == COHORT_ID
    assert resource.enrollment_id == ENROLLMENT_ID
    assert resource.class_id == CLASS_ID
    assert resource.project_id == PROJECT_ID


def test_pending_supplement_rows_exclude_historical_staff_person_ids() -> None:
    staff_a_v3 = SimpleNamespace(
        id=UUID("80000000-0000-4000-8000-000000000002"), staff_person_id=STUDENT_ID
    )
    staff_b_v3 = SimpleNamespace(
        id=UUID("80000000-0000-4000-8000-000000000003"), staff_person_id=PROJECT_ID
    )

    pending_staff_snapshot_ids = getattr(survey_service, "_pending_staff_snapshot_ids", None)
    assert callable(pending_staff_snapshot_ids)
    assert pending_staff_snapshot_ids(
        (staff_a_v3, staff_b_v3),
        completed_person_ids={STUDENT_ID},
    ) == {staff_b_v3.id}
