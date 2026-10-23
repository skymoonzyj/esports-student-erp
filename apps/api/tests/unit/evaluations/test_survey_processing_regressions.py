from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.evaluations import survey_router as router
from app.evaluations.survey_query_service import (
    AdminSurveyPage,
    CampSurveyQueryService,
)

ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000301")
PERSON_ID = UUID("20000000-0000-4000-8000-000000000301")


class _Db:
    def scalar(self, _statement: object) -> object:
        return None

    def scalars(self, _statement: object) -> list[object]:
        return []


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        account=SimpleNamespace(id=ACCOUNT_ID),
        person=SimpleNamespace(id=PERSON_ID),
    )


def _feedback_row(*, cohort_id: UUID, allowed: bool) -> SimpleNamespace:
    snapshot = SimpleNamespace(
        id=uuid4(),
        staff_person_id=PERSON_ID,
        staff_name_snapshot="工作人员",
        role_key_snapshot="primary_coach",
        role_name_snapshot="主教练",
        role_category="teaching",
        roles_snapshot=[
            {
                "role_key": "primary_coach",
                "role_name": "主教练",
                "role_category": "teaching",
            }
        ],
        service_intervals=[],
    )
    cycle = SimpleNamespace(
        id=uuid4(),
        cohort_id=cohort_id,
        class_id=uuid4(),
        student_person_id=uuid4(),
        enrollment_id=uuid4(),
    )
    return SimpleNamespace(
        cycle=cycle,
        submission=SimpleNamespace(id=uuid4()),
        version=SimpleNamespace(
            id=uuid4(),
            version=1,
            course_training_score=9,
            course_training_reason=None,
            life_service_score=8,
            life_service_reason=None,
            service_management_score=7,
            service_management_reason=None,
            overall_suggestion=None,
            favorite_cuisines=[],
            favorite_dishes=[],
        ),
        rating=SimpleNamespace(score=8, reason=None, suggestion=None),
        snapshot=snapshot,
        project_id=None,
        is_supplement=False,
        authorized=allowed,
    )


class _StaffQuery:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def _feedback_rows(self, **_kwargs: object) -> list[SimpleNamespace]:
        return self.rows


def test_unfiltered_my_feedback_paginates_only_rows_allowed_by_each_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed_cohort = uuid4()
    denied_cohort = uuid4()
    query = _StaffQuery(
        [
            _feedback_row(cohort_id=allowed_cohort, allowed=True),
            _feedback_row(cohort_id=denied_cohort, allowed=False),
        ]
    )
    monkeypatch.setattr(router, "_live_staff_account_allowed", lambda **_: True)
    monkeypatch.setattr(router, "_resolve_role_key", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router, "_staff_feedback_target_exists", lambda **_: True)
    monkeypatch.setattr(router, "_staff_feedback_scope_allowed", lambda **_: True)
    monkeypatch.setattr(router, "_snapshot_has_legal_role", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        router,
        "_staff_feedback_row_authorizer",
        lambda **_kwargs: lambda row: row.authorized,
        raising=False,
    )

    result = router.get_my_survey_feedback(
        context=_context(),  # type: ignore[arg-type]
        query=query,  # type: ignore[arg-type]
        db=_Db(),  # type: ignore[arg-type]
    )

    assert result.total == 1
    assert len(result.items) == 1
    assert result.items[0].cohort_id == allowed_cohort


def test_hidden_admin_feedback_redacts_respondent_linked_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort_id = uuid4()
    cycle_id = uuid4()
    enrollment_id = uuid4()
    version_id = uuid4()
    item = SimpleNamespace(
        cycle_id=cycle_id,
        enrollment_id=enrollment_id,
        cohort_id=cohort_id,
        version_id=version_id,
        version=1,
        ordinal=1,
        staff_snapshot_id=None,
        staff_person_id=PERSON_ID,
        staff_name="工作人员",
        role_key="primary_coach",
        role_name="主教练",
        role_category="teaching",
        score=8,
        reason=None,
        suggestion=None,
        respondent_label="匿名评价 #1",
        respondent_person_id=None,
        respondent_name=None,
        respondent_kind=None,
        course_training_score=None,
        course_training_reason=None,
        life_service_score=None,
        life_service_reason=None,
        service_management_score=None,
        service_management_reason=None,
        overall_suggestion=None,
        favorite_cuisines=None,
        favorite_dishes=None,
    )

    class _Query:
        def list_for_admin(self, **_kwargs: object) -> AdminSurveyPage:
            return AdminSurveyPage(items=(item,), total=1, page=1, page_size=50)

    monkeypatch.setattr(router, "_survey_row_authorizer", lambda *args, **kwargs: lambda _row: True)
    monkeypatch.setattr(router, "_survey_global_capability", lambda *args, **kwargs: True)

    result = router.list_admin_surveys(
        request=SimpleNamespace(state=SimpleNamespace(request_id="hidden-redact")),  # type: ignore[arg-type]
        context=_context(),  # type: ignore[arg-type]
        query=_Query(),  # type: ignore[arg-type]
        db=_Db(),  # type: ignore[arg-type]
        identity="hidden",
    )

    payload = result.items[0].model_dump()
    assert payload["cycle_id"] is None
    assert payload["enrollment_id"] is None
    assert payload["version_id"] is None


class _Statement:
    def __init__(self, source: str) -> None:
        self.source = source

    def order_by(self, *_columns: object) -> _Statement:
        return self


class _Result:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self.rows

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        return iter(self.rows)


class _QueryDb:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def execute(self, statement: object) -> _Result:
        if isinstance(statement, _Statement) and statement.source == "main":
            return _Result(self.rows)
        return _Result([])


def test_historical_rating_uses_current_included_snapshot_metadata() -> None:
    staff_id = uuid4()
    cycle = SimpleNamespace(id=uuid4(), cohort_id=uuid4(), class_id=uuid4(), enrollment_id=uuid4())
    submission = SimpleNamespace(id=uuid4(), respondent_person_id=uuid4(), respondent_kind="self")
    version = SimpleNamespace(id=uuid4(), version=1)
    rating = SimpleNamespace(score=4, reason="历史原因", suggestion="历史建议")
    historical_snapshot = SimpleNamespace(
        id=uuid4(),
        staff_person_id=staff_id,
        staff_name_snapshot="旧姓名",
        role_key_snapshot="old_role",
        role_name_snapshot="旧角色",
        role_category="operations",
        roles_snapshot=[{"role_key": "old_role", "role_name": "旧角色", "role_category": "operations"}],
        service_intervals=[{"role_key": "old_role"}],
    )
    current_snapshot = SimpleNamespace(
        id=uuid4(),
        staff_person_id=staff_id,
        staff_name_snapshot="当前姓名",
        role_key_snapshot="life_teacher",
        role_name_snapshot="当前生活老师",
        role_category="life_service",
        roles_snapshot=[
            {
                "role_key": "life_teacher",
                "role_name": "当前生活老师",
                "role_category": "life_service",
            }
        ],
        service_intervals=[{"role_key": "life_teacher", "scope": "cohort"}],
    )
    service = CampSurveyQueryService(
        _QueryDb(
            [
                (
                    cycle,
                    submission,
                    version,
                    rating,
                    historical_snapshot,
                    current_snapshot,
                )
            ]
        )  # type: ignore[arg-type]
    )
    service._main_statement = lambda **_kwargs: _Statement("main")  # type: ignore[method-assign]
    service._supplement_statement = lambda **_kwargs: _Statement("supplement")  # type: ignore[method-assign]

    page = service.list_anonymous(
        include_staff_identity=True,
        role_key="life_teacher",
    )

    assert page.total == 1
    assert page.items[0].score == 4
    assert page.items[0].reason == "历史原因"
    assert page.items[0].staff_name == "当前姓名"
    assert page.items[0].role_key == "life_teacher"
    assert page.items[0].role_name == "当前生活老师"
    assert page.items[0].role_category == "life_service"
    assert page.items[0].cycle_id is None
    assert page.items[0].enrollment_id is None
    assert page.items[0].version_id is None
