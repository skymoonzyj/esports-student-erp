from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.evaluations.errors import (
    EvaluationResultInvalid,
    EvaluationTaskStateConflict,
    EvaluationTaskVersionConflict,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.query_service import EvaluationQueryService
from app.evaluations.result_validation import validate_result_content
from app.evaluations.review_models import (
    SystemEvaluationManualDraft,
    SystemEvaluationResultVersion,
)
from app.evaluations.review_schemas import (
    EvaluationManualDraftInput,
    EvaluationManualSubmitInput,
    EvaluationResultRevisionInput,
)
from app.evaluations.task_models import SystemEvaluationEvent, SystemEvaluationTask
from app.evaluations.task_state import require_transition


@dataclass(frozen=True)
class EvaluationDraftSaved:
    task_id: UUID
    task_status: str
    task_version: int
    draft_revision: int
    is_complete: bool
    missing_required_fields: tuple[str, ...]
    updated_at: datetime


@dataclass(frozen=True)
class EvaluationResultSubmitted:
    task_id: UUID
    result_id: UUID
    result_version: int
    task_status: str
    task_version: int


@dataclass(frozen=True)
class EvaluationRevisionStarted:
    task_id: UUID
    task_status: str
    task_version: int
    draft_revision: int | None


class EvaluationResultService:
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

    def _next_result_version(self, task_id: UUID) -> int:
        return int(
            self.db.scalar(
                select(
                    func.coalesce(func.max(SystemEvaluationResultVersion.version), 0)
                    + 1
                ).where(SystemEvaluationResultVersion.task_id == task_id)
            )
            or 1
        )

    @classmethod
    def _draft_completeness(
        cls,
        task: SystemEvaluationTask,
        *,
        fields: dict[str, object],
        confidences: dict[str, object],
        missing_fields: list[str],
        warnings: list[dict[str, object]],
        components: list[dict[str, object]],
        score: Decimal | None,
        grade: str | None,
        advice: str | None,
    ) -> tuple[bool, tuple[str, ...]]:
        validation = validate_result_content(
            task.analysis_config_snapshot,
            fields=fields,
            confidences=confidences,
            missing_fields=missing_fields,
            warnings=warnings,
            components=components,
            score=score,
            grade=grade,
            advice=advice,
            require_complete=False,
        )
        return validation.is_complete, validation.missing_required_fields

    @staticmethod
    def _draft_values(
        input: EvaluationManualDraftInput,
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        list[str],
        list[dict[str, object]],
        list[dict[str, object]],
        Decimal | None,
        str | None,
        str | None,
    ]:
        return (
            {item.key: item.value for item in input.fields},
            {item.key: item.confidence for item in input.confidences},
            list(input.missing_fields),
            [item.model_dump(mode="json") for item in input.warnings],
            [item.model_dump(mode="json") for item in input.components],
            Decimal(str(input.score)) if input.score is not None else None,
            input.grade,
            input.advice,
        )

    @classmethod
    def _stored_draft_completeness(
        cls,
        task: SystemEvaluationTask,
        draft: SystemEvaluationManualDraft,
    ) -> tuple[bool, tuple[str, ...]]:
        try:
            validated = EvaluationManualDraftInput.model_validate(
                {
                    "expected_lock_version": task.lock_version,
                    "draft_revision": draft.lock_version,
                    "fields": [
                        {"key": key, "value": value}
                        for key, value in dict(draft.fields).items()
                    ],
                    "confidences": [
                        {"key": key, "confidence": value}
                        for key, value in dict(draft.confidences).items()
                    ],
                    "missing_fields": list(draft.missing_fields),
                    "warnings": list(draft.warnings),
                    "components": list(draft.components),
                    "score": draft.score,
                    "grade": draft.grade,
                    "advice": draft.advice,
                }
            )
        except ValidationError:
            raise EvaluationResultInvalid("evaluation result is invalid") from None
        values = cls._draft_values(validated)
        return cls._draft_completeness(
            task,
            fields=values[0],
            confidences=values[1],
            missing_fields=values[2],
            warnings=values[3],
            components=values[4],
            score=values[5],
            grade=values[6],
            advice=values[7],
        )

    def _append_audit(
        self,
        task: SystemEvaluationTask,
        *,
        actor_user_id: UUID,
        action: str,
        evaluation_action: str,
        request_id: str,
        result: SystemEvaluationResultVersion | None = None,
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

    def save_manual_draft(
        self,
        task_id: UUID,
        input: EvaluationManualDraftInput,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationDraftSaved:
        self._require_aware(occurred_at)
        with write_transaction(self.db):
            task = self.query.require_action_task(
                task_id,
                actor_user_id=actor_user_id,
                permission="evaluation_jobs.manual_input",
                occurred_at=occurred_at,
                for_update=True,
            )
            if task.lock_version != input.expected_lock_version:
                raise EvaluationTaskVersionConflict("evaluation task version changed")
            if task.status not in {"manual_input", "correction_pending"}:
                raise EvaluationTaskStateConflict("evaluation task state changed")
            values = self._draft_values(input)
            complete, missing_required = self._draft_completeness(
                task,
                fields=values[0],
                confidences=values[1],
                missing_fields=values[2],
                warnings=values[3],
                components=values[4],
                score=values[5],
                grade=values[6],
                advice=values[7],
            )
            draft = self.db.scalar(
                select(SystemEvaluationManualDraft)
                .where(SystemEvaluationManualDraft.task_id == task.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if draft is None:
                if input.draft_revision is not None:
                    raise EvaluationTaskVersionConflict(
                        "evaluation draft version changed"
                    )
                draft = SystemEvaluationManualDraft(
                    task_id=task.id,
                    last_edited_by_user_id=actor_user_id,
                    lock_version=1,
                )
                self.db.add(draft)
            else:
                if input.draft_revision != draft.lock_version:
                    raise EvaluationTaskVersionConflict(
                        "evaluation draft version changed"
                    )
                draft.lock_version += 1
                draft.last_edited_by_user_id = actor_user_id
            (
                draft.fields,
                draft.confidences,
                draft.missing_fields,
                draft.warnings,
                draft.components,
                draft.score,
                draft.grade,
                draft.advice,
            ) = values
            draft.updated_at = occurred_at
            self.db.flush()
            self._append_audit(
                task,
                actor_user_id=actor_user_id,
                action="evaluation.task.manual_draft_saved",
                evaluation_action="save_draft",
                request_id=request_id,
            )
            saved = EvaluationDraftSaved(
                task_id=task.id,
                task_status=task.status,
                task_version=task.lock_version,
                draft_revision=draft.lock_version,
                is_complete=complete,
                missing_required_fields=missing_required,
                updated_at=draft.updated_at,
            )
        return saved

    def _result_replay(
        self,
        *,
        resource_id: UUID | None,
    ) -> EvaluationResultSubmitted | None:
        if resource_id is None:
            return None
        result = self.db.scalar(
            select(SystemEvaluationResultVersion)
            .where(SystemEvaluationResultVersion.id == resource_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if result is None:
            raise RuntimeError("evaluation idempotency result is missing")
        event = self.db.scalar(
            select(SystemEvaluationEvent)
            .where(
                SystemEvaluationEvent.task_id == result.task_id,
                SystemEvaluationEvent.result_version_id == result.id,
                SystemEvaluationEvent.event_type == "manual_result_submitted",
            )
            .order_by(SystemEvaluationEvent.sequence)
            .limit(1)
        )
        if event is None:
            raise RuntimeError("evaluation result event is missing")
        task_version = event.event_metadata.get("task_version")
        if not isinstance(task_version, int) or isinstance(task_version, bool):
            raise TypeError("evaluation result event is invalid")
        return EvaluationResultSubmitted(
            task_id=result.task_id,
            result_id=result.id,
            result_version=result.version,
            task_status="needs_review",
            task_version=task_version,
        )

    def submit_manual_result(
        self,
        task_id: UUID,
        input: EvaluationManualSubmitInput,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationResultSubmitted:
        self._require_aware(occurred_at)
        digests = self.idempotency.prepare(
            scope="evaluation.result.manual.submit",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "task_id": str(task_id),
                "expected_lock_version": input.expected_lock_version,
                "draft_revision": input.draft_revision,
            },
        )
        with write_transaction(self.db):
            task = self.query.require_action_task(
                task_id,
                actor_user_id=actor_user_id,
                permission="evaluation_jobs.manual_input",
                occurred_at=occurred_at,
                for_update=True,
            )
            replay = self._result_replay(
                resource_id=self.idempotency.existing_resource_id(
                    digests,
                    occurred_at=occurred_at,
                )
            )
            if replay is not None:
                return replay
            if task.status not in {"manual_input", "correction_pending"}:
                raise EvaluationTaskStateConflict("evaluation task state changed")
            if task.lock_version != input.expected_lock_version:
                raise EvaluationTaskVersionConflict("evaluation task version changed")
            previous_id: UUID | None = None
            source = "manual"
            if task.status == "correction_pending":
                source = "correction"
                previous_id = task.current_result_version_id
            elif task.published_result_version_id is not None:
                source = "revision"
                previous_id = task.current_result_version_id
            if previous_id is not None:
                previous_result = self.db.scalar(
                    select(SystemEvaluationResultVersion)
                    .where(
                        SystemEvaluationResultVersion.id == previous_id,
                        SystemEvaluationResultVersion.task_id == task.id,
                    )
                    .with_for_update()
                )
                if previous_result is None:
                    raise EvaluationResultInvalid("evaluation result is invalid")
            elif source != "manual":
                raise EvaluationResultInvalid("evaluation result is invalid")
            draft = self.db.scalar(
                select(SystemEvaluationManualDraft)
                .where(SystemEvaluationManualDraft.task_id == task.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if draft is None or draft.lock_version != input.draft_revision:
                raise EvaluationTaskVersionConflict("evaluation draft version changed")
            complete, _missing_required = self._stored_draft_completeness(task, draft)
            if not complete:
                raise EvaluationResultInvalid("evaluation result is invalid")

            result_schema_version = task.analysis_config_snapshot.get(
                "result_schema_version"
            )
            if not isinstance(result_schema_version, str) or not result_schema_version:
                raise EvaluationResultInvalid("evaluation result is invalid")
            result = SystemEvaluationResultVersion(
                task_id=task.id,
                version=self._next_result_version(task.id),
                source=source,
                fields=dict(draft.fields),
                confidences=dict(draft.confidences),
                warnings=[dict(item) for item in draft.warnings],
                components=[dict(item) for item in draft.components],
                missing_fields=list(draft.missing_fields),
                score=draft.score,
                grade=draft.grade,
                advice=draft.advice,
                result_schema_version=result_schema_version,
                created_by_user_id=draft.last_edited_by_user_id,
                previous_version_id=previous_id,
                review_decision="pending",
            )
            self.db.add(result)
            self.db.flush()
            source_status = task.status
            require_transition(source_status, "needs_review")
            task.status = "needs_review"
            task.current_result_version_id = result.id
            task.lock_version += 1
            self.db.add(
                SystemEvaluationEvent(
                    task_id=task.id,
                    sequence=self._next_sequence(task.id),
                    event_type="manual_result_submitted",
                    from_status=source_status,
                    to_status="needs_review",
                    result_version_id=result.id,
                    actor_user_id=actor_user_id,
                    event_metadata={
                        "task_version": task.lock_version,
                        "result_version": result.version,
                    },
                    occurred_at=occurred_at,
                )
            )
            self.db.delete(draft)
            self.idempotency.record(
                digests,
                resource_id=result.id,
                occurred_at=occurred_at,
            )
            self._append_audit(
                task,
                actor_user_id=actor_user_id,
                action="evaluation.task.manual_result_submitted",
                evaluation_action="manual_submit",
                request_id=request_id,
                result=result,
            )
            self.db.flush()
            submitted = EvaluationResultSubmitted(
                task_id=task.id,
                result_id=result.id,
                result_version=result.version,
                task_status=task.status,
                task_version=task.lock_version,
            )
        return submitted

    @staticmethod
    def _copy_result_to_draft(
        result: SystemEvaluationResultVersion,
        *,
        task_id: UUID,
        editor_user_id: UUID,
        occurred_at: datetime,
    ) -> SystemEvaluationManualDraft:
        return SystemEvaluationManualDraft(
            task_id=task_id,
            last_edited_by_user_id=editor_user_id,
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

    def _revision_replay(
        self,
        *,
        task_id: UUID,
        resource_id: UUID | None,
        expected_state: str,
        mode: str,
    ) -> EvaluationRevisionStarted | None:
        if resource_id is None:
            return None
        event_type = (
            "correction_started"
            if expected_state == "rejected"
            else "revision_routed"
        )
        event = self.db.scalar(
            select(SystemEvaluationEvent).where(
                SystemEvaluationEvent.id == resource_id,
                SystemEvaluationEvent.task_id == task_id,
                SystemEvaluationEvent.event_type == event_type,
            )
        )
        expected_status = (
            "correction_pending"
            if expected_state == "rejected"
            else ("manual_input" if mode == "manual" else "queued")
        )
        if event is None or event.to_status != expected_status:
            raise TypeError("evaluation revision event is invalid")
        task_version = event.event_metadata.get("task_version")
        if not isinstance(task_version, int) or isinstance(task_version, bool):
            raise TypeError("evaluation revision event is invalid")
        return EvaluationRevisionStarted(
            task_id=task_id,
            task_status=expected_status,
            task_version=task_version,
            draft_revision=1 if mode == "manual" else None,
        )

    def _start_revision_action(
        self,
        task_id: UUID,
        input: EvaluationResultRevisionInput,
        *,
        expected_state: str,
        intermediate_state: str,
        actor_user_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationRevisionStarted:
        self._require_aware(occurred_at)
        scope = (
            "evaluation.result.correction.start"
            if expected_state == "rejected"
            else "evaluation.result.revision.start"
        )
        digests = self.idempotency.prepare(
            scope=scope,
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "task_id": str(task_id),
                "expected_lock_version": input.expected_lock_version,
                "mode": input.mode,
            },
        )
        with write_transaction(self.db):
            task = self.query.require_action_task(
                task_id,
                actor_user_id=actor_user_id,
                permission="evaluation_jobs.manual_input",
                occurred_at=occurred_at,
                for_update=True,
            )
            replay = self._revision_replay(
                task_id=task.id,
                resource_id=self.idempotency.existing_resource_id(
                    digests,
                    occurred_at=occurred_at,
                ),
                expected_state=expected_state,
                mode=input.mode,
            )
            if replay is not None:
                return replay
            if task.status != expected_state:
                raise EvaluationTaskStateConflict("evaluation task state changed")
            if task.lock_version != input.expected_lock_version:
                raise EvaluationTaskVersionConflict("evaluation task version changed")
            if task.current_result_version_id is None:
                raise EvaluationResultInvalid("evaluation result is invalid")
            base_result = self.db.scalar(
                select(SystemEvaluationResultVersion)
                .where(
                    SystemEvaluationResultVersion.id
                    == task.current_result_version_id,
                    SystemEvaluationResultVersion.task_id == task.id,
                )
                .with_for_update()
            )
            if base_result is None:
                raise EvaluationResultInvalid("evaluation result is invalid")
            require_transition(expected_state, intermediate_state)
            task.status = intermediate_state
            final_status = intermediate_state
            event_sequence = self._next_sequence(task.id)
            started_event = SystemEvaluationEvent(
                task_id=task.id,
                sequence=event_sequence,
                event_type=(
                    "correction_started"
                    if expected_state == "rejected"
                    else "revision_started"
                ),
                from_status=expected_state,
                to_status=intermediate_state,
                result_version_id=base_result.id,
                actor_user_id=actor_user_id,
                event_metadata={},
                occurred_at=occurred_at,
            )
            self.db.add(started_event)
            response_event = started_event
            draft_revision: int | None = None
            if expected_state == "rejected":
                if input.mode != "manual":
                    raise EvaluationResultInvalid("evaluation result is invalid")
                draft = self._copy_result_to_draft(
                    base_result,
                    task_id=task.id,
                    editor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                )
                self.db.add(draft)
                draft_revision = 1
            else:
                final_status = "manual_input" if input.mode == "manual" else "queued"
                require_transition(intermediate_state, final_status)
                if input.mode == "manual":
                    draft = self._copy_result_to_draft(
                        base_result,
                        task_id=task.id,
                        editor_user_id=actor_user_id,
                        occurred_at=occurred_at,
                    )
                    self.db.add(draft)
                    draft_revision = 1
                else:
                    if task.analysis_config_snapshot.get("mode") != "automatic":
                        raise EvaluationResultInvalid("evaluation result is invalid")
                    task.available_at = occurred_at
                response_event = SystemEvaluationEvent(
                    task_id=task.id,
                    sequence=event_sequence + 1,
                    event_type="revision_routed",
                    from_status=intermediate_state,
                    to_status=final_status,
                    result_version_id=base_result.id,
                    actor_user_id=actor_user_id,
                    event_metadata={},
                    occurred_at=occurred_at,
                )
                self.db.add(response_event)
                task.status = final_status
            task.lock_version += 1
            for event in self.db.new:
                if isinstance(event, SystemEvaluationEvent) and event.task_id == task.id:
                    event.event_metadata = {
                        "task_version": task.lock_version,
                        "result_version": base_result.version,
                    }
            self.db.flush()
            self.idempotency.record(
                digests,
                resource_id=response_event.id,
                occurred_at=occurred_at,
            )
            self._append_audit(
                task,
                actor_user_id=actor_user_id,
                action=(
                    "evaluation.task.correction_started"
                    if expected_state == "rejected"
                    else "evaluation.task.revision_started"
                ),
                evaluation_action=(
                    "create_correction"
                    if expected_state == "rejected"
                    else "create_revision"
                ),
                request_id=request_id,
                result=base_result,
            )
            self.db.flush()
            started = EvaluationRevisionStarted(
                task_id=task.id,
                task_status=task.status,
                task_version=task.lock_version,
                draft_revision=draft_revision,
            )
        return started

    def start_correction(
        self,
        task_id: UUID,
        input: EvaluationResultRevisionInput,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationRevisionStarted:
        return self._start_revision_action(
            task_id,
            input,
            expected_state="rejected",
            intermediate_state="correction_pending",
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            request_id=request_id,
        )

    def start_revision(
        self,
        task_id: UUID,
        input: EvaluationResultRevisionInput,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationRevisionStarted:
        return self._start_revision_action(
            task_id,
            input,
            expected_state="published",
            intermediate_state="revision_draft",
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            request_id=request_id,
        )
