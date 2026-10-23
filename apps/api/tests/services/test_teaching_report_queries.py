from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evaluations.report_models import StudentReportVersion
from app.evaluations.report_query_service import TeachingReportQueryService
from app.evaluations.report_service import TeachingReportService
from app.rbac.models import Permission, UserPermissionOverride
from tests.services.test_teaching_report_access import (
    ReportAccessScenario,
    report_access_scenario,
)
from tests.services.test_teaching_reports import draft_input, report_service

__all__ = ["report_access_scenario", "report_service"]

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


def _pending_report(
    db: Session,
    scenario: ReportAccessScenario,
    service: TeachingReportService,
) -> StudentReportVersion:
    created = service.save_draft(
        draft_input(scenario, body="审核人应看到的完整日报正文"),
        actor_user_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    version = StudentReportVersion(
        report_id=created.report.id,
        version=1,
        source="initial",
        body=created.draft.body,
        dimension_values={},
        submitted_by_user_id=scenario.accounts["primary"].id,
        submitted_at=NOW,
    )
    db.add(version)
    db.flush()
    created.report.status = "pending_review"
    created.report.current_version_id = version.id
    created.report.submitted_at = NOW
    db.delete(created.draft)
    db.flush()
    return version


def test_reviewer_queue_is_sql_authorized_and_exposes_only_the_review_projection(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    version = _pending_report(db, report_access_scenario, report_service)

    page = TeachingReportQueryService(db).review_queue(
        account_id=report_access_scenario.accounts["admin"].id,
        occurred_at=NOW,
        cohort_id=report_access_scenario.cohort.id,
        project_id=None,
        class_id=None,
        page=1,
        page_size=20,
    )

    assert page.total == 1
    assert len(page.items) == 1
    item = page.items[0]
    assert item.version_id == version.id
    assert item.body == "审核人应看到的完整日报正文"
    assert item.author_name == report_access_scenario.people["primary"].full_name
    assert item.status == "pending_review"
    assert not hasattr(item, "author_user_id")
    assert not hasattr(item, "phone")
    assert not hasattr(item, "identity_number")
    assert not hasattr(item, "draft_body")
    assert not hasattr(item, "review_reason")
    assert not hasattr(item, "permission_source")


def test_reviewer_queue_hides_self_authored_and_denied_reports_on_the_next_query(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    _pending_report(db, report_access_scenario, report_service)
    query = TeachingReportQueryService(db)
    review_permission = report_access_scenario.db.scalar(
        select(Permission).where(Permission.key == "student_reports.review")
    )
    assert review_permission is not None
    report_access_scenario.db.add(
        UserPermissionOverride(
            user_id=report_access_scenario.accounts["primary"].id,
            permission_id=review_permission.id,
            effect="allow",
            scope_kind="assigned_class",
            scope_id=report_access_scenario.teaching_class.id,
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=30),
            created_by_user_id=report_access_scenario.accounts["admin"].id,
            grant_reason="作者自审禁止测试",
        )
    )
    db.flush()

    assert query.review_queue(
        account_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        cohort_id=None,
        project_id=None,
        class_id=None,
        page=1,
        page_size=20,
    ).items == ()

    report_access_scenario.db.add(
        UserPermissionOverride(
            user_id=report_access_scenario.accounts["admin"].id,
            permission_id=review_permission.id,
            effect="deny",
            scope_kind="enrollment",
            scope_id=report_access_scenario.enrollment.id,
            created_by_user_id=report_access_scenario.accounts["admin"].id,
            grant_reason="审核队列即时撤权测试",
        )
    )
    db.flush()

    assert query.review_queue(
        account_id=report_access_scenario.accounts["admin"].id,
        occurred_at=NOW,
        cohort_id=None,
        project_id=None,
        class_id=None,
        page=1,
        page_size=20,
    ).items == ()
