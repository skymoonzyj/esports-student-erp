from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.audit.models import AuditEvent
from app.evaluations.errors import EvaluationOperationDenied, EvaluationResourceNotFound
from app.evaluations.report_models import (
    StudentReport,
    StudentReportReview,
    StudentReportVersion,
)
from app.evaluations.report_query_service import (
    TeachingReportCapabilities,
    TeachingReportQueryService,
)
from app.evaluations.report_service import TeachingReportService
from app.evaluations.report_workspace_query_service import (
    TeachingReportTargetFilters,
    TeachingReportTargetPage,
    TeachingReportWorkspaceQueryService,
)
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
    EvaluationStaffRole,
)
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.identity.models import Person, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride
from app.teaching.models import GameProject, TeachingClass
from app.teaching.seed import INITIAL_PROJECTS
from tests.services.test_teaching_report_access import ReportAccessScenario
from tests.services.test_teaching_reports import draft_input

pytest_plugins = (
    "tests.services.test_teaching_report_access",
    "tests.services.test_teaching_reports",
)

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


def _write_counts(db: Session) -> tuple[int, int, int]:
    return (
        db.scalar(select(func.count(StudentReport.id))) or 0,
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))) or 0,
        db.scalar(select(func.count(AuditEvent.id))) or 0,
    )


def _add_authorized_student(
    db: Session,
    scenario: ReportAccessScenario,
    *,
    name: str,
    student_number: str,
    class_id: UUID | None = None,
) -> Enrollment:
    token = uuid4().hex
    person = Person(
        full_name=name,
        identity_ciphertext=f"workspace-target:{token}",
        identity_digest=(token * 2)[:64],
        sex="female",
        date_of_birth=date(2010, 1, 2),
    )
    db.add(person)
    db.flush()
    db.add(
        StudentProfile(
            person_id=person.id,
            student_number=student_number,
            status="active",
        )
    )
    admission_case = AdmissionCase(
        student_person_id=person.id,
        status="ready",
        created_by_user_id=scenario.accounts["admin"].id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=person.id,
        cohort_id=scenario.cohort.id,
        class_id=class_id or scenario.teaching_class.id,
        agreed_tuition_cents=0,
        status="active",
        confirmed_at=scenario.cohort.starts_at - timedelta(days=1),
        created_by_user_id=scenario.accounts["admin"].id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    db.flush()
    return enrollment


def _count_selects(db: Session, operation: Callable[[], object]) -> int:
    bind = db.get_bind()
    selects = 0

    def record_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(bind, "before_cursor_execute", record_select)
    try:
        operation()
    finally:
        event.remove(bind, "before_cursor_execute", record_select)
    return selects


def test_author_targets_reuse_real_author_slots_and_return_complete_facets(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    scenario = report_access_scenario
    service = TeachingReportWorkspaceQueryService(db)

    primary = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(page=1, page_size=20),
    )
    assert primary.total == 1
    assert primary.items[0].enrollment_id == scenario.enrollment.id
    assert primary.items[0].student_name == scenario.people["student"].full_name
    assert primary.facets.cohorts[0].id == scenario.cohort.id
    assert primary.facets.projects[0].cohort_ids == (scenario.cohort.id,)
    assert primary.facets.classes[0].project_id == scenario.teaching_class.project_id

    scenario.allow_account("assistant")
    assert service.author_targets(
        account_id=scenario.accounts["assistant"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(page=1, page_size=20),
    ).total == 1

    scenario.attach_staff_permission()
    assert service.author_targets(
        account_id=scenario.accounts["staff"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(page=1, page_size=20),
    ).total == 1


def test_author_targets_remain_visible_through_the_last_weekly_deadline(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    scenario = report_access_scenario
    scenario.cohort.status = "completed"
    scenario.enrollment.status = "completed"
    scenario.enrollment.completed_at = scenario.cohort.ends_at
    db.flush()
    service = TeachingReportWorkspaceQueryService(db)

    last_deadline_minute = datetime(2026, 8, 28, 15, 59, tzinfo=UTC)
    within_window = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=last_deadline_minute,
        filters=TeachingReportTargetFilters(page=1, page_size=20),
    )

    assert within_window.total == 1
    assert within_window.items[0].enrollment_id == scenario.enrollment.id

    after_window = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=datetime(2026, 8, 28, 16, tzinfo=UTC),
        filters=TeachingReportTargetFilters(page=1, page_size=20),
    )

    assert after_window.items == ()
    assert after_window.total == 0


def test_author_target_query_count_is_independent_of_candidate_count(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    scenario = report_access_scenario
    service = TeachingReportWorkspaceQueryService(db)

    small_count = _count_selects(
        db,
        lambda: service.author_targets(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=TeachingReportTargetFilters(page=1, page_size=1),
        ),
    )

    for index in range(99):
        _add_authorized_student(
            db,
            scenario,
            name=f"批量授权学员 {index:03d}",
            student_number=f"RPT-BATCH-{index:03d}",
        )

    large_page: TeachingReportTargetPage | None = None

    def load_large_page() -> None:
        nonlocal large_page
        large_page = service.author_targets(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=TeachingReportTargetFilters(page=1, page_size=1),
        )

    large_count = _count_selects(db, load_large_page)

    assert large_page is not None
    assert large_page.total == 100
    assert small_count <= 30
    assert large_count <= small_count + 2


def test_report_context_author_query_count_is_independent_of_denied_candidates(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    scenario = report_access_scenario
    service = TeachingReportQueryService(db)

    small_capabilities: TeachingReportCapabilities | None = None

    def load_small_context() -> None:
        nonlocal small_capabilities
        small_capabilities = service.capabilities(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
        )

    small_count = _count_selects(db, load_small_context)
    assert small_capabilities is not None
    assert small_capabilities.author_available is True

    unassigned_class = TeachingClass(
        cohort_id=scenario.cohort.id,
        project_id=scenario.teaching_class.project_id,
        name=f"未授权班级-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=200,
        default_tuition_cents=0,
        created_by_user_id=scenario.accounts["admin"].id,
    )
    db.add(unassigned_class)
    db.flush()
    scenario.enrollment.confirmed_at = scenario.cohort.starts_at
    for index in range(99):
        _add_authorized_student(
            db,
            scenario,
            name=f"无权候选学员 {index:03d}",
            student_number=f"RPT-DENIED-{index:03d}",
            class_id=unassigned_class.id,
        )
    db.flush()

    large_capabilities: TeachingReportCapabilities | None = None

    def load_large_context() -> None:
        nonlocal large_capabilities
        large_capabilities = service.capabilities(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
        )

    large_count = _count_selects(db, load_large_context)

    assert large_capabilities is not None
    assert large_capabilities.author_available is True
    assert small_count <= 40
    assert large_count <= small_count + 3


def test_author_targets_include_explicitly_authorized_other_staff(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    scenario = report_access_scenario
    role = EvaluationStaffRole(
        key=f"report_operations_{uuid4().hex[:12]}",
        name="项目运营",
        category="operations",
        created_by_user_id=scenario.accounts["admin"].id,
    )
    db.add(role)
    db.flush()
    assignment = CohortStaffAssignment(
        cohort_id=scenario.cohort.id,
        role_id=role.id,
        staff_person_id=scenario.people["outsider"].id,
        project_id=None,
        class_id=scenario.teaching_class.id,
        role_name_snapshot=role.name,
        role_category_snapshot=role.category,
        valid_from=scenario.cohort.starts_at,
        valid_until=scenario.cohort.ends_at + timedelta(days=1),
        created_by_user_id=scenario.accounts["admin"].id,
    )
    db.add(assignment)
    db.flush()
    db.add(
        CohortStaffAssignmentPermission(
            assignment_id=assignment.id,
            permission_id=scenario.permission.id,
            created_by_user_id=scenario.accounts["admin"].id,
        )
    )
    db.flush()

    page = TeachingReportWorkspaceQueryService(db).author_targets(
        account_id=scenario.accounts["outsider"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(page=1, page_size=20),
    )

    assert page.total == 1
    assert page.items[0].enrollment_id == scenario.enrollment.id


def test_author_targets_have_stable_multi_target_pages_and_reject_mixed_hierarchy(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    scenario = report_access_scenario
    first = _add_authorized_student(
        db,
        scenario,
        name="A 稳定排序学员",
        student_number="RPT-STABLE-A",
    )
    second = _add_authorized_student(
        db,
        scenario,
        name="Z 稳定排序学员",
        student_number="RPT-STABLE-Z",
    )
    service = TeachingReportWorkspaceQueryService(db)

    pages = [
        service.author_targets(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=TeachingReportTargetFilters(page=page, page_size=1),
        )
        for page in (1, 2, 3)
    ]
    repeated = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(page=2, page_size=1),
    )

    assert [page.total for page in pages] == [3, 3, 3]
    assert [page.items[0].enrollment_id for page in pages] == [
        first.id,
        second.id,
        scenario.enrollment.id,
    ]
    assert repeated.items == pages[1].items
    assert len({page.items[0].enrollment_id for page in pages}) == 3
    assert len(pages[0].facets.cohorts) == 1
    assert len(pages[0].facets.projects) == 1
    assert len(pages[0].facets.classes) == 1

    mixed = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(
            project_id=INITIAL_PROJECTS[1].id,
            class_id=scenario.teaching_class.id,
            page=1,
            page_size=20,
        ),
    )
    assert mixed.items == ()
    assert mixed.total == 0
    assert mixed.facets == pages[0].facets

def test_author_target_filters_are_literal_authorized_and_paginated(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    scenario = report_access_scenario
    service = TeachingReportWorkspaceQueryService(db)
    profile = db.get(StudentProfile, scenario.people["student"].id)
    assert profile is not None
    project = db.get(GameProject, scenario.teaching_class.project_id)
    assert project is not None

    matching = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(
            cohort_id=scenario.cohort.id,
            project_id=scenario.teaching_class.project_id,
            class_id=scenario.teaching_class.id,
            student_id=scenario.people["student"].id,
            query=profile.student_number,
            page=1,
            page_size=1,
        ),
    )
    assert matching.total == 1
    assert len(matching.items) == 1
    assert matching.page == matching.page_size == 1

    for literal in ("%", "_", "not-present"):
        empty = service.author_targets(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=TeachingReportTargetFilters(query=literal, page=1, page_size=20),
        )
        assert empty.items == ()
        assert empty.total == 0
        assert empty.facets.cohorts

    for non_student_field in (
        scenario.cohort.name,
        project.name,
        scenario.teaching_class.name,
    ):
        empty = service.author_targets(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=TeachingReportTargetFilters(
                query=non_student_field,
                page=1,
                page_size=20,
            ),
        )
        assert empty.items == ()
        assert empty.total == 0

    wrong_scope = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(class_id=uuid4(), page=1, page_size=20),
    )
    assert wrong_scope.items == ()
    assert wrong_scope.total == 0


def test_author_target_query_length_is_checked_before_casefold_expansion(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    page = TeachingReportWorkspaceQueryService(db).author_targets(
        account_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(
            query="ß" * 80,
            page=1,
            page_size=20,
        ),
    )
    assert page.items == ()
    assert page.total == 0


@pytest.mark.parametrize(
    "filters",
    (
        TeachingReportTargetFilters(page=0, page_size=20),
        TeachingReportTargetFilters(page=1, page_size=0),
        TeachingReportTargetFilters(page=1, page_size=101),
        TeachingReportTargetFilters(query="x" * 81, page=1, page_size=20),
    ),
)
def test_author_targets_reject_invalid_time_and_filters(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    filters: TeachingReportTargetFilters,
) -> None:
    service = TeachingReportWorkspaceQueryService(db)
    with pytest.raises(ValueError):
        service.author_targets(
            account_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=filters,
        )
    with pytest.raises(ValueError):
        service.author_targets(
            account_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW.replace(tzinfo=None),
            filters=TeachingReportTargetFilters(page=1, page_size=20),
        )


@pytest.mark.parametrize(
    "failure",
    (
        "future_assignment",
        "deny",
        "disabled_account",
        "deleted_student",
        "inactive_student",
        "cancelled_enrollment",
        "inactive_project",
        "inactive_class",
    ),
)
def test_author_targets_hide_ineligible_resources_without_writes(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    failure: str,
) -> None:
    scenario = report_access_scenario
    if failure == "future_assignment":
        scenario.primary_assignment.valid_from = NOW + timedelta(hours=1)
    elif failure == "deny":
        scenario.deny_account("primary")
    elif failure == "disabled_account":
        scenario.accounts["primary"].status = "disabled"
    elif failure == "deleted_student":
        scenario.people["student"].deleted_at = NOW
    elif failure == "inactive_student":
        profile = db.get(StudentProfile, scenario.people["student"].id)
        assert profile is not None
        profile.status = "inactive"
    elif failure == "cancelled_enrollment":
        scenario.enrollment.status = "cancelled"
        scenario.enrollment.cancelled_at = NOW
    elif failure == "inactive_project":
        project = db.get(GameProject, scenario.teaching_class.project_id)
        assert project is not None
        project.is_active = False
    else:
        scenario.teaching_class.is_active = False
    db.flush()
    before = _write_counts(db)

    service = TeachingReportWorkspaceQueryService(db)
    if failure in {"future_assignment", "disabled_account"}:
        with pytest.raises(EvaluationOperationDenied):
            service.author_targets(
                account_id=scenario.accounts["primary"].id,
                occurred_at=NOW,
                filters=TeachingReportTargetFilters(page=1, page_size=20),
            )
    else:
        page = service.author_targets(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=TeachingReportTargetFilters(page=1, page_size=20),
        )
        assert page.items == ()
        assert page.total == 0

    after = _write_counts(db)
    assert after == before


def test_report_versions_allow_author_or_reviewer_and_expose_no_internal_identity(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    scenario = report_access_scenario
    created = report_service.save_draft(
        draft_input(scenario, body="版本一正文"),
        actor_user_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"workspace-draft-{uuid4()}",
        request_id=f"workspace-draft-{uuid4()}",
    )
    first = StudentReportVersion(
        report_id=created.report.id,
        version=1,
        source="initial",
        body="版本一正文",
        dimension_values={},
        submitted_by_user_id=scenario.accounts["primary"].id,
        submitted_at=NOW,
    )
    db.add(first)
    db.flush()
    second = StudentReportVersion(
        report_id=created.report.id,
        version=2,
        source="revision",
        body="版本二正文",
        dimension_values={},
        submitted_by_user_id=scenario.accounts["primary"].id,
        submitted_at=NOW + timedelta(hours=2),
        previous_version_id=first.id,
    )
    db.add(second)
    db.flush()
    db.delete(created.draft)
    created.report.status = "rejected"
    created.report.current_version_id = second.id
    created.report.published_version_id = first.id
    created.report.submitted_at = second.submitted_at
    created.report.published_at = NOW + timedelta(hours=1)
    db.add_all(
        [
            StudentReportReview(
                report_id=created.report.id,
                version_id=first.id,
                decision="approved",
                reason=None,
                reviewed_by_user_id=scenario.accounts["admin"].id,
                reviewed_at=NOW + timedelta(hours=1),
            ),
            StudentReportReview(
                report_id=created.report.id,
                version_id=second.id,
                decision="rejected",
                reason="需要补充复盘结论",
                reviewed_by_user_id=scenario.accounts["admin"].id,
                reviewed_at=NOW + timedelta(hours=3),
            ),
        ]
    )
    db.flush()

    service = TeachingReportWorkspaceQueryService(db)
    author_page = service.report_versions(
        created.report.id,
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW + timedelta(hours=4),
        page=1,
        page_size=20,
    )
    reviewer_page = service.report_versions(
        created.report.id,
        account_id=scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=4),
        page=1,
        page_size=20,
    )

    assert [item.version for item in author_page.items] == [2, 1]
    assert reviewer_page.items == author_page.items
    assert author_page.items[0].review is not None
    assert author_page.items[0].review.reason == "需要补充复盘结论"
    assert author_page.items[0].is_current is True
    assert author_page.items[0].is_published is False
    assert author_page.items[1].is_published is True
    assert not hasattr(author_page.items[0], "reviewed_by_user_id")
    assert not hasattr(author_page.items[0], "window_grant_reason")
    assert all(not hasattr(item, "author_user_id") for item in author_page.items)
    assert all(not hasattr(item, "submitted_by_user_id") for item in author_page.items)


def test_report_versions_private_failures_are_denied_or_not_found_without_writes(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    scenario = report_access_scenario
    created = report_service.save_draft(
        draft_input(scenario),
        actor_user_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"workspace-failure-{uuid4()}",
        request_id=f"workspace-failure-{uuid4()}",
    )
    before = _write_counts(db)
    service = TeachingReportWorkspaceQueryService(db)

    with pytest.raises(EvaluationOperationDenied):
        service.report_versions(
            created.report.id,
            account_id=scenario.accounts["outsider"].id,
            occurred_at=NOW,
            page=1,
            page_size=20,
        )
    assert _write_counts(db) == before

    scenario.allow_account("assistant")
    with pytest.raises(EvaluationResourceNotFound):
        service.report_versions(
            created.report.id,
            account_id=scenario.accounts["assistant"].id,
            occurred_at=NOW,
            page=1,
            page_size=20,
        )
    assert _write_counts(db) == before

    with pytest.raises(EvaluationResourceNotFound):
        service.report_versions(
            uuid4(),
            account_id=scenario.accounts["admin"].id,
            occurred_at=NOW,
            page=1,
            page_size=20,
        )
    assert _write_counts(db) == before

    permission = db.scalar(
        select(Permission).where(Permission.key == "student_reports.review")
    )
    assert permission is not None
    db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["admin"].id,
            permission_id=permission.id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=30),
            created_by_user_id=scenario.accounts["admin"].id,
        )
    )
    db.flush()
    with pytest.raises(EvaluationResourceNotFound):
        service.report_versions(
            created.report.id,
            account_id=scenario.accounts["admin"].id,
            occurred_at=NOW,
            page=1,
            page_size=20,
        )
    assert _write_counts(db) == before


@pytest.mark.parametrize(
    "page,page_size",
    ((0, 20), (1, 0), (1, 101)),
)
def test_report_versions_reject_invalid_pagination(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    page: int,
    page_size: int,
) -> None:
    service = TeachingReportWorkspaceQueryService(db)
    with pytest.raises(ValueError):
        service.report_versions(
            uuid4(),
            account_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW,
            page=page,
            page_size=page_size,
        )
    with pytest.raises(ValueError):
        service.report_versions(
            uuid4(),
            account_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW.replace(tzinfo=None),
            page=1,
            page_size=20,
        )
