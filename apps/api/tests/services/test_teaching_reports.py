from __future__ import annotations

import base64
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.audit.models import AuditEvent
from app.core.config import Settings
from app.evaluations import dependencies as evaluation_dependencies
from app.evaluations import report_schemas
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationResourceNotFound,
    EvaluationSelfReviewForbidden,
    TeachingReportDraftVersionConflict,
    TeachingReportStateConflict,
    TeachingReportWindowClosed,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.report_models import (
    StudentReport,
    StudentReportDraft,
    StudentReportReview,
    StudentReportVersion,
    StudentReportWindowGrant,
)
from app.evaluations.report_periods import correction_deadline
from app.evaluations.report_query_service import TeachingReportQueryService
from app.evaluations.report_schemas import (
    TeachingReportDraftSave,
    TeachingReportDraftStartInput,
    TeachingReportReopenInput,
    TeachingReportReviewInput,
    TeachingReportSubmitInput,
)
from app.evaluations.report_service import TeachingReportService
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride
from app.teaching.models import GameProject
from tests.services.test_teaching_report_access import (
    ReportAccessScenario,
    report_access_scenario,
)

__all__ = ["report_access_scenario"]

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


@pytest.fixture
def report_service(
    db: Session,
) -> TeachingReportService:
    return TeachingReportService(
        db,
        idempotency=EvaluationIdempotencyService(db, digest_key=b"r" * 32),
    )


def draft_input(
    scenario: ReportAccessScenario,
    *,
    kind: str = "daily",
    starts_on: date = date(2026, 8, 1),
    body: str = "学员今日补刀训练稳定，沟通有明显进步。",
    expected_revision: int = 0,
) -> TeachingReportDraftSave:
    return TeachingReportDraftSave.model_validate(
        {
            "enrollment_id": scenario.enrollment.id,
            "report_kind": kind,
            "period_start_date": starts_on,
            "body": body,
            "dimension_values": {},
            "expected_draft_revision": expected_revision,
        }
    )


def _submitted_report(
    scenario: ReportAccessScenario,
    service: TeachingReportService,
) -> tuple[StudentReport, StudentReportVersion]:
    created = service.save_draft(
        draft_input(scenario),
        actor_user_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    submitted = service.submit(
        created.report.id,
        TeachingReportSubmitInput(
            expected_lock_version=created.report.lock_version,
            expected_draft_revision=created.draft.draft_revision,
        ),
        actor_user_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-submit-{uuid4()}",
        request_id=f"report-submit-{uuid4()}",
    )
    return submitted.report, submitted.version


def _allow_review(scenario: ReportAccessScenario, key: str) -> None:
    permission = scenario.db.scalar(
        select(Permission).where(Permission.key == "student_reports.review")
    )
    assert permission is not None
    scenario.db.add(
        UserPermissionOverride(
            user_id=scenario.accounts[key].id,
            permission_id=permission.id,
            effect="allow",
            scope_kind="assigned_class",
            scope_id=scenario.teaching_class.id,
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=30),
            created_by_user_id=scenario.accounts["admin"].id,
            grant_reason="教学评语审核测试授权",
        )
    )
    scenario.db.flush()


def _rejected_report(
    scenario: ReportAccessScenario,
    service: TeachingReportService,
    *,
    rejected_at: datetime = NOW + timedelta(hours=1),
) -> tuple[StudentReport, StudentReportVersion, StudentReportReview]:
    report, version = _submitted_report(scenario, service)
    reviewed = service.review(
        report.id,
        TeachingReportReviewInput(
            expected_lock_version=report.lock_version,
            decision="rejected",
            reason="请补充具体训练数据。",
        ),
        reviewer_user_id=scenario.accounts["admin"].id,
        occurred_at=rejected_at,
        idempotency_key=f"report-review-{uuid4()}",
        request_id=f"report-review-{uuid4()}",
    )
    return reviewed.report, version, reviewed.review


def _published_report(
    scenario: ReportAccessScenario,
    service: TeachingReportService,
    *,
    published_at: datetime = NOW + timedelta(hours=1),
) -> tuple[StudentReport, StudentReportVersion]:
    report, version = _submitted_report(scenario, service)
    reviewed = service.review(
        report.id,
        TeachingReportReviewInput(
            expected_lock_version=report.lock_version,
            decision="approved",
            reason=None,
        ),
        reviewer_user_id=scenario.accounts["admin"].id,
        occurred_at=published_at,
        idempotency_key=f"report-review-{uuid4()}",
        request_id=f"report-review-{uuid4()}",
    )
    return reviewed.report, version


def test_submission_and_review_schemas_are_strict_and_decision_aware() -> None:
    submit_type = report_schemas.TeachingReportSubmitInput
    review_type = report_schemas.TeachingReportReviewInput

    submitted = submit_type.model_validate(
        {"expected_lock_version": 1, "expected_draft_revision": 2}
    )
    assert submitted.expected_lock_version == 1
    assert submitted.expected_draft_revision == 2
    with pytest.raises(ValidationError):
        submit_type.model_validate(
            {
                "expected_lock_version": 1,
                "expected_draft_revision": 2,
                "unexpected": True,
            }
        )
    for payload in (
        {"expected_lock_version": True, "expected_draft_revision": 2},
        {"expected_lock_version": 1, "expected_draft_revision": 0},
    ):
        with pytest.raises(ValidationError):
            submit_type.model_validate(payload)

    approved = review_type.model_validate(
        {"expected_lock_version": 2, "decision": "approved", "reason": None}
    )
    assert approved.reason is None
    rejected = review_type.model_validate(
        {
            "expected_lock_version": 2,
            "decision": "rejected",
            "reason": "  训练内容过于笼统，请补充具体数据。  ",
        }
    )
    assert rejected.reason == "训练内容过于笼统，请补充具体数据。"
    for invalid_review_payload in (
        {"expected_lock_version": 2, "decision": "rejected", "reason": None},
        {"expected_lock_version": 2, "decision": "rejected", "reason": "   "},
        {"expected_lock_version": 2, "decision": "approved", "reason": "不应填写"},
        {"expected_lock_version": 2, "decision": "unknown", "reason": None},
    ):
        with pytest.raises(ValidationError):
            review_type.model_validate(invalid_review_payload)


def test_draft_start_and_reopen_schemas_require_versions_and_business_reasons() -> None:
    start_type = report_schemas.TeachingReportDraftStartInput
    reopen_type = report_schemas.TeachingReportReopenInput

    assert start_type.model_validate({"expected_lock_version": 3}).expected_lock_version == 3
    with pytest.raises(ValidationError):
        start_type.model_validate({"expected_lock_version": 0})
    reopened = reopen_type.model_validate(
        {
            "expected_lock_version": 3,
            "grant_kind": "correction",
            "reason": "  教练因网络中断错过整改时间，批准重新开放。  ",
        }
    )
    assert reopened.reason == "教练因网络中断错过整改时间，批准重新开放。"
    for payload in (
        {"expected_lock_version": 3, "grant_kind": "correction", "reason": ""},
        {"expected_lock_version": 3, "grant_kind": "unknown", "reason": "原因"},
        {"expected_lock_version": True, "grant_kind": "revision", "reason": "原因"},
    ):
        with pytest.raises(ValidationError):
            reopen_type.model_validate(payload)


def test_report_dependencies_use_the_production_lookup_digest_key(
    db: Session,
    test_settings: Settings,
) -> None:
    request = cast(
        Request,
        SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(settings=test_settings))
        ),
    )
    get_service = evaluation_dependencies.get_teaching_report_service
    get_query = evaluation_dependencies.get_teaching_report_query_service

    service = get_service(request, db)
    query = get_query(db)

    raw_lookup_key = test_settings.field_lookup_key.get_secret_value()
    assert service.idempotency.digest_key == base64.urlsafe_b64decode(raw_lookup_key)
    assert query.db is db


@pytest.mark.parametrize(
    "payload",
    [
        {"body": "   "},
        {"body": "有效正文", "report_kind": "monthly"},
        {"body": "有效正文", "expected_draft_revision": True},
        {"body": "有效正文", "expected_draft_revision": -1},
        {"body": "有效正文", "dimension_values": []},
        {"body": "有效正文", "dimension_values": {"unconfirmed_score": 10}},
        {"body": "有效正文", "unexpected": "field"},
    ],
)
def test_draft_schema_rejects_loose_or_invalid_inputs(payload: dict[str, object]) -> None:
    base: dict[str, object] = {
        "enrollment_id": uuid4(),
        "report_kind": "daily",
        "period_start_date": date(2026, 8, 1),
        "body": "有效正文",
        "dimension_values": {},
        "expected_draft_revision": 0,
    }
    base.update(payload)

    with pytest.raises(ValidationError):
        TeachingReportDraftSave.model_validate(base)


def test_primary_coach_creates_a_scoped_draft_and_safe_audit(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    request_id = f"report-draft-{uuid4()}"

    result = report_service.save_draft(
        draft_input(report_access_scenario),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=request_id,
    )

    assert result.report.status == "draft"
    assert result.report.enrollment_id == report_access_scenario.enrollment.id
    assert result.report.author_person_id == report_access_scenario.people["primary"].id
    assert result.report.author_source == "coach_assignment"
    assert result.report.role_key_snapshot == "primary_coach"
    assert result.draft.body == "学员今日补刀训练稳定，沟通有明显进步。"
    assert result.draft.draft_revision == 1
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert audit.action == "student_report.draft.save"
    assert audit.resource_id == result.report.id
    assert result.draft.body not in str(audit.event_metadata)
    idempotency = db.scalar(select(EvaluationIdempotencyKey))
    assert idempotency is not None
    assert result.draft.body not in idempotency.payload_digest
    assert result.draft.body not in idempotency.key_digest


def test_same_idempotency_key_returns_one_draft_without_duplicate_audit(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    key = f"report-draft-{uuid4()}"
    input = draft_input(report_access_scenario)
    first = report_service.save_draft(
        input,
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=key,
        request_id=f"report-draft-{uuid4()}",
    )
    second = report_service.save_draft(
        input,
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=key,
        request_id=f"report-draft-{uuid4()}",
    )

    assert second.report.id == first.report.id
    assert second.draft.draft_revision == 1
    assert db.scalar(select(func.count(StudentReport.id))) == 1
    assert db.scalar(select(func.count(AuditEvent.id))) == 1
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == 1


def test_reusing_an_idempotency_key_with_changed_text_is_rejected(
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    key = f"report-draft-{uuid4()}"
    report_service.save_draft(
        draft_input(report_access_scenario),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=key,
        request_id=f"report-draft-{uuid4()}",
    )

    with pytest.raises(EvaluationIdempotencyConflict):
        report_service.save_draft(
            draft_input(report_access_scenario, body="不同正文"),
            actor_user_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW,
            idempotency_key=key,
            request_id=f"report-draft-{uuid4()}",
        )


def test_draft_revision_is_optimistic_and_increments_once(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    first = report_service.save_draft(
        draft_input(report_access_scenario),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    second = report_service.save_draft(
        draft_input(
            report_access_scenario,
            body="第二版训练记录",
            expected_revision=first.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    assert second.draft.body == "第二版训练记录"
    assert second.draft.draft_revision == 2

    with pytest.raises(TeachingReportDraftVersionConflict):
        report_service.save_draft(
            draft_input(
                report_access_scenario,
                body="过期编辑",
                expected_revision=1,
            ),
            actor_user_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW,
            idempotency_key=f"report-draft-{uuid4()}",
            request_id=f"report-draft-{uuid4()}",
        )
    assert db.scalar(select(1)) == 1


def test_weekly_draft_can_be_saved_before_weekly_submission_opens(
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    result = report_service.save_draft(
        draft_input(
            report_access_scenario,
            kind="weekly",
            starts_on=date(2026, 8, 8),
            body="第二周进行中的周报草稿",
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )

    assert result.slot.submit_allowed is False
    assert result.slot.draft_allowed is True


def test_initial_submit_creates_one_immutable_version_and_safe_audit(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    created = report_service.save_draft(
        draft_input(report_access_scenario),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    request_id = f"report-submit-{uuid4()}"

    submitted = report_service.submit(
        created.report.id,
        TeachingReportSubmitInput(
            expected_lock_version=created.report.lock_version,
            expected_draft_revision=created.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-submit-{uuid4()}",
        request_id=request_id,
    )

    assert submitted.report.status == "pending_review"
    assert submitted.report.lock_version == 2
    assert submitted.report.current_version_id == submitted.version.id
    assert submitted.report.published_version_id is None
    assert submitted.report.submitted_at == NOW
    assert submitted.version.version == 1
    assert submitted.version.source == "initial"
    assert submitted.version.body == "学员今日补刀训练稳定，沟通有明显进步。"
    assert submitted.version.previous_version_id is None
    assert db.get(StudentReportDraft, created.report.id) is None
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert audit.action == "student_report.submit"
    assert submitted.version.body not in str(audit.event_metadata)


def test_weekly_report_cannot_submit_until_the_period_has_ended(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    created = report_service.save_draft(
        draft_input(
            report_access_scenario,
            kind="weekly",
            starts_on=date(2026, 8, 8),
            body="第二周尚未结束的周报草稿",
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    before = (
        db.scalar(select(func.count(StudentReportVersion.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    with pytest.raises(TeachingReportWindowClosed):
        report_service.submit(
            created.report.id,
            TeachingReportSubmitInput(
                expected_lock_version=created.report.lock_version,
                expected_draft_revision=created.draft.draft_revision,
            ),
            actor_user_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW,
            idempotency_key=f"report-submit-{uuid4()}",
            request_id=f"report-submit-{uuid4()}",
        )

    assert db.get(StudentReportDraft, created.report.id) is not None
    assert (
        db.scalar(select(func.count(StudentReportVersion.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before


def test_submit_replay_is_idempotent_and_a_changed_payload_is_rejected(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    created = report_service.save_draft(
        draft_input(report_access_scenario),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    input = TeachingReportSubmitInput(
        expected_lock_version=created.report.lock_version,
        expected_draft_revision=created.draft.draft_revision,
    )
    key = f"report-submit-{uuid4()}"
    first = report_service.submit(
        created.report.id,
        input,
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=key,
        request_id=f"report-submit-{uuid4()}",
    )
    audit_count = db.scalar(select(func.count(AuditEvent.id)))
    idempotency_count = db.scalar(select(func.count(EvaluationIdempotencyKey.id)))

    replay = report_service.submit(
        created.report.id,
        input,
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=key,
        request_id=f"report-submit-{uuid4()}",
    )

    assert replay.idempotent is True
    assert replay.version.id == first.version.id
    assert db.scalar(select(func.count(StudentReportVersion.id))) == 1
    assert db.scalar(select(func.count(AuditEvent.id))) == audit_count
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == idempotency_count
    with pytest.raises(EvaluationIdempotencyConflict):
        report_service.submit(
            created.report.id,
            TeachingReportSubmitInput(
                expected_lock_version=input.expected_lock_version + 1,
                expected_draft_revision=input.expected_draft_revision,
            ),
            actor_user_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW,
            idempotency_key=key,
            request_id=f"report-submit-{uuid4()}",
        )


def test_expired_or_stale_submit_keeps_the_draft_and_session_usable(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    created = report_service.save_draft(
        draft_input(report_access_scenario),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    with pytest.raises(TeachingReportStateConflict):
        report_service.submit(
            created.report.id,
            TeachingReportSubmitInput(
                expected_lock_version=created.report.lock_version + 1,
                expected_draft_revision=created.draft.draft_revision,
            ),
            actor_user_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW,
            idempotency_key=f"report-submit-{uuid4()}",
            request_id=f"report-submit-{uuid4()}",
        )
    assert db.scalar(select(1)) == 1

    with pytest.raises(TeachingReportWindowClosed):
        report_service.submit(
            created.report.id,
            TeachingReportSubmitInput(
                expected_lock_version=created.report.lock_version,
                expected_draft_revision=created.draft.draft_revision,
            ),
            actor_user_id=report_access_scenario.accounts["primary"].id,
            occurred_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
            idempotency_key=f"report-submit-{uuid4()}",
            request_id=f"report-submit-{uuid4()}",
        )
    assert db.get(StudentReportDraft, created.report.id) is not None
    assert db.scalar(select(func.count(StudentReportVersion.id))) == 0


def test_independent_reviewer_approves_and_publishes_the_current_version(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, version = _submitted_report(report_access_scenario, report_service)
    request_id = f"report-review-{uuid4()}"

    reviewed = report_service.review(
        report.id,
        TeachingReportReviewInput(
            expected_lock_version=report.lock_version,
            decision="approved",
            reason=None,
        ),
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        idempotency_key=f"report-review-{uuid4()}",
        request_id=request_id,
    )

    assert reviewed.report.status == "published"
    assert reviewed.report.current_version_id == version.id
    assert reviewed.report.published_version_id == version.id
    assert reviewed.report.published_at == NOW + timedelta(hours=1)
    assert reviewed.report.lock_version == 3
    assert reviewed.review.version_id == version.id
    assert reviewed.review.decision == "approved"
    assert reviewed.review.reason is None
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert audit.action == "student_report.review"
    assert audit.event_metadata["review_decision"] == "approved"


def test_review_replay_returns_the_original_decision_without_duplicate_history(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, _ = _submitted_report(report_access_scenario, report_service)
    input = TeachingReportReviewInput(
        expected_lock_version=report.lock_version,
        decision="approved",
        reason=None,
    )
    key = f"report-review-{uuid4()}"
    first = report_service.review(
        report.id,
        input,
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        idempotency_key=key,
        request_id=f"report-review-{uuid4()}",
    )
    audit_count = db.scalar(select(func.count(AuditEvent.id)))

    replay = report_service.review(
        report.id,
        input,
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        idempotency_key=key,
        request_id=f"report-review-{uuid4()}",
    )

    assert replay.idempotent is True
    assert replay.review.id == first.review.id
    assert db.scalar(select(func.count(StudentReportReview.id))) == 1
    assert db.scalar(select(func.count(AuditEvent.id))) == audit_count


def test_rejection_records_the_business_reason_but_never_copies_it_to_audit(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, version = _submitted_report(report_access_scenario, report_service)
    request_id = f"report-review-{uuid4()}"
    reason = "缺少当天训练目标完成情况，请补充后重新提交。"

    reviewed = report_service.review(
        report.id,
        TeachingReportReviewInput(
            expected_lock_version=report.lock_version,
            decision="rejected",
            reason=reason,
        ),
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=1),
        idempotency_key=f"report-review-{uuid4()}",
        request_id=request_id,
    )

    assert reviewed.report.status == "rejected"
    assert reviewed.report.current_version_id == version.id
    assert reviewed.report.published_version_id is None
    assert reviewed.report.correction_due_at == correction_deadline(
        NOW + timedelta(hours=1)
    )
    assert reviewed.review.reason == reason
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert reason not in str(audit.event_metadata)
    assert audit.purpose is None


def test_author_cannot_self_review_even_with_review_permission(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, _ = _submitted_report(report_access_scenario, report_service)
    _allow_review(report_access_scenario, "primary")
    before = (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    with pytest.raises(EvaluationSelfReviewForbidden):
        report_service.review(
            report.id,
            TeachingReportReviewInput(
                expected_lock_version=report.lock_version,
                decision="approved",
                reason=None,
            ),
            reviewer_user_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW + timedelta(hours=1),
            idempotency_key=f"report-review-{uuid4()}",
            request_id=f"report-review-{uuid4()}",
        )

    assert (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before
    assert db.scalar(select(1)) == 1


def test_removing_scoped_review_permission_blocks_the_next_review_without_writes(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, _ = _submitted_report(report_access_scenario, report_service)
    _allow_review(report_access_scenario, "outsider")
    queue = TeachingReportQueryService(db).review_queue(
        account_id=report_access_scenario.accounts["outsider"].id,
        occurred_at=NOW + timedelta(minutes=1),
        cohort_id=None,
        project_id=None,
        class_id=None,
        page=1,
        page_size=20,
    )
    assert [item.report_id for item in queue.items] == [report.id]
    override = db.scalar(
        select(UserPermissionOverride)
        .join(Permission, Permission.id == UserPermissionOverride.permission_id)
        .where(
            UserPermissionOverride.user_id
            == report_access_scenario.accounts["outsider"].id,
            Permission.key == "student_reports.review",
            UserPermissionOverride.effect == "allow",
        )
    )
    assert override is not None
    db.delete(override)
    db.flush()
    before = (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    with pytest.raises(EvaluationResourceNotFound):
        report_service.review(
            report.id,
            TeachingReportReviewInput(
                expected_lock_version=report.lock_version,
                decision="approved",
                reason=None,
            ),
            reviewer_user_id=report_access_scenario.accounts["outsider"].id,
            occurred_at=NOW + timedelta(minutes=2),
            idempotency_key=f"report-review-{uuid4()}",
            request_id=f"report-review-{uuid4()}",
        )

    assert (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before


def test_rejected_author_starts_a_three_day_correction_draft_from_the_version(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, rejected_version, _ = _rejected_report(
        report_access_scenario,
        report_service,
    )
    request_id = f"report-correction-{uuid4()}"

    correction = report_service.start_correction(
        report.id,
        TeachingReportDraftStartInput(
            expected_lock_version=report.lock_version,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW + timedelta(days=1),
        idempotency_key=f"report-correction-{uuid4()}",
        request_id=request_id,
    )

    assert correction.report.status == "correction_draft"
    assert correction.report.current_version_id == rejected_version.id
    assert correction.report.published_version_id is None
    assert correction.report.lock_version == 4
    assert correction.draft.draft_kind == "correction"
    assert correction.draft.draft_revision == 1
    assert correction.draft.body == rejected_version.body
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert audit.action == "student_report.correction_draft.start"
    assert rejected_version.body not in str(audit.event_metadata)


def test_correction_start_replays_the_original_draft_without_duplicate_writes(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, _, _ = _rejected_report(report_access_scenario, report_service)
    input = TeachingReportDraftStartInput(
        expected_lock_version=report.lock_version,
    )
    key = f"report-correction-{uuid4()}"
    first = report_service.start_correction(
        report.id,
        input,
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW + timedelta(days=1),
        idempotency_key=key,
        request_id=f"report-correction-{uuid4()}",
    )
    audit_count = db.scalar(select(func.count(AuditEvent.id)))
    idempotency_count = db.scalar(select(func.count(EvaluationIdempotencyKey.id)))

    replay = report_service.start_correction(
        report.id,
        input,
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW + timedelta(days=1),
        idempotency_key=key,
        request_id=f"report-correction-{uuid4()}",
    )

    assert replay.idempotent is True
    assert replay.draft.report_id == first.draft.report_id
    assert replay.draft.draft_revision == first.draft.draft_revision
    assert db.scalar(select(func.count(AuditEvent.id))) == audit_count
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == idempotency_count


def test_expired_correction_window_requires_a_reviewer_reopen(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, _, _ = _rejected_report(report_access_scenario, report_service)
    assert report.correction_due_at is not None

    with pytest.raises(TeachingReportWindowClosed):
        report_service.start_correction(
            report.id,
            TeachingReportDraftStartInput(
                expected_lock_version=report.lock_version,
            ),
            actor_user_id=report_access_scenario.accounts["primary"].id,
            occurred_at=report.correction_due_at + timedelta(microseconds=1),
            idempotency_key=f"report-correction-{uuid4()}",
            request_id=f"report-correction-{uuid4()}",
        )

    assert report.status == "rejected"
    assert db.get(StudentReportDraft, report.id) is None
    assert db.scalar(select(1)) == 1


@pytest.mark.parametrize(
    "failure",
    (
        "inactive_student",
        "deleted_student",
        "cancelled_enrollment",
        "archived_cohort",
        "inactive_class",
        "inactive_project",
    ),
)
def test_review_rechecks_live_target_eligibility_before_and_inside_the_transaction(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
    failure: str,
) -> None:
    report, _ = _submitted_report(report_access_scenario, report_service)
    if failure == "inactive_student":
        profile = db.get(
            StudentProfile,
            report_access_scenario.people["student"].id,
        )
        assert profile is not None
        profile.status = "inactive"
    elif failure == "deleted_student":
        student = db.get(Person, report_access_scenario.people["student"].id)
        assert student is not None
        student.deleted_at = NOW
    elif failure == "cancelled_enrollment":
        enrollment = db.get(Enrollment, report_access_scenario.enrollment.id)
        assert enrollment is not None
        enrollment.status = "cancelled"
        enrollment.cancelled_at = NOW
    elif failure == "archived_cohort":
        report_access_scenario.cohort.status = "archived"
    elif failure == "inactive_class":
        report_access_scenario.teaching_class.is_active = False
    else:
        project = db.get(GameProject, report_access_scenario.teaching_class.project_id)
        assert project is not None
        project.is_active = False
    db.flush()

    queue = TeachingReportQueryService(db).review_queue(
        account_id=report_access_scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(minutes=1),
        cohort_id=None,
        project_id=None,
        class_id=None,
        page=1,
        page_size=20,
    )
    before_report = (
        report.status,
        report.lock_version,
        report.current_version_id,
        report.published_version_id,
        report.current_window_grant_id,
    )
    before_counts = (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    with pytest.raises(EvaluationResourceNotFound):
        report_service.review(
            report.id,
            TeachingReportReviewInput(
                expected_lock_version=report.lock_version,
                decision="approved",
                reason=None,
            ),
            reviewer_user_id=report_access_scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(minutes=1),
            idempotency_key=f"report-review-{uuid4()}",
            request_id=f"report-review-{uuid4()}",
        )

    assert queue.total == 0
    db.refresh(report)
    assert (
        report.status,
        report.lock_version,
        report.current_version_id,
        report.published_version_id,
        report.current_window_grant_id,
    ) == before_report
    assert (
        db.scalar(select(func.count(StudentReportReview.id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before_counts
    assert db.scalar(select(1)) == 1


def test_correction_edit_submit_and_approval_create_a_continuous_second_version(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, rejected_version, _ = _rejected_report(
        report_access_scenario,
        report_service,
    )
    correction_at = NOW + timedelta(days=1)
    started = report_service.start_correction(
        report.id,
        TeachingReportDraftStartInput(
            expected_lock_version=report.lock_version,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=correction_at,
        idempotency_key=f"report-correction-{uuid4()}",
        request_id=f"report-correction-{uuid4()}",
    )

    edited = report_service.save_draft(
        draft_input(
            report_access_scenario,
            body="已补充当日训练目标、完成率和具体数据。",
            expected_revision=started.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=correction_at + timedelta(minutes=10),
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    assert edited.draft.draft_kind == "correction"
    assert edited.draft.draft_revision == 2

    submitted = report_service.submit(
        report.id,
        TeachingReportSubmitInput(
            expected_lock_version=edited.report.lock_version,
            expected_draft_revision=edited.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=correction_at + timedelta(minutes=20),
        idempotency_key=f"report-submit-{uuid4()}",
        request_id=f"report-submit-{uuid4()}",
    )

    assert submitted.report.status == "pending_review"
    assert submitted.version.version == 2
    assert submitted.version.source == "correction"
    assert submitted.version.previous_version_id == rejected_version.id
    assert submitted.version.body == "已补充当日训练目标、完成率和具体数据。"
    persisted_rejected = db.get(StudentReportVersion, rejected_version.id)
    assert persisted_rejected is not None
    assert persisted_rejected.body == rejected_version.body
    approved = report_service.review(
        report.id,
        TeachingReportReviewInput(
            expected_lock_version=submitted.report.lock_version,
            decision="approved",
            reason=None,
        ),
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=correction_at + timedelta(hours=1),
        idempotency_key=f"report-review-{uuid4()}",
        request_id=f"report-review-{uuid4()}",
    )
    assert approved.report.published_version_id == submitted.version.id
    assert approved.report.status == "published"


def test_reviewer_reopens_an_expired_correction_for_three_local_days(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, _, _ = _rejected_report(report_access_scenario, report_service)
    assert report.correction_due_at is not None
    reopened_at = report.correction_due_at + timedelta(microseconds=1)
    reason = "教练提交时网络中断，批准重新开放整改窗口。"
    request_id = f"report-reopen-{uuid4()}"

    reopened = report_service.reopen(
        report.id,
        TeachingReportReopenInput(
            expected_lock_version=report.lock_version,
            grant_kind="correction",
            reason=reason,
        ),
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=reopened_at,
        idempotency_key=f"report-reopen-{uuid4()}",
        request_id=request_id,
    )

    assert reopened.report.status == "rejected"
    assert reopened.report.current_window_grant_id == reopened.grant.id
    assert reopened.report.correction_due_at == reopened.grant.expires_at
    assert reopened.report.lock_version == 4
    assert reopened.grant.grant_kind == "correction"
    assert reopened.grant.reason == reason
    assert reopened.grant.opens_at == reopened_at
    assert reopened.grant.expires_at == correction_deadline(reopened_at)
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert reason not in str(audit.event_metadata)

    started = report_service.start_correction(
        report.id,
        TeachingReportDraftStartInput(
            expected_lock_version=reopened.report.lock_version,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=reopened_at + timedelta(minutes=1),
        idempotency_key=f"report-correction-{uuid4()}",
        request_id=f"report-correction-{uuid4()}",
    )
    assert started.report.status == "correction_draft"
    assert db.get(StudentReportWindowGrant, reopened.grant.id) is not None


def test_reopen_replays_the_original_grant_without_extending_the_window(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, _, _ = _rejected_report(report_access_scenario, report_service)
    assert report.correction_due_at is not None
    reopened_at = report.correction_due_at + timedelta(microseconds=1)
    input = TeachingReportReopenInput(
        expected_lock_version=report.lock_version,
        grant_kind="correction",
        reason="网络异常导致错过整改时间。",
    )
    key = f"report-reopen-{uuid4()}"
    first = report_service.reopen(
        report.id,
        input,
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=reopened_at,
        idempotency_key=key,
        request_id=f"report-reopen-{uuid4()}",
    )
    audit_count = db.scalar(select(func.count(AuditEvent.id)))

    replay = report_service.reopen(
        report.id,
        input,
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=reopened_at,
        idempotency_key=key,
        request_id=f"report-reopen-{uuid4()}",
    )

    assert replay.idempotent is True
    assert replay.grant.id == first.grant.id
    assert replay.grant.expires_at == first.grant.expires_at
    assert db.scalar(select(func.count(StudentReportWindowGrant.id))) == 1
    assert db.scalar(select(func.count(AuditEvent.id))) == audit_count


def test_revision_keeps_the_old_publication_until_independent_approval(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, published_version = _published_report(
        report_access_scenario,
        report_service,
    )
    revision_at = NOW + timedelta(hours=2)

    started = report_service.start_revision(
        report.id,
        TeachingReportDraftStartInput(
            expected_lock_version=report.lock_version,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=revision_at,
        idempotency_key=f"report-revision-{uuid4()}",
        request_id=f"report-revision-{uuid4()}",
    )

    assert started.report.status == "published"
    assert started.report.current_version_id == published_version.id
    assert started.report.published_version_id == published_version.id
    assert started.draft.draft_kind == "revision"
    edited = report_service.save_draft(
        draft_input(
            report_access_scenario,
            body="发布后的修订正文，审核前家庭不可见。",
            expected_revision=started.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=revision_at + timedelta(minutes=10),
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    submitted = report_service.submit(
        report.id,
        TeachingReportSubmitInput(
            expected_lock_version=edited.report.lock_version,
            expected_draft_revision=edited.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=revision_at + timedelta(minutes=20),
        idempotency_key=f"report-submit-{uuid4()}",
        request_id=f"report-submit-{uuid4()}",
    )

    assert submitted.report.status == "revision_pending_review"
    assert submitted.version.version == 2
    assert submitted.version.source == "revision"
    assert submitted.version.previous_version_id == published_version.id
    assert submitted.report.current_version_id == submitted.version.id
    assert submitted.report.published_version_id == published_version.id
    before_approval = TeachingReportQueryService(db).published_for_student(
        account_id=report_access_scenario.accounts["student"].id,
        student_person_id=report_access_scenario.people["student"].id,
        occurred_at=revision_at + timedelta(minutes=30),
        page=1,
        page_size=20,
    )
    assert before_approval.items[0].version_id == published_version.id
    assert before_approval.items[0].body == published_version.body

    approved = report_service.review(
        report.id,
        TeachingReportReviewInput(
            expected_lock_version=submitted.report.lock_version,
            decision="approved",
            reason=None,
        ),
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=revision_at + timedelta(hours=1),
        idempotency_key=f"report-review-{uuid4()}",
        request_id=f"report-review-{uuid4()}",
    )
    assert approved.report.status == "published"
    assert approved.report.published_version_id == submitted.version.id
    after_approval = TeachingReportQueryService(db).published_for_student(
        account_id=report_access_scenario.accounts["student"].id,
        student_person_id=report_access_scenario.people["student"].id,
        occurred_at=revision_at + timedelta(hours=2),
        page=1,
        page_size=20,
    )
    assert after_approval.items[0].version_id == submitted.version.id
    assert after_approval.items[0].body == "发布后的修订正文，审核前家庭不可见。"


def test_expired_revision_requires_reasoned_reopen_and_uses_the_grant_window(
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, published_version = _published_report(
        report_access_scenario,
        report_service,
    )
    reopened_at = report.initial_submission_deadline + timedelta(microseconds=1)
    with pytest.raises(TeachingReportWindowClosed):
        report_service.start_revision(
            report.id,
            TeachingReportDraftStartInput(
                expected_lock_version=report.lock_version,
            ),
            actor_user_id=report_access_scenario.accounts["primary"].id,
            occurred_at=reopened_at,
            idempotency_key=f"report-revision-{uuid4()}",
            request_id=f"report-revision-{uuid4()}",
        )

    reopened = report_service.reopen(
        report.id,
        TeachingReportReopenInput(
            expected_lock_version=report.lock_version,
            grant_kind="revision",
            reason="补充营期数据后需要修订已发布日报。",
        ),
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=reopened_at,
        idempotency_key=f"report-reopen-{uuid4()}",
        request_id=f"report-reopen-{uuid4()}",
    )
    started = report_service.start_revision(
        report.id,
        TeachingReportDraftStartInput(
            expected_lock_version=reopened.report.lock_version,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=reopened_at + timedelta(minutes=1),
        idempotency_key=f"report-revision-{uuid4()}",
        request_id=f"report-revision-{uuid4()}",
    )
    edited = report_service.save_draft(
        draft_input(
            report_access_scenario,
            body="窗口外获批后的修订正文。",
            expected_revision=started.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=reopened_at + timedelta(minutes=2),
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    submitted = report_service.submit(
        report.id,
        TeachingReportSubmitInput(
            expected_lock_version=edited.report.lock_version,
            expected_draft_revision=edited.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=reopened_at + timedelta(minutes=3),
        idempotency_key=f"report-submit-{uuid4()}",
        request_id=f"report-submit-{uuid4()}",
    )
    assert submitted.report.status == "revision_pending_review"
    assert submitted.report.published_version_id == published_version.id
    assert submitted.version.source == "revision"


def test_active_revision_grant_cannot_be_extended_with_a_second_reopen(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, _ = _published_report(report_access_scenario, report_service)
    reopened_at = report.initial_submission_deadline + timedelta(microseconds=1)
    first = report_service.reopen(
        report.id,
        TeachingReportReopenInput(
            expected_lock_version=report.lock_version,
            grant_kind="revision",
            reason="首次开放窗口。",
        ),
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=reopened_at,
        idempotency_key=f"report-reopen-{uuid4()}",
        request_id=f"report-reopen-{uuid4()}",
    )

    with pytest.raises(TeachingReportStateConflict):
        report_service.reopen(
            report.id,
            TeachingReportReopenInput(
                expected_lock_version=first.report.lock_version,
                grant_kind="revision",
                reason="不允许覆盖仍有效的首次窗口。",
            ),
            reviewer_user_id=report_access_scenario.accounts["admin"].id,
            occurred_at=reopened_at + timedelta(minutes=1),
            idempotency_key=f"report-reopen-{uuid4()}",
            request_id=f"report-reopen-{uuid4()}",
        )

    assert db.scalar(select(func.count(StudentReportWindowGrant.id))) == 1
    assert first.report.current_window_grant_id == first.grant.id


def test_rejected_revision_correction_preserves_old_publication_until_version_three(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    report, published_version = _published_report(
        report_access_scenario,
        report_service,
    )
    revision_at = NOW + timedelta(hours=2)
    revision = report_service.start_revision(
        report.id,
        TeachingReportDraftStartInput(expected_lock_version=report.lock_version),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=revision_at,
        idempotency_key=f"report-revision-{uuid4()}",
        request_id=f"report-revision-{uuid4()}",
    )
    revision_edit = report_service.save_draft(
        draft_input(
            report_access_scenario,
            body="第二版修订正文。",
            expected_revision=revision.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=revision_at + timedelta(minutes=5),
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    revision_submit = report_service.submit(
        report.id,
        TeachingReportSubmitInput(
            expected_lock_version=revision_edit.report.lock_version,
            expected_draft_revision=revision_edit.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=revision_at + timedelta(minutes=10),
        idempotency_key=f"report-submit-{uuid4()}",
        request_id=f"report-submit-{uuid4()}",
    )
    rejected = report_service.review(
        report.id,
        TeachingReportReviewInput(
            expected_lock_version=revision_submit.report.lock_version,
            decision="rejected",
            reason="修订正文仍缺少数据。",
        ),
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=revision_at + timedelta(minutes=20),
        idempotency_key=f"report-review-{uuid4()}",
        request_id=f"report-review-{uuid4()}",
    )
    assert rejected.report.published_version_id == published_version.id

    correction_at = revision_at + timedelta(days=1)
    correction = report_service.start_correction(
        report.id,
        TeachingReportDraftStartInput(
            expected_lock_version=rejected.report.lock_version,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=correction_at,
        idempotency_key=f"report-correction-{uuid4()}",
        request_id=f"report-correction-{uuid4()}",
    )
    correction_edit = report_service.save_draft(
        draft_input(
            report_access_scenario,
            body="第三版已补齐数据的修订正文。",
            expected_revision=correction.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=correction_at + timedelta(minutes=5),
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    corrected_submit = report_service.submit(
        report.id,
        TeachingReportSubmitInput(
            expected_lock_version=correction_edit.report.lock_version,
            expected_draft_revision=correction_edit.draft.draft_revision,
        ),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=correction_at + timedelta(minutes=10),
        idempotency_key=f"report-submit-{uuid4()}",
        request_id=f"report-submit-{uuid4()}",
    )

    assert corrected_submit.version.version == 3
    assert corrected_submit.version.previous_version_id == revision_submit.version.id
    assert corrected_submit.report.status == "pending_review"
    assert corrected_submit.report.published_version_id == published_version.id
    family_before = TeachingReportQueryService(db).published_for_student(
        account_id=report_access_scenario.accounts["student"].id,
        student_person_id=report_access_scenario.people["student"].id,
        occurred_at=correction_at + timedelta(minutes=15),
        page=1,
        page_size=20,
    )
    assert family_before.items[0].version_id == published_version.id
    approved = report_service.review(
        report.id,
        TeachingReportReviewInput(
            expected_lock_version=corrected_submit.report.lock_version,
            decision="approved",
            reason=None,
        ),
        reviewer_user_id=report_access_scenario.accounts["admin"].id,
        occurred_at=correction_at + timedelta(minutes=20),
        idempotency_key=f"report-review-{uuid4()}",
        request_id=f"report-review-{uuid4()}",
    )
    assert approved.report.published_version_id == corrected_submit.version.id
    assert list(
        db.scalars(
            select(StudentReportVersion.version)
            .where(StudentReportVersion.report_id == report.id)
            .order_by(StudentReportVersion.version)
        )
    ) == [1, 2, 3]


def test_unauthorized_assistant_failure_has_zero_business_audit_or_idempotency_writes(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(StudentReportDraft.report_id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    )

    with pytest.raises(EvaluationResourceNotFound):
        report_service.save_draft(
            draft_input(report_access_scenario),
            actor_user_id=report_access_scenario.accounts["assistant"].id,
            occurred_at=NOW,
            idempotency_key=f"report-draft-{uuid4()}",
            request_id=f"report-draft-{uuid4()}",
        )

    assert (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(StudentReportDraft.report_id))),
        db.scalar(select(func.count(AuditEvent.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
    ) == before


def publish_then_start_revision(
    db: Session,
    *,
    report: StudentReport,
    actor_user_id: UUID,
) -> tuple[StudentReportVersion, StudentReportVersion]:
    published = StudentReportVersion(
        report_id=report.id,
        version=1,
        source="initial",
        body="家庭可见的已发布日报",
        dimension_values={},
        submitted_by_user_id=actor_user_id,
        submitted_at=NOW,
    )
    db.add(published)
    db.flush()
    report.status = "published"
    report.current_version_id = published.id
    report.published_version_id = published.id
    report.submitted_at = NOW
    report.published_at = NOW
    db.flush()
    revision = StudentReportVersion(
        report_id=report.id,
        version=2,
        source="revision",
        body="仍在审核、家庭不可见的修订正文",
        dimension_values={},
        submitted_by_user_id=actor_user_id,
        submitted_at=NOW,
        previous_version_id=published.id,
    )
    db.add(revision)
    db.flush()
    report.status = "revision_pending_review"
    report.current_version_id = revision.id
    report.submitted_at = NOW
    db.flush()
    return published, revision


def test_author_workspace_returns_its_draft_without_exposing_it_to_other_staff(
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    created = report_service.save_draft(
        draft_input(report_access_scenario),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    query = TeachingReportQueryService(report_access_scenario.db)

    workspace = query.author_workspace(
        account_id=report_access_scenario.accounts["primary"].id,
        enrollment_id=report_access_scenario.enrollment.id,
        occurred_at=NOW,
    )

    item = next(item for item in workspace if item.slot.report_id == created.report.id)
    assert item.draft_body == created.draft.body
    assert item.draft_revision == 1
    with pytest.raises(EvaluationResourceNotFound):
        query.author_workspace(
            account_id=report_access_scenario.accounts["assistant"].id,
            enrollment_id=report_access_scenario.enrollment.id,
            occurred_at=NOW,
        )


@pytest.mark.parametrize("viewer", ["student", "guardian"])
def test_family_projection_starts_from_the_published_pointer_only(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
    viewer: str,
) -> None:
    created = report_service.save_draft(
        draft_input(report_access_scenario, body="未发布草稿绝不能给家庭"),
        actor_user_id=report_access_scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"report-draft-{uuid4()}",
        request_id=f"report-draft-{uuid4()}",
    )
    published, revision = publish_then_start_revision(
        db,
        report=created.report,
        actor_user_id=report_access_scenario.accounts["primary"].id,
    )

    page = TeachingReportQueryService(db).published_for_student(
        account_id=report_access_scenario.accounts[viewer].id,
        student_person_id=report_access_scenario.people["student"].id,
        occurred_at=NOW,
        page=1,
        page_size=20,
    )

    assert page.total == 1
    assert len(page.items) == 1
    item = page.items[0]
    assert item.report_id == created.report.id
    assert item.version_id == published.id
    assert item.body == published.body
    assert item.body != revision.body
    assert "未发布草稿" not in item.body
    assert not hasattr(item, "review_reason")
    assert not hasattr(item, "author_user_id")


def test_guardian_relationship_termination_revokes_the_next_published_query(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    query = TeachingReportQueryService(db)
    assert query.published_for_student(
        account_id=report_access_scenario.accounts["guardian"].id,
        student_person_id=report_access_scenario.people["student"].id,
        occurred_at=NOW,
        page=1,
        page_size=20,
    ).items == ()
    relationship = report_access_scenario.db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id
            == report_access_scenario.people["guardian"].id,
            GuardianRelationship.student_person_id
            == report_access_scenario.people["student"].id,
        )
    )
    assert relationship is not None
    relationship.ended_at = NOW
    relationship.ended_by_user_id = report_access_scenario.accounts["admin"].id
    relationship.end_reason = "测试解除关系"
    db.flush()

    with pytest.raises(EvaluationResourceNotFound):
        query.published_for_student(
            account_id=report_access_scenario.accounts["guardian"].id,
            student_person_id=report_access_scenario.people["student"].id,
            occurred_at=NOW + timedelta(seconds=1),
            page=1,
            page_size=20,
        )
