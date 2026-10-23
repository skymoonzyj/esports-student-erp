"""Focused authorization regressions for the camp-survey router.

These tests exercise the route functions directly so each authorization decision
is isolated from the PostgreSQL fixture.  The production API tests continue to
cover the full request/session boundary in ``test_camp_surveys.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from uuid import UUID

import pytest

from app.core.errors import ProblemError
from app.evaluations import survey_router as router
from app.evaluations.survey_query_service import (
    AnonymousSurveyPage,
    PrintDetail,
    PrintProjection,
    PrintProjectionItem,
    StaffSurveyPage,
)

ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000101")
PERSON_ID = UUID("20000000-0000-4000-8000-000000000101")
COHORT_ID = UUID("30000000-0000-4000-8000-000000000101")
ROLE_ID = UUID("40000000-0000-4000-8000-000000000101")


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        account=SimpleNamespace(id=ACCOUNT_ID, status="active"),
        person=SimpleNamespace(id=PERSON_ID),
    )


class _Db:
    """Small read-only stand-in; route regressions must not need a real DB."""

    def __init__(self) -> None:
        self.commits = 0

    def scalar(self, _statement: object) -> object:
        return None

    def commit(self) -> None:
        self.commits += 1


class _Decision:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed


class _Rbac:
    decisions: ClassVar[dict[str, bool]] = {}

    def __init__(self, _db: object) -> None:
        pass

    def decide(self, *, permission: str, **_kwargs: object) -> _Decision:
        return _Decision(self.decisions.get(permission, False))


class _EvaluationAccess:
    def __init__(self, _db: object) -> None:
        pass

    def resource_tokens(self, **_kwargs: object) -> set[str]:
        return {"global", f"cohort:{COHORT_ID}"}

    def decide(self, *, permission: str, **_kwargs: object) -> _Decision:
        return _Decision(_Rbac.decisions.get(permission, permission == "evaluation_center.access"))


class _Query:
    def __init__(self, *, projection: object | None = None) -> None:
        self.staff_calls: list[dict[str, object]] = []
        self.print_calls: list[dict[str, object]] = []
        self.projection = projection or PrintProjection(identity="hidden", items=())

    def has_staff_snapshot(self, *, staff_person_id: UUID) -> bool:
        del staff_person_id
        return True

    def list_for_staff(self, **kwargs: object) -> StaffSurveyPage:
        self.staff_calls.append(kwargs)
        return StaffSurveyPage(items=(), total=0, page=1, page_size=50)

    def _feedback_rows(self, **kwargs: object) -> list[object]:
        self.staff_calls.append(kwargs)
        return []

    def print_projection(self, **kwargs: object) -> object:
        self.print_calls.append(kwargs)
        row_authorizer = kwargs.get("row_authorizer")
        if callable(row_authorizer) and self.projection.items:
            item = self.projection.items[0]
            row = SimpleNamespace(
                cycle=SimpleNamespace(
                    student_person_id=PERSON_ID,
                    cohort_id=item.cohort_id,
                    enrollment_id=UUID("50000000-0000-4000-8000-000000000101"),
                    class_id=UUID("60000000-0000-4000-8000-000000000101"),
                ),
                snapshot=SimpleNamespace(staff_person_id=item.staff_person_id),
                project_id=item.project_id,
            )
            if not row_authorizer(row):
                return PrintProjection(identity=self.projection.identity, items=())
        return self.projection

    def list_for_admin(self, **kwargs: object) -> AnonymousSurveyPage:
        self.staff_calls.append(kwargs)
        return AnonymousSurveyPage(items=(), total=0, page=1, page_size=50)


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(request_id="survey-router-regression"))


def _print_item() -> PrintProjectionItem:
    return PrintProjectionItem(
        cohort_id=COHORT_ID,
        project_id=None,
        staff_person_id=PERSON_ID,
        staff_name="测试工作人员",
        role_key="primary_coach",
        role_name="主教练",
        role_category="teaching",
        service_intervals=(),
        average_score=8.0,
        item_count=1,
        details=(
            PrintDetail(
                score=8,
                reason=None,
                suggestion=None,
                ordinal=1,
            ),
        ),
    )


def test_my_feedback_rechecks_live_person_before_returning_snapshot_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted person cannot read an old staff snapshot."""

    monkeypatch.setattr(router, "RbacService", _Rbac)
    _Rbac.decisions = {"evaluation_center.access": True}
    query = _Query()

    with pytest.raises(ProblemError) as caught:
        router.get_my_survey_feedback(
            context=_context(),
            query=query,
            db=_Db(),
        )

    assert caught.value.status == 403
    assert caught.value.code == "permission_denied"


def test_my_feedback_accepts_server_owned_cohort_and_role_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route exposes both documented cohort and role filter spellings."""

    monkeypatch.setattr(router, "RbacService", _Rbac)
    _Rbac.decisions = {"evaluation_center.access": True}
    monkeypatch.setattr(router, "_live_staff_account_allowed", lambda **_: True)
    monkeypatch.setattr(router, "_staff_feedback_target_exists", lambda **_: True)
    monkeypatch.setattr(router, "_staff_feedback_scope_allowed", lambda **_: True)
    query = _Query()

    router.get_my_survey_feedback(
        context=_context(),
        query=query,
        db=_Db(),
        cohort_id=COHORT_ID,
        role_key="primary_coach",
    )

    assert query.staff_calls
    assert query.staff_calls[-1]["cohort_id"] == COHORT_ID
    assert query.staff_calls[-1]["role_key"] == "primary_coach"


def test_my_feedback_resolves_role_id_without_trusting_client_role_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(router, "RbacService", _Rbac)
    _Rbac.decisions = {"evaluation_center.access": True}
    monkeypatch.setattr(router, "_live_staff_account_allowed", lambda **_: True)
    monkeypatch.setattr(router, "_staff_feedback_target_exists", lambda **_: True)
    monkeypatch.setattr(router, "_staff_feedback_scope_allowed", lambda **_: True)
    monkeypatch.setattr(
        router,
        "_resolve_role_key",
        lambda _db, *, role_id, role_key: (
            "primary_coach" if role_id == ROLE_ID and role_key is None else role_key
        ),
    )
    query = _Query()

    router.get_my_survey_feedback(
        context=_context(),
        query=query,
        db=_Db(),
        cohort_id=COHORT_ID,
        role_id=ROLE_ID,
    )

    assert query.staff_calls[-1]["role_key"] == "primary_coach"


def test_admin_real_name_list_requires_identity_permission_without_print(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admin list and the dedicated print surface have separate grants."""

    captured: dict[str, object] = {}
    global_captured: dict[str, object] = {}

    def authorizer(_context: object, _db: object, **kwargs: object):
        captured.update(kwargs)
        return lambda _row: True

    monkeypatch.setattr(router, "_survey_row_authorizer", authorizer)
    def global_capability(*_args: object, **kwargs: object) -> bool:
        global_captured.update(kwargs)
        return True

    monkeypatch.setattr(router, "_survey_global_capability", global_capability)
    monkeypatch.setattr(
        router,
        "AuditService",
        lambda _db: SimpleNamespace(append=lambda **_: None),
    )

    router.list_admin_surveys(
        request=_request(),
        context=_context(),
        query=_Query(),
        db=_Db(),
        identity="real_name",
    )

    assert captured["required_permissions"] == ("camp_surveys.identity.read",)
    assert captured["require_all"] is True
    assert global_captured["required_permissions"] == (
        "camp_surveys.identity.read",
    )
    assert global_captured["require_all"] is True


@pytest.mark.parametrize(
    ("identity", "decisions"),
    [
        ("hidden", {"camp_surveys.print": False, "camp_surveys.identity.read": True}),
        ("real_name", {"camp_surveys.print": True, "camp_surveys.identity.read": False}),
    ],
)
def test_print_projection_enforces_permission_pair_before_serializing_rows(
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
    decisions: dict[str, bool],
) -> None:
    """Hidden needs print; real-name needs both print and identity.read."""

    monkeypatch.setattr(router, "RbacService", _Rbac)
    monkeypatch.setattr(router, "EvaluationAccessService", _EvaluationAccess)
    _Rbac.decisions = decisions
    query = _Query(
        projection=PrintProjection(identity=identity, items=(_print_item(),))
    )
    monkeypatch.setattr(router, "AuditService", lambda _db: SimpleNamespace(append=lambda **_: None))

    db = _Db()
    with pytest.raises(ProblemError) as caught:
        router.get_print_projection(
            request=_request(),
            context=_context(),
            query=query,
            db=db,
            identity=identity,  # type: ignore[arg-type]
        )

    assert caught.value.status == 403
    assert caught.value.code == "permission_denied"
    assert db.commits == 0
    assert query.print_calls
    assert query.print_calls[-1]["can_print"] is decisions["camp_surveys.print"]
    assert query.print_calls[-1]["can_read_identity"] is (
        identity == "real_name" and decisions["camp_surveys.identity.read"]
    )


def test_print_unknown_role_target_is_private_not_found_even_with_global_print(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A made-up role must not be converted into an authorized empty result."""

    monkeypatch.setattr(router, "RbacService", _Rbac)
    monkeypatch.setattr(router, "EvaluationAccessService", _EvaluationAccess)
    _Rbac.decisions = {"camp_surveys.print": True}
    query = _Query()

    with pytest.raises(ProblemError) as caught:
        router.get_print_projection(
            request=_request(),
            context=_context(),
            query=query,
            db=_Db(),
            cohort_id=COHORT_ID,
            role_key="not-a-real-survey-role",
        )

    assert caught.value.status == 404
    assert caught.value.code == "evaluation_resource_not_found"
