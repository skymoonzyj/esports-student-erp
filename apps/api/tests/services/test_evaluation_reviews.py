from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    EvaluationResultInvalid,
    EvaluationSelfReviewForbidden,
    EvaluationTaskStateConflict,
    EvaluationTaskVersionConflict,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.review_models import (
    SystemEvaluationManualDraft,
    SystemEvaluationResultVersion,
)
from app.evaluations.review_schemas import (
    EvaluationArchiveInput,
    EvaluationReopenInput,
    EvaluationReviewInput,
)
from app.evaluations.review_service import EvaluationReviewService
from app.evaluations.task_models import (
    EvaluationIdempotencyKey,
    SystemEvaluationEvent,
    SystemEvaluationTask,
    SystemEvaluationTaskMedia,
)
from tests.services.test_evaluation_processing import _create_queued_task
from tests.services.test_evaluation_queries import EvaluationQueryScenario
from tests.services.test_evaluation_uploads import TaskCreationScenario

pytest_plugins = ("tests.services.test_evaluation_queries",)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
IDEMPOTENCY_KEY = b"review-service-idempotency-key-32-bytes-minimum"


def _service(db: Session) -> EvaluationReviewService:
    return EvaluationReviewService(
        db,
        idempotency_service=EvaluationIdempotencyService(
            db,
            digest_key=IDEMPOTENCY_KEY,
        ),
    )


def test_approve_publishes_atomically_and_replay_is_write_free(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        approved = service.review(
            scenario.pending_task_id,
            EvaluationReviewInput(
                expected_lock_version=1,
                decision="approved",
            ),
            reviewer_user_id=scenario.base.admin_user_id,
            idempotency_key="review-approve-1",
            occurred_at=NOW,
            request_id="evaluation-review-approve",
        )
        counts = (
            db.scalar(
                select(func.count(SystemEvaluationEvent.id)).where(
                    SystemEvaluationEvent.task_id == scenario.pending_task_id
                )
            ),
            db.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.pending_task_id
                )
            ),
        )
        replay = service.review(
            scenario.pending_task_id,
            EvaluationReviewInput(
                expected_lock_version=1,
                decision="approved",
            ),
            reviewer_user_id=scenario.base.admin_user_id,
            idempotency_key="review-approve-1",
            occurred_at=NOW + timedelta(seconds=1),
            request_id="evaluation-review-approve-replay",
        )
        assert replay == approved
        assert counts == (
            db.scalar(
                select(func.count(SystemEvaluationEvent.id)).where(
                    SystemEvaluationEvent.task_id == scenario.pending_task_id
                )
            ),
            db.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.pending_task_id
                )
            ),
        )

    with Session(scenario.base.engine) as observer:
        task = observer.get(SystemEvaluationTask, scenario.pending_task_id)
        result = observer.get(SystemEvaluationResultVersion, scenario.pending_result_id)
        assert task is not None and task.status == "published"
        assert task.current_result_version_id == scenario.pending_result_id
        assert task.published_result_version_id == scenario.pending_result_id
        assert task.lock_version == 2
        assert result is not None and result.review_decision == "approved"
        assert result.reviewed_by_user_id == scenario.base.admin_user_id
        assert result.reviewed_at == NOW
        assert result.published_at == NOW


def test_reject_requires_a_bounded_reason_and_keeps_it_out_of_audit_metadata(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        with pytest.raises(EvaluationResultInvalid):
            service.review(
                scenario.pending_task_id,
                EvaluationReviewInput(
                    expected_lock_version=1,
                    decision="rejected",
                    rejection_reason=" ",
                ),
                reviewer_user_id=scenario.base.admin_user_id,
                idempotency_key="review-reject-empty",
                occurred_at=NOW,
                request_id="evaluation-review-reject-empty",
            )
        rejected = service.review(
            scenario.pending_task_id,
            EvaluationReviewInput(
                expected_lock_version=1,
                decision="rejected",
                rejection_reason="关键数据需要重新核对",
            ),
            reviewer_user_id=scenario.base.admin_user_id,
            idempotency_key="review-reject-1",
            occurred_at=NOW,
            request_id="evaluation-review-reject",
        )
        assert rejected.review_decision == "rejected"

    with Session(scenario.base.engine) as observer:
        result = observer.get(SystemEvaluationResultVersion, scenario.pending_result_id)
        task = observer.get(SystemEvaluationTask, scenario.pending_task_id)
        assert result is not None
        assert result.rejection_reason == "关键数据需要重新核对"
        assert task is not None and task.status == "rejected"
        business_event = observer.scalar(
            select(SystemEvaluationEvent).where(
                SystemEvaluationEvent.task_id == scenario.pending_task_id,
                SystemEvaluationEvent.event_type == "result_rejected",
            )
        )
        assert business_event is not None
        assert business_event.reason == "关键数据需要重新核对"
        audits = list(
            observer.scalars(
                select(AuditEvent).where(
                    AuditEvent.resource_id == scenario.pending_task_id
                )
            )
        )
        assert audits
        assert "关键数据需要重新核对" not in repr(
            [audit.event_metadata for audit in audits]
        )
        assert all("reason" not in audit.event_metadata for audit in audits)


def test_administrator_cannot_review_a_result_they_authored(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as setup, setup.begin():
        task = setup.get(SystemEvaluationTask, scenario.pending_task_id)
        assert task is not None
        result = setup.get(SystemEvaluationResultVersion, scenario.pending_result_id)
        assert result is not None
        # The result table is immutable, so create a separate task/version authored by admin.
        self_task = SystemEvaluationTask(
            public_number=f"EV-{uuid4().hex[:20].upper()}",
            upload_session_id=task.upload_session_id,
            created_by_user_id=task.created_by_user_id,
            student_person_id=task.student_person_id,
            enrollment_id=task.enrollment_id,
            cohort_id=task.cohort_id,
            project_id=task.project_id,
            class_id=task.class_id,
            analysis_profile_id=task.analysis_profile_id,
            analysis_profile_version=task.analysis_profile_version,
            analysis_config_snapshot=dict(task.analysis_config_snapshot),
            upload_window_start=task.upload_window_start,
            upload_window_end=task.upload_window_end,
            status="needs_review",
        )
        # A task needs its own unique upload session. Reuse is forbidden, so clone it.
        from app.evaluations.task_models import SystemEvaluationUploadSession

        upload = SystemEvaluationUploadSession(
            created_by_user_id=task.created_by_user_id,
            project_id=task.project_id,
            student_person_id=task.student_person_id,
            enrollment_id=task.enrollment_id,
            status="committed",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            committed_at=datetime.now(UTC),
        )
        setup.add(upload)
        setup.flush()
        self_task.upload_session_id = upload.id
        setup.add(self_task)
        setup.flush()
        self_result = SystemEvaluationResultVersion(
            task_id=self_task.id,
            version=1,
            source="manual",
            fields=dict(result.fields),
            confidences=dict(result.confidences),
            warnings=list(result.warnings),
            components=list(result.components),
            missing_fields=list(result.missing_fields),
            score=result.score,
            grade=result.grade,
            advice=result.advice,
            result_schema_version=result.result_schema_version,
            created_by_user_id=scenario.base.admin_user_id,
            review_decision="pending",
        )
        setup.add(self_result)
        setup.flush()
        self_task.current_result_version_id = self_result.id
        setup.flush()
        task_id = self_task.id

    with Session(scenario.base.engine) as db:
        before = (
            db.scalar(
                select(func.count(SystemEvaluationEvent.id)).where(
                    SystemEvaluationEvent.task_id == task_id
                )
            ),
            db.scalar(
                select(func.count(EvaluationIdempotencyKey.id)).where(
                    EvaluationIdempotencyKey.resource_id == task_id
                )
            ),
        )
        with pytest.raises(EvaluationSelfReviewForbidden):
            _service(db).review(
                task_id,
                EvaluationReviewInput(
                    expected_lock_version=1,
                    decision="approved",
                ),
                reviewer_user_id=scenario.base.admin_user_id,
                idempotency_key="review-self-admin",
                occurred_at=NOW,
                request_id="evaluation-review-self-admin",
            )
        assert db.scalar(select(1)) == 1
        assert before == (
            db.scalar(
                select(func.count(SystemEvaluationEvent.id)).where(
                    SystemEvaluationEvent.task_id == task_id
                )
            ),
            db.scalar(
                select(func.count(EvaluationIdempotencyKey.id)).where(
                    EvaluationIdempotencyKey.resource_id == task_id
                )
            ),
        )


def test_review_version_state_and_payload_conflicts_leave_session_usable(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        with pytest.raises(EvaluationTaskVersionConflict):
            service.review(
                scenario.pending_task_id,
                EvaluationReviewInput(
                    expected_lock_version=999,
                    decision="approved",
                ),
                reviewer_user_id=scenario.base.admin_user_id,
                idempotency_key="review-version-conflict",
                occurred_at=NOW,
                request_id="evaluation-review-version-conflict",
            )
        assert db.scalar(select(1)) == 1

        service.review(
            scenario.pending_task_id,
            EvaluationReviewInput(
                expected_lock_version=1,
                decision="approved",
            ),
            reviewer_user_id=scenario.base.admin_user_id,
            idempotency_key="review-state-base",
            occurred_at=NOW,
            request_id="evaluation-review-state-base",
        )
        with pytest.raises(EvaluationIdempotencyConflict):
            service.review(
                scenario.pending_task_id,
                EvaluationReviewInput(
                    expected_lock_version=1,
                    decision="rejected",
                    rejection_reason="改变决定",
                ),
                reviewer_user_id=scenario.base.admin_user_id,
                idempotency_key="review-state-base",
                occurred_at=NOW + timedelta(seconds=1),
                request_id="evaluation-review-payload-conflict",
            )
        assert db.scalar(select(1)) == 1
        with pytest.raises(EvaluationTaskStateConflict):
            service.review(
                scenario.pending_task_id,
                EvaluationReviewInput(
                    expected_lock_version=2,
                    decision="approved",
                ),
                reviewer_user_id=scenario.base.admin_user_id,
                idempotency_key="review-state-new",
                occurred_at=NOW + timedelta(seconds=1),
                request_id="evaluation-review-state-conflict",
            )
        assert db.scalar(select(1)) == 1


def test_review_replay_reauthorizes_before_idempotency(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    input = EvaluationReviewInput(expected_lock_version=1, decision="approved")
    with Session(scenario.base.engine) as db:
        _service(db).review(
            scenario.pending_task_id,
            input,
            reviewer_user_id=scenario.base.admin_user_id,
            idempotency_key="review-reauthorize",
            occurred_at=NOW,
            request_id="evaluation-review-reauthorize",
        )

    with Session(scenario.base.engine) as setup, setup.begin():
        reviewer = setup.get(UserAccount, scenario.base.admin_user_id)
        assert reviewer is not None
        reviewer.status = "disabled"

    try:
        with Session(scenario.base.engine) as db:
            before = (
                db.scalar(
                    select(func.count(SystemEvaluationEvent.id)).where(
                        SystemEvaluationEvent.task_id == scenario.pending_task_id
                    )
                ),
                db.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.resource_id == scenario.pending_task_id
                    )
                ),
            )
            with pytest.raises(EvaluationOperationDenied):
                _service(db).review(
                    scenario.pending_task_id,
                    input,
                    reviewer_user_id=scenario.base.admin_user_id,
                    idempotency_key="review-reauthorize",
                    occurred_at=NOW + timedelta(seconds=1),
                    request_id="evaluation-review-reauthorize-replay",
                )
            assert db.scalar(select(1)) == 1
            assert before == (
                db.scalar(
                    select(func.count(SystemEvaluationEvent.id)).where(
                        SystemEvaluationEvent.task_id == scenario.pending_task_id
                    )
                ),
                db.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.resource_id == scenario.pending_task_id
                    )
                ),
            )
    finally:
        with Session(scenario.base.engine) as cleanup, cleanup.begin():
            reviewer = cleanup.get(UserAccount, scenario.base.admin_user_id)
            assert reviewer is not None
            reviewer.status = "active"


def test_review_audit_failure_rolls_back_every_write_and_keeps_session_usable(
    evaluation_query_scenario: EvaluationQueryScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = evaluation_query_scenario

    def fail_audit(_self: AuditService, **_kwargs: object) -> None:
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with Session(scenario.base.engine) as db:
        before = (
            db.scalar(
                select(func.count(SystemEvaluationEvent.id)).where(
                    SystemEvaluationEvent.task_id == scenario.pending_task_id
                )
            ),
            db.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.pending_task_id
                )
            ),
            db.scalar(
                select(func.count(EvaluationIdempotencyKey.id)).where(
                    EvaluationIdempotencyKey.scope == "evaluation.result.review"
                )
            ),
        )
        with pytest.raises(RuntimeError, match="audit storage unavailable"):
            _service(db).review(
                scenario.pending_task_id,
                EvaluationReviewInput(
                    expected_lock_version=1,
                    decision="approved",
                ),
                reviewer_user_id=scenario.base.admin_user_id,
                idempotency_key="review-audit-rollback",
                occurred_at=NOW,
                request_id="evaluation-review-audit-rollback",
            )
        assert db.scalar(select(1)) == 1

    with Session(scenario.base.engine) as observer:
        task = observer.get(SystemEvaluationTask, scenario.pending_task_id)
        result = observer.get(
            SystemEvaluationResultVersion,
            scenario.pending_result_id,
        )
        assert task is not None
        assert task.status == "needs_review"
        assert task.lock_version == 1
        assert task.published_result_version_id is None
        assert result is not None
        assert result.review_decision == "pending"
        assert result.reviewed_by_user_id is None
        assert before == (
            observer.scalar(
                select(func.count(SystemEvaluationEvent.id)).where(
                    SystemEvaluationEvent.task_id == scenario.pending_task_id
                )
            ),
            observer.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.pending_task_id
                )
            ),
            observer.scalar(
                select(func.count(EvaluationIdempotencyKey.id)).where(
                    EvaluationIdempotencyKey.scope == "evaluation.result.review"
                )
            ),
        )


def test_review_capability_gate_and_private_missing_target_are_distinct(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    input = EvaluationReviewInput(expected_lock_version=1, decision="approved")
    with Session(scenario.base.engine) as db:
        with pytest.raises(EvaluationOperationDenied):
            _service(db).review(
                scenario.pending_task_id,
                input,
                reviewer_user_id=scenario.base.actor_user_id,
                idempotency_key="review-no-capability",
                occurred_at=NOW,
                request_id="evaluation-review-no-capability",
            )
        assert db.scalar(select(1)) == 1

        with pytest.raises(EvaluationResourceNotFound):
            _service(db).review(
                uuid4(),
                input,
                reviewer_user_id=scenario.base.admin_user_id,
                idempotency_key="review-private-missing",
                occurred_at=NOW,
                request_id="evaluation-review-private-missing",
            )
        assert db.scalar(select(1)) == 1


def test_archive_and_reopen_correction_preserve_results_media_and_business_reasons(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        service.review(
            scenario.pending_task_id,
            EvaluationReviewInput(
                expected_lock_version=1,
                decision="rejected",
                rejection_reason="先核对数据",
            ),
            reviewer_user_id=scenario.base.admin_user_id,
            idempotency_key="archive-reopen-review",
            occurred_at=NOW,
            request_id="evaluation-archive-reopen-review",
        )
        before = (
            db.scalar(
                select(func.count(SystemEvaluationResultVersion.id)).where(
                    SystemEvaluationResultVersion.task_id == scenario.pending_task_id
                )
            ),
            db.scalar(
                select(func.count(SystemEvaluationTaskMedia.id)).where(
                    SystemEvaluationTaskMedia.task_id == scenario.pending_task_id
                )
            ),
        )
        archived = service.archive(
            scenario.pending_task_id,
            EvaluationArchiveInput(
                expected_lock_version=2,
                reason="暂时结束本次处理",
            ),
            actor_user_id=scenario.base.admin_user_id,
            idempotency_key="task-archive-1",
            occurred_at=NOW + timedelta(minutes=1),
            request_id="evaluation-task-archive",
        )
        replay = service.archive(
            scenario.pending_task_id,
            EvaluationArchiveInput(
                expected_lock_version=2,
                reason="暂时结束本次处理",
            ),
            actor_user_id=scenario.base.admin_user_id,
            idempotency_key="task-archive-1",
            occurred_at=NOW + timedelta(minutes=2),
            request_id="evaluation-task-archive-replay",
        )
        assert replay == archived
        assert archived.task_status == "archived"
        assert archived.task_version == 3
        with pytest.raises(EvaluationIdempotencyConflict):
            service.archive(
                scenario.pending_task_id,
                EvaluationArchiveInput(
                    expected_lock_version=2,
                    reason="尝试改变归档原因",
                ),
                actor_user_id=scenario.base.admin_user_id,
                idempotency_key="task-archive-1",
                occurred_at=NOW + timedelta(minutes=2),
                request_id="evaluation-task-archive-conflict",
            )
        assert db.scalar(select(1)) == 1

        reopened = service.reopen(
            scenario.pending_task_id,
            EvaluationReopenInput(
                expected_lock_version=3,
                resume="correction",
                reason="重新进入人工整改",
            ),
            actor_user_id=scenario.base.admin_user_id,
            idempotency_key="task-reopen-correction-1",
            occurred_at=NOW + timedelta(minutes=3),
            request_id="evaluation-task-reopen-correction",
        )
        assert reopened.task_status == "correction_pending"
        assert reopened.task_version == 4

    with Session(scenario.base.engine) as observer:
        task = observer.get(SystemEvaluationTask, scenario.pending_task_id)
        result = observer.get(
            SystemEvaluationResultVersion,
            scenario.pending_result_id,
        )
        draft = observer.get(SystemEvaluationManualDraft, scenario.pending_task_id)
        assert task is not None and task.status == "correction_pending"
        assert task.current_result_version_id == scenario.pending_result_id
        assert result is not None and result.review_decision == "rejected"
        assert draft is not None and draft.last_edited_by_user_id == scenario.base.admin_user_id
        assert draft.fields == result.fields
        assert before == (
            observer.scalar(
                select(func.count(SystemEvaluationResultVersion.id)).where(
                    SystemEvaluationResultVersion.task_id == scenario.pending_task_id
                )
            ),
            observer.scalar(
                select(func.count(SystemEvaluationTaskMedia.id)).where(
                    SystemEvaluationTaskMedia.task_id == scenario.pending_task_id
                )
            ),
        )
        business_reasons = set(
            observer.scalars(
                select(SystemEvaluationEvent.reason).where(
                    SystemEvaluationEvent.task_id == scenario.pending_task_id,
                    SystemEvaluationEvent.event_type.in_(
                        {"task_archived", "task_reopened"}
                    ),
                )
            )
        )
        assert business_reasons == {
            "暂时结束本次处理",
            "重新进入人工整改",
        }
        audit_metadata = repr(
            list(
                observer.scalars(
                    select(AuditEvent.event_metadata).where(
                        AuditEvent.resource_id == scenario.pending_task_id
                    )
                )
            )
        )
        assert "暂时结束本次处理" not in audit_metadata
        assert "重新进入人工整改" not in audit_metadata
        assert "reason" not in audit_metadata


def test_archive_failed_task_can_reopen_into_the_automatic_queue(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = datetime.now(UTC).replace(microsecond=0)
    task_id = _create_queued_task(scenario, occurred_at=occurred_at)
    with Session(scenario.engine) as setup, setup.begin():
        task = setup.get(SystemEvaluationTask, task_id)
        assert task is not None
        task.status = "failed"
        task.failure_code = "temporary_service"

    with Session(scenario.engine) as db:
        service = _service(db)
        archived = service.archive(
            task_id,
            EvaluationArchiveInput(expected_lock_version=1, reason="等待重试"),
            actor_user_id=scenario.admin_user_id,
            idempotency_key="failed-archive-1",
            occurred_at=occurred_at,
            request_id="evaluation-failed-archive",
        )
        reopened = service.reopen(
            task_id,
            EvaluationReopenInput(
                expected_lock_version=archived.task_version,
                resume="automatic",
                reason="恢复自动处理",
            ),
            actor_user_id=scenario.admin_user_id,
            idempotency_key="failed-reopen-automatic-1",
            occurred_at=occurred_at + timedelta(minutes=1),
            request_id="evaluation-failed-reopen-automatic",
        )
        assert reopened.task_status == "queued"

    with Session(scenario.engine) as observer:
        task = observer.get(SystemEvaluationTask, task_id)
        assert task is not None and task.status == "queued"
        assert task.available_at == occurred_at + timedelta(minutes=1)
        assert task.failure_code is None


def test_archive_audit_failure_rolls_back_state_event_and_idempotency(
    evaluation_query_scenario: EvaluationQueryScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        _service(db).review(
            scenario.pending_task_id,
            EvaluationReviewInput(
                expected_lock_version=1,
                decision="rejected",
                rejection_reason="先驳回再测试归档事务",
            ),
            reviewer_user_id=scenario.base.admin_user_id,
            idempotency_key="archive-rollback-review",
            occurred_at=NOW,
            request_id="evaluation-archive-rollback-review",
        )

    def fail_audit(_self: AuditService, **_kwargs: object) -> None:
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with Session(scenario.base.engine) as db:
        before = (
            db.scalar(
                select(func.count(SystemEvaluationEvent.id)).where(
                    SystemEvaluationEvent.task_id == scenario.pending_task_id
                )
            ),
            db.scalar(
                select(func.count(EvaluationIdempotencyKey.id)).where(
                    EvaluationIdempotencyKey.scope == "evaluation.task.archive"
                )
            ),
        )
        with pytest.raises(RuntimeError, match="audit storage unavailable"):
            _service(db).archive(
                scenario.pending_task_id,
                EvaluationArchiveInput(
                    expected_lock_version=2,
                    reason="本次归档应整体回滚",
                ),
                actor_user_id=scenario.base.admin_user_id,
                idempotency_key="archive-audit-rollback",
                occurred_at=NOW + timedelta(minutes=1),
                request_id="evaluation-archive-audit-rollback",
            )
        assert db.scalar(select(1)) == 1

    with Session(scenario.base.engine) as observer:
        task = observer.get(SystemEvaluationTask, scenario.pending_task_id)
        assert task is not None and task.status == "rejected"
        assert task.lock_version == 2
        assert task.archived_at is None
        assert task.archived_by_user_id is None
        assert before == (
            observer.scalar(
                select(func.count(SystemEvaluationEvent.id)).where(
                    SystemEvaluationEvent.task_id == scenario.pending_task_id
                )
            ),
            observer.scalar(
                select(func.count(EvaluationIdempotencyKey.id)).where(
                    EvaluationIdempotencyKey.scope == "evaluation.task.archive"
                )
            ),
        )
