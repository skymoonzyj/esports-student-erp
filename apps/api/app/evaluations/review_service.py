from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.evaluations.errors import (
    EvaluationResultInvalid,
    EvaluationSelfReviewForbidden,
    EvaluationTaskStateConflict,
    EvaluationTaskVersionConflict,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.query_service import EvaluationQueryService
from app.evaluations.review_models import (
    SystemEvaluationManualDraft,
    SystemEvaluationResultVersion,
)
from app.evaluations.review_schemas import (
    EvaluationArchiveInput,
    EvaluationReopenInput,
    EvaluationReviewInput,
)
from app.evaluations.task_models import SystemEvaluationEvent, SystemEvaluationTask
from app.evaluations.task_state import require_transition


@dataclass(frozen=True)
class EvaluationReviewed:
    task_id: UUID
    result_id: UUID
    result_version: int
    task_status: str
    task_version: int
    review_decision: str
    reviewed_at: datetime
    published_at: datetime | None


@dataclass(frozen=True)
class EvaluationTaskStateChanged:
    task_id: UUID
    task_status: str
    task_version: int


class EvaluationReviewService:
    def __init__(
        self,
        db: Session,
        *,
        idempotency_service: EvaluationIdempotencyService,
        query_service: EvaluationQueryService | None = None,
    ) -> None:
        self.db = db
        self.idempotency = idempotency_service
        self.query = query_service or EvaluationQueryService(db)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    def _next_sequence(self, task_id: UUID) -> int:
        return int(
            self.db.scalar(
                select(func.coalesce(func.max(SystemEvaluationEvent.sequence), 0) + 1)
                .where(SystemEvaluationEvent.task_id == task_id)
            )
            or 1
        )

    @staticmethod
    def _rejection_reason(input: EvaluationReviewInput) -> str | None:
        reason = input.rejection_reason
        if input.decision == "approved":
            if reason is not None:
                raise EvaluationResultInvalid("evaluation result is invalid")
            return None
        if reason is None or not reason.strip() or len(reason) > 1000:
            raise EvaluationResultInvalid("evaluation result is invalid")
        return reason

    def _locked_result(
        self,
        *,
        task_id: UUID,
        result_id: UUID,
    ) -> SystemEvaluationResultVersion:
        result = self.db.scalar(
            select(SystemEvaluationResultVersion)
            .where(
                SystemEvaluationResultVersion.id == result_id,
                SystemEvaluationResultVersion.task_id == task_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if result is None:
            raise EvaluationResultInvalid("evaluation result is invalid")
        return result

    def _review_replay(
        self,
        *,
        task_id: UUID,
        resource_id: UUID | None,
    ) -> EvaluationReviewed | None:
        if resource_id is None:
            return None
        result = self._locked_result(task_id=task_id, result_id=resource_id)
        if (
            result.review_decision not in {"approved", "rejected"}
            or result.reviewed_at is None
        ):
            raise RuntimeError("evaluation review result is invalid")
        event_type = (
            "result_approved"
            if result.review_decision == "approved"
            else "result_rejected"
        )
        event = self.db.scalar(
            select(SystemEvaluationEvent)
            .where(
                SystemEvaluationEvent.task_id == task_id,
                SystemEvaluationEvent.result_version_id == result.id,
                SystemEvaluationEvent.event_type == event_type,
            )
            .order_by(SystemEvaluationEvent.sequence)
            .limit(1)
        )
        if event is None or event.to_status not in {"published", "rejected"}:
            raise RuntimeError("evaluation review event is missing")
        task_version = event.event_metadata.get("task_version")
        if not isinstance(task_version, int) or isinstance(task_version, bool):
            raise TypeError("evaluation review event is invalid")
        return EvaluationReviewed(
            task_id=task_id,
            result_id=result.id,
            result_version=result.version,
            task_status=event.to_status,
            task_version=task_version,
            review_decision=result.review_decision,
            reviewed_at=result.reviewed_at,
            published_at=result.published_at,
        )

    def _append_audit(
        self,
        task: SystemEvaluationTask,
        result: SystemEvaluationResultVersion,
        *,
        reviewer_user_id: UUID,
        request_id: str,
    ) -> None:
        AuditService(self.db).append(
            actor_user_id=reviewer_user_id,
            action=(
                "evaluation.task.result_approved"
                if result.review_decision == "approved"
                else "evaluation.task.result_rejected"
            ),
            resource_type="system_evaluation_task",
            resource_id=task.id,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata={
                "task_id": str(task.id),
                "result_version_id": str(result.id),
                "result_version": result.version,
                "task_status": task.status,
                "task_version": task.lock_version,
                "review_decision": result.review_decision,
                "result_schema_version": result.result_schema_version,
                "evaluation_action": (
                    "approve" if result.review_decision == "approved" else "reject"
                ),
            },
        )

    def _append_state_audit(
        self,
        task: SystemEvaluationTask,
        *,
        actor_user_id: UUID,
        action: str,
        evaluation_action: str,
        request_id: str,
        result: SystemEvaluationResultVersion | None,
    ) -> None:
        metadata: dict[str, object] = {
            "task_id": str(task.id),
            "task_status": task.status,
            "task_version": task.lock_version,
            "evaluation_action": evaluation_action,
        }
        if result is not None:
            metadata.update(
                {
                    "result_version_id": str(result.id),
                    "result_version": result.version,
                    "review_decision": result.review_decision,
                    "result_schema_version": result.result_schema_version,
                }
            )
        AuditService(self.db).append(
            actor_user_id=actor_user_id,
            action=action,
            resource_type="system_evaluation_task",
            resource_id=task.id,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata=metadata,
        )

    def _state_change_replay(
        self,
        *,
        task_id: UUID,
        resource_id: UUID | None,
        event_type: str,
        expected_status: str | None = None,
    ) -> EvaluationTaskStateChanged | None:
        if resource_id is None:
            return None
        event = self.db.scalar(
            select(SystemEvaluationEvent).where(
                SystemEvaluationEvent.id == resource_id,
                SystemEvaluationEvent.task_id == task_id,
                SystemEvaluationEvent.event_type == event_type,
            )
        )
        if (
            event is None
            or event.to_status is None
            or (expected_status is not None and event.to_status != expected_status)
        ):
            raise RuntimeError("evaluation task state event is invalid")
        task_version = event.event_metadata.get("task_version")
        if not isinstance(task_version, int) or isinstance(task_version, bool):
            raise TypeError("evaluation task state event is invalid")
        return EvaluationTaskStateChanged(
            task_id=task_id,
            task_status=event.to_status,
            task_version=task_version,
        )

    def _current_result(
        self,
        task: SystemEvaluationTask,
    ) -> SystemEvaluationResultVersion | None:
        if task.current_result_version_id is None:
            return None
        return self._locked_result(
            task_id=task.id,
            result_id=task.current_result_version_id,
        )

    def review(
        self,
        task_id: UUID,
        input: EvaluationReviewInput,
        *,
        reviewer_user_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationReviewed:
        self._require_aware(occurred_at)
        digests = self.idempotency.prepare(
            scope="evaluation.result.review",
            actor_user_id=reviewer_user_id,
            key=idempotency_key,
            payload={
                "task_id": str(task_id),
                "expected_lock_version": input.expected_lock_version,
                "decision": input.decision,
                "rejection_reason": input.rejection_reason,
            },
        )
        with write_transaction(self.db):
            task = self.query.require_action_task(
                task_id,
                actor_user_id=reviewer_user_id,
                permission="evaluation_jobs.review",
                occurred_at=occurred_at,
                for_update=True,
            )
            replay = self._review_replay(
                task_id=task.id,
                resource_id=self.idempotency.existing_resource_id(
                    digests,
                    occurred_at=occurred_at,
                ),
            )
            if replay is not None:
                return replay
            if task.status != "needs_review":
                raise EvaluationTaskStateConflict("evaluation task state changed")
            if task.lock_version != input.expected_lock_version:
                raise EvaluationTaskVersionConflict("evaluation task version changed")
            if task.current_result_version_id is None:
                raise EvaluationResultInvalid("evaluation result is invalid")
            result = self._locked_result(
                task_id=task.id,
                result_id=task.current_result_version_id,
            )
            if result.created_by_user_id == reviewer_user_id:
                raise EvaluationSelfReviewForbidden("evaluation review is not allowed")
            if result.review_decision != "pending":
                raise EvaluationTaskStateConflict("evaluation task state changed")
            rejection_reason = self._rejection_reason(input)

            target_status = (
                "published" if input.decision == "approved" else "rejected"
            )
            require_transition("needs_review", target_status)
            result.review_decision = input.decision
            result.reviewed_by_user_id = reviewer_user_id
            result.reviewed_at = occurred_at
            result.rejection_reason = rejection_reason
            result.published_at = occurred_at if input.decision == "approved" else None

            task.status = target_status
            task.reviewed_at = occurred_at
            task.reviewed_by_user_id = reviewer_user_id
            if input.decision == "approved":
                task.published_result_version_id = result.id
                task.published_at = occurred_at
                task.published_by_user_id = reviewer_user_id
            task.lock_version += 1
            self.db.add(
                SystemEvaluationEvent(
                    task_id=task.id,
                    sequence=self._next_sequence(task.id),
                    event_type=(
                        "result_approved"
                        if input.decision == "approved"
                        else "result_rejected"
                    ),
                    from_status="needs_review",
                    to_status=target_status,
                    result_version_id=result.id,
                    actor_user_id=reviewer_user_id,
                    reason=rejection_reason,
                    event_metadata={
                        "task_version": task.lock_version,
                        "result_version": result.version,
                    },
                    occurred_at=occurred_at,
                )
            )
            self.idempotency.record(
                digests,
                resource_id=result.id,
                occurred_at=occurred_at,
            )
            self._append_audit(
                task,
                result,
                reviewer_user_id=reviewer_user_id,
                request_id=request_id,
            )
            self.db.flush()
            reviewed = EvaluationReviewed(
                task_id=task.id,
                result_id=result.id,
                result_version=result.version,
                task_status=task.status,
                task_version=task.lock_version,
                review_decision=result.review_decision,
                reviewed_at=occurred_at,
                published_at=result.published_at,
            )
        return reviewed

    def archive(
        self,
        task_id: UUID,
        input: EvaluationArchiveInput,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationTaskStateChanged:
        self._require_aware(occurred_at)
        digests = self.idempotency.prepare(
            scope="evaluation.task.archive",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "task_id": str(task_id),
                "expected_lock_version": input.expected_lock_version,
                "reason": input.reason,
            },
        )
        with write_transaction(self.db):
            task = self.query.require_action_task(
                task_id,
                actor_user_id=actor_user_id,
                permission="evaluation_jobs.match",
                occurred_at=occurred_at,
                for_update=True,
            )
            replay = self._state_change_replay(
                task_id=task.id,
                resource_id=self.idempotency.existing_resource_id(
                    digests,
                    occurred_at=occurred_at,
                ),
                event_type="task_archived",
                expected_status="archived",
            )
            if replay is not None:
                return replay
            if task.status not in {"failed", "rejected"}:
                raise EvaluationTaskStateConflict("evaluation task state changed")
            if task.lock_version != input.expected_lock_version:
                raise EvaluationTaskVersionConflict("evaluation task version changed")
            source_status = task.status
            require_transition(source_status, "archived")
            result = self._current_result(task)
            task.status = "archived"
            task.archived_at = occurred_at
            task.archived_by_user_id = actor_user_id
            task.available_at = None
            task.lock_version += 1
            event = SystemEvaluationEvent(
                task_id=task.id,
                sequence=self._next_sequence(task.id),
                event_type="task_archived",
                from_status=source_status,
                to_status="archived",
                result_version_id=result.id if result is not None else None,
                actor_user_id=actor_user_id,
                reason=input.reason,
                event_metadata={"task_version": task.lock_version},
                occurred_at=occurred_at,
            )
            self.db.add(event)
            self.db.flush()
            self.idempotency.record(
                digests,
                resource_id=event.id,
                occurred_at=occurred_at,
            )
            self._append_state_audit(
                task,
                actor_user_id=actor_user_id,
                action="evaluation.task.archived",
                evaluation_action="archive",
                request_id=request_id,
                result=result,
            )
            self.db.flush()
            changed = EvaluationTaskStateChanged(
                task_id=task.id,
                task_status=task.status,
                task_version=task.lock_version,
            )
        return changed

    def reopen(
        self,
        task_id: UUID,
        input: EvaluationReopenInput,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationTaskStateChanged:
        self._require_aware(occurred_at)
        target_status = "queued" if input.resume == "automatic" else "correction_pending"
        digests = self.idempotency.prepare(
            scope="evaluation.task.reopen",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "task_id": str(task_id),
                "expected_lock_version": input.expected_lock_version,
                "resume": input.resume,
                "reason": input.reason,
            },
        )
        with write_transaction(self.db):
            task = self.query.require_action_task(
                task_id,
                actor_user_id=actor_user_id,
                permission="evaluation_jobs.match",
                occurred_at=occurred_at,
                for_update=True,
            )
            replay = self._state_change_replay(
                task_id=task.id,
                resource_id=self.idempotency.existing_resource_id(
                    digests,
                    occurred_at=occurred_at,
                ),
                event_type="task_reopened",
                expected_status=target_status,
            )
            if replay is not None:
                return replay
            if task.status != "archived":
                raise EvaluationTaskStateConflict("evaluation task state changed")
            if task.lock_version != input.expected_lock_version:
                raise EvaluationTaskVersionConflict("evaluation task version changed")
            require_transition("archived", target_status)
            result = self._current_result(task)
            if input.resume == "automatic":
                if (
                    task.analysis_config_snapshot.get("mode") != "automatic"
                    or task.current_attempt >= 3
                ):
                    raise EvaluationResultInvalid("evaluation result is invalid")
                task.available_at = occurred_at
            else:
                if result is None or result.review_decision != "rejected":
                    raise EvaluationResultInvalid("evaluation result is invalid")
                existing_draft = self.db.scalar(
                    select(SystemEvaluationManualDraft)
                    .where(SystemEvaluationManualDraft.task_id == task.id)
                    .with_for_update()
                )
                if existing_draft is not None:
                    raise EvaluationTaskStateConflict("evaluation task state changed")
                self.db.add(
                    SystemEvaluationManualDraft(
                        task_id=task.id,
                        last_edited_by_user_id=actor_user_id,
                        fields=dict(result.fields),
                        confidences=dict(result.confidences),
                        warnings=[dict(item) for item in result.warnings],
                        components=[dict(item) for item in result.components],
                        missing_fields=list(result.missing_fields),
                        score=result.score,
                        grade=result.grade,
                        advice=result.advice,
                        lock_version=1,
                        updated_at=occurred_at,
                    )
                )
                task.available_at = None
            task.status = target_status
            task.failure_code = None
            task.manual_queue_reason = None
            task.lock_version += 1
            event = SystemEvaluationEvent(
                task_id=task.id,
                sequence=self._next_sequence(task.id),
                event_type="task_reopened",
                from_status="archived",
                to_status=target_status,
                result_version_id=result.id if result is not None else None,
                actor_user_id=actor_user_id,
                reason=input.reason,
                event_metadata={"task_version": task.lock_version},
                occurred_at=occurred_at,
            )
            self.db.add(event)
            self.db.flush()
            self.idempotency.record(
                digests,
                resource_id=event.id,
                occurred_at=occurred_at,
            )
            self._append_state_audit(
                task,
                actor_user_id=actor_user_id,
                action="evaluation.task.reopened",
                evaluation_action="reopen",
                request_id=request_id,
                result=result,
            )
            self.db.flush()
            changed = EvaluationTaskStateChanged(
                task_id=task.id,
                task_status=task.status,
                task_version=task.lock_version,
            )
        return changed
