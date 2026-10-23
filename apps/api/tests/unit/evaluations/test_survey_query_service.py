from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.evaluations.survey_models import CampSurveyStaffSnapshot
from app.evaluations.survey_query_service import CampSurveyQueryService


def test_query_service_exposes_anonymous_and_staff_projection_methods() -> None:
    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]
    assert hasattr(service, "list_anonymous")
    assert hasattr(service, "list_for_staff")
    assert hasattr(service, "has_staff_snapshot")


def test_staff_projection_is_a_privacy_safe_whitelist() -> None:
    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]
    item = service.staff_item_from_anonymous(
        cycle_id=uuid4(), cohort_id=uuid4(), version=1, ordinal=1,
        role_category="teaching", score=9, reason="清晰", suggestion="继续",
        course_training_score=8, life_service_score=10,
        course_training_reason="课程原因",
        life_service_reason="生活原因",
        service_management_score=7,
        service_management_reason="管理原因",
        overall_suggestion="总体建议",
    )
    assert item.score == 9
    assert item.course_training_score == 8
    assert item.course_training_reason == "课程原因"
    assert item.team_suggestion == "总体建议"
    assert not hasattr(item, "respondent_person_id")
    assert not hasattr(item, "staff_person_id")
    assert not hasattr(item, "submitted_at")
    assert item.life_service_score is None
    assert item.life_service_reason is None
    assert item.service_management_score is None
    assert item.service_management_reason is None


def test_staff_projection_includes_project_and_role_context_without_identity() -> None:
    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]
    item = service.staff_item_from_anonymous(
        cycle_id=uuid4(),
        cohort_id=uuid4(),
        project_id=uuid4(),
        role_key="primary_coach",
        role_name="主教练",
        version=1,
        ordinal=1,
        role_category="teaching",
        score=9,
        reason=None,
        suggestion=None,
    )
    assert item.project_id is not None
    assert item.role_key == "primary_coach"
    assert item.role_name == "主教练"
    assert not hasattr(item, "respondent_person_id")


def test_admin_and_print_query_methods_are_available() -> None:
    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]
    assert hasattr(service, "list_for_admin")
    assert hasattr(service, "print_projection")


def test_print_projection_rejects_identity_without_explicit_authorization() -> None:
    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]
    with pytest.raises(PermissionError):
        service.print_projection(identity="real_name", can_print=True, can_read_identity=False)


def test_staff_projection_treats_all_snapshot_roles_as_capabilities() -> None:
    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]
    item = service.staff_item_from_anonymous(
        cycle_id=uuid4(), cohort_id=uuid4(), version=1, ordinal=1,
        role_category="operations", role_categories=("operations", "teaching"),
        score=9, reason=None, suggestion=None,
        course_training_score=8, course_training_reason="课程原因",
        life_service_score=7, life_service_reason="生活原因",
        service_management_score=6, service_management_reason="管理原因",
        overall_suggestion="教学建议",
    )
    assert item.course_training_score == 8
    assert item.course_training_reason == "课程原因"
    assert item.service_management_score == 6
    assert item.service_management_reason == "管理原因"
    assert item.life_service_score is None
    assert item.life_service_reason is None
    assert item.team_suggestion == "教学建议"


def test_staff_projection_exposes_life_service_summary_only_for_life_roles() -> None:
    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]
    item = service.staff_item_from_anonymous(
        cycle_id=uuid4(),
        cohort_id=uuid4(),
        version=1,
        ordinal=1,
        role_category="life_service",
        score=9,
        reason=None,
        suggestion=None,
        course_training_score=8,
        course_training_reason="课程原因",
        life_service_score=7,
        life_service_reason="生活原因",
        service_management_score=6,
        service_management_reason="管理原因",
        overall_suggestion="总体建议",
    )

    assert item.life_service_score == 7
    assert item.life_service_reason == "生活原因"
    assert item.course_training_score is None
    assert item.course_training_reason is None
    assert item.service_management_score is None
    assert item.service_management_reason is None
    assert item.team_suggestion is None


def test_print_projection_unions_roles_across_staff_snapshots() -> None:
    """A later roster snapshot can add teaching capability to one staff group."""

    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]
    staff_id = uuid4()
    cohort_id = uuid4()
    cycle_id = uuid4()

    def snapshot(
        *, role_category: str, roles_snapshot: list[dict[str, str]], role_key: str
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid4(),
            staff_person_id=staff_id,
            staff_name_snapshot="兼任工作人员",
            role_key_snapshot=role_key,
            role_name_snapshot=role_key,
            role_category=role_category,
            roles_snapshot=roles_snapshot,
            service_intervals=[],
        )

    life_snapshot = snapshot(
        role_category="life_service",
        role_key="life_teacher",
        roles_snapshot=[
            {
                "role_category": "life_service",
                "role_key": "life_teacher",
                "role_name": "生活老师",
            }
        ],
    )
    teaching_snapshot = snapshot(
        role_category="teaching",
        role_key="primary_coach",
        roles_snapshot=[
            {
                "role_category": "teaching",
                "role_key": "primary_coach",
                "role_name": "主教练",
            },
            {
                "role_category": "life_service",
                "role_key": "life_teacher",
                "role_name": "生活老师",
            },
        ],
    )

    def feedback_row(snapshot_row: SimpleNamespace, suggestion: str) -> SimpleNamespace:
        return SimpleNamespace(
            cycle=SimpleNamespace(id=cycle_id, cohort_id=cohort_id, class_id=uuid4()),
            submission=SimpleNamespace(id=uuid4()),
            version=SimpleNamespace(version=1, overall_suggestion=suggestion),
            rating=SimpleNamespace(score=8, reason=None, suggestion=None),
            snapshot=snapshot_row,
            project_id=None,
            is_supplement=False,
        )

    service._feedback_rows = lambda **_: [  # type: ignore[method-assign]
        feedback_row(life_snapshot, "生活建议"),
        feedback_row(teaching_snapshot, "教学建议"),
    ]

    projection = service.print_projection()

    assert len(projection.items) == 1
    assert projection.items[0].team_suggestions == ("生活建议", "教学建议")


def test_staff_projection_uses_roles_snapshot_category_union() -> None:
    """A non-leading life-service category still receives teaching fields."""

    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]
    staff_id = uuid4()
    cycle = SimpleNamespace(id=uuid4(), cohort_id=uuid4(), class_id=uuid4())
    snapshot = SimpleNamespace(
        id=uuid4(),
        staff_person_id=staff_id,
        staff_name_snapshot="兼任工作人员",
        role_key_snapshot="life_teacher",
        role_name_snapshot="生活老师",
        role_category="life_service",
        roles_snapshot=[
            {
                "role_category": "life_service",
                "role_key": "life_teacher",
                "role_name": "生活老师",
            },
            {
                "role_category": "teaching",
                "role_key": "primary_coach",
                "role_name": "主教练",
            },
        ],
    )
    row = SimpleNamespace(
        cycle=cycle,
        submission=SimpleNamespace(id=uuid4()),
        version=SimpleNamespace(
            version=1,
            course_training_score=9,
            course_training_reason="课程原因",
            life_service_score=8,
            life_service_reason="生活原因",
            service_management_score=7,
            service_management_reason="管理原因",
            overall_suggestion="教学团队建议",
        ),
        rating=SimpleNamespace(score=10, reason="清晰", suggestion="继续"),
        snapshot=snapshot,
        project_id=None,
        is_supplement=False,
    )
    service._feedback_rows = lambda **_: [row]  # type: ignore[method-assign]

    result = service.list_for_staff(staff_person_id=staff_id)

    assert len(result.items) == 1
    assert result.items[0].course_training_score == 9
    assert result.items[0].team_suggestion == "教学团队建议"


def test_role_key_filter_matches_a_non_leading_snapshot_role() -> None:
    service = CampSurveyQueryService(SimpleNamespace())  # type: ignore[arg-type]

    statement = service._apply_filters(
        select(CampSurveyStaffSnapshot),
        cohort_id=None,
        staff_person_id=None,
        role_key="primary_coach",
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
        )
    )

    assert "role_key_snapshot" in sql
    assert "roles_snapshot" in sql
    assert "role_key" in sql
