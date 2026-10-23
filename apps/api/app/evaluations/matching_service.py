from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.evaluations.errors import (
    EvaluationResourceNotFound,
    EvaluationTaskStateConflict,
    EvaluationTaskVersionConflict,
)
from app.evaluations.game_id_digest import student_game_id_digest
from app.evaluations.game_models import StudentAuxiliaryPhone, StudentGameAccount
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.query_service import EvaluationQueryService
from app.evaluations.review_schemas import EvaluationTaskMatchInput
from app.evaluations.schemas import normalize_game_id
from app.evaluations.task_access import (
    AuthorizedEvaluationTarget,
    AuthorizedUploadTargetSelection,
    EvaluationTaskAccessService,
)
from app.evaluations.task_models import (
    SystemEvaluationEvent,
    SystemEvaluationTask,
    SystemEvaluationTaskMedia,
)
from app.evaluations.task_state import require_transition
from app.identity.models import Person
from app.identity.normalization import normalize_phone
from app.security.encryption import LookupDigest


@dataclass(frozen=True)
class EvaluationTaskMatched:
    task_id: UUID
    student_person_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    project_id: UUID
    class_id: UUID
    task_status: str
    task_version: int


class EvaluationMatchingService:
    def __init__(
        self,
        db: Session,
        *,
        lookup: LookupDigest,
        access: EvaluationTaskAccessService | None = None,
        idempotency_service: EvaluationIdempotencyService | None = None,
        query_service: EvaluationQueryService | None = None,
    ) -> None:
        self.db = db
        self.lookup = lookup
        self.access = access or EvaluationTaskAccessService(db)
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
    def _input_matches_target(
        input: EvaluationTaskMatchInput,
        target: AuthorizedEvaluationTarget,
    ) -> bool:
        return bool(
            input.student_person_id == target.student_person_id
            and input.enrollment_id == target.enrollment_id
            and input.cohort_id == target.cohort_id
            and input.class_id == target.class_id
        )

    @staticmethod
    def _final_status(task: SystemEvaluationTask) -> str:
        mode = task.analysis_config_snapshot.get("mode")
        if mode == "automatic":
            return "queued"
        if mode == "manual_only":
            return "manual_input"
        raise EvaluationTaskStateConflict("evaluation task state changed")

    def _match_replay(
        self,
        *,
        task: SystemEvaluationTask,
        resource_id: UUID | None,
    ) -> EvaluationTaskMatched | None:
        if resource_id is None:
            return None
        event = self.db.scalar(
            select(SystemEvaluationEvent).where(
                SystemEvaluationEvent.id == resource_id,
                SystemEvaluationEvent.task_id == task.id,
                SystemEvaluationEvent.event_type == "task_matched",
            )
        )
        task_version = event.event_metadata.get("task_version") if event else None
        if (
            event is None
            or event.to_status not in {"queued", "manual_input"}
            or not isinstance(task_version, int)
            or task.student_person_id is None
            or task.enrollment_id is None
            or task.cohort_id is None
            or task.class_id is None
        ):
            raise RuntimeError("evaluation match replay is invalid")
        return EvaluationTaskMatched(
            task_id=task.id,
            student_person_id=task.student_person_id,
            enrollment_id=task.enrollment_id,
            cohort_id=task.cohort_id,
            project_id=task.project_id,
            class_id=task.class_id,
            task_status=event.to_status,
            task_version=task_version,
        )

    def confirm_task_match(
        self,
        task_id: UUID,
        input: EvaluationTaskMatchInput,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationTaskMatched:
        self._require_aware(occurred_at)
        if self.idempotency is None:
            raise RuntimeError("evaluation idempotency service is required")
        digests = self.idempotency.prepare(
            scope="evaluation.task.match",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "task_id": str(task_id),
                **input.model_dump(mode="json"),
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
            replay_resource_id = self.idempotency.existing_resource_id(
                digests,
                occurred_at=occurred_at,
            )
            if replay_resource_id is not None:
                replay_target = self.access.require_match_target(
                    account_id=actor_user_id,
                    enrollment_id=input.enrollment_id,
                    project_id=task.project_id,
                    occurred_at=occurred_at,
                )
                if not self._input_matches_target(input, replay_target):
                    raise EvaluationResourceNotFound("evaluation resource not found")
                replay = self._match_replay(
                    task=task,
                    resource_id=replay_resource_id,
                )
                if replay is None or any(
                    (
                        replay.student_person_id != replay_target.student_person_id,
                        replay.enrollment_id != replay_target.enrollment_id,
                        replay.cohort_id != replay_target.cohort_id,
                        replay.project_id != replay_target.project_id,
                        replay.class_id != replay_target.class_id,
                    )
                ):
                    raise RuntimeError("evaluation match replay is invalid")
                return replay
            if task.status != "pending_match" or any(
                value is not None
                for value in (
                    task.student_person_id,
                    task.enrollment_id,
                    task.cohort_id,
                    task.class_id,
                )
            ):
                raise EvaluationTaskStateConflict("evaluation task state changed")
            if task.lock_version != input.expected_lock_version:
                raise EvaluationTaskVersionConflict("evaluation task version changed")
            target = self.access.require_match_target(
                account_id=actor_user_id,
                enrollment_id=input.enrollment_id,
                project_id=task.project_id,
                occurred_at=occurred_at,
            )
            if not self._input_matches_target(input, target):
                raise EvaluationResourceNotFound("evaluation resource not found")
            assets = list(
                self.db.scalars(
                    select(EvaluationMediaAsset)
                    .join(
                        SystemEvaluationTaskMedia,
                        SystemEvaluationTaskMedia.media_id
                        == EvaluationMediaAsset.id,
                    )
                    .where(SystemEvaluationTaskMedia.task_id == task.id)
                    .order_by(EvaluationMediaAsset.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if not assets or any(
                asset.project_id != task.project_id
                or asset.student_person_id is not None
                or asset.enrollment_id is not None
                or asset.class_id is not None
                for asset in assets
            ):
                raise EvaluationTaskStateConflict("evaluation task state changed")
            blob_ids = [asset.blob_id for asset in assets]
            if len(set(blob_ids)) != len(blob_ids):
                raise EvaluationTaskStateConflict("evaluation task state changed")
            locked_blob_ids = set(
                self.db.scalars(
                    select(EvaluationMediaBlob.id)
                    .where(EvaluationMediaBlob.id.in_(blob_ids))
                    .order_by(EvaluationMediaBlob.id)
                    .with_for_update()
                )
            )
            if locked_blob_ids != set(blob_ids):
                raise EvaluationTaskStateConflict("evaluation task state changed")
            duplicate = self.db.scalar(
                select(EvaluationMediaAsset.id)
                .where(
                    EvaluationMediaAsset.id.not_in([asset.id for asset in assets]),
                    EvaluationMediaAsset.blob_id.in_(blob_ids),
                    EvaluationMediaAsset.student_person_id
                    == target.student_person_id,
                    EvaluationMediaAsset.enrollment_id == target.enrollment_id,
                    EvaluationMediaAsset.project_id == target.project_id,
                )
                .limit(1)
            )
            if duplicate is not None:
                raise EvaluationTaskStateConflict("evaluation task state changed")

            final_status = self._final_status(task)
            require_transition("pending_match", final_status)
            for asset in assets:
                asset.student_person_id = target.student_person_id
                asset.enrollment_id = target.enrollment_id
                asset.class_id = target.class_id
                asset.matched_at = occurred_at
                asset.matched_by_user_id = actor_user_id
                asset.match_reason = "task_target_confirmed"
            task.student_person_id = target.student_person_id
            task.enrollment_id = target.enrollment_id
            task.cohort_id = target.cohort_id
            task.class_id = target.class_id
            task.status = final_status
            task.matched_at = occurred_at
            task.matched_by_user_id = actor_user_id
            task.lock_version += 1
            event = SystemEvaluationEvent(
                task_id=task.id,
                sequence=self._next_sequence(task.id),
                event_type="task_matched",
                from_status="pending_match",
                to_status=final_status,
                actor_user_id=actor_user_id,
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
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="evaluation.task.matched",
                resource_type="system_evaluation_task",
                resource_id=task.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "task_id": str(task.id),
                    "task_status": final_status,
                    "evaluation_action": "confirm_match",
                    "task_version": task.lock_version,
                    "media_count": len(assets),
                },
            )
            self.db.flush()
            matched = EvaluationTaskMatched(
                task_id=task.id,
                student_person_id=target.student_person_id,
                enrollment_id=target.enrollment_id,
                cohort_id=target.cohort_id,
                project_id=target.project_id,
                class_id=target.class_id,
                task_status=task.status,
                task_version=task.lock_version,
            )
        return matched

    def _game_id_targets(
        self,
        *,
        selection: AuthorizedUploadTargetSelection,
        project_id: UUID,
        occurred_at: datetime,
        game_id: str,
    ) -> list[AuthorizedEvaluationTarget]:
        digest = student_game_id_digest(self.lookup, normalize_game_id(game_id))
        authorized = selection.statement.cte("authorized_evaluation_game_targets")
        rows = self.db.execute(
            select(authorized)
            .join(
                StudentGameAccount,
                and_(
                    StudentGameAccount.student_person_id
                    == authorized.c.student_person_id,
                    StudentGameAccount.project_id == authorized.c.project_id,
                ),
            )
            .where(
                StudentGameAccount.project_id == project_id,
                StudentGameAccount.identifier_digest == digest,
                StudentGameAccount.valid_from <= occurred_at,
                or_(
                    StudentGameAccount.unbound_at.is_(None),
                    StudentGameAccount.unbound_at > occurred_at,
                ),
            )
            .distinct()
            .limit(2)
        ).mappings().all()
        return [selection.target_from_mapping(row) for row in rows]

    def _primary_phone_targets(
        self,
        *,
        selection: AuthorizedUploadTargetSelection,
        phone: str,
    ) -> list[AuthorizedEvaluationTarget]:
        digest = self.lookup.digest(normalize_phone(phone))
        authorized = selection.statement.cte("authorized_evaluation_phone_targets")
        rows = self.db.execute(
            select(authorized)
            .join(Person, Person.id == authorized.c.student_person_id)
            .where(Person.phone_digest == digest)
            .distinct()
            .limit(2)
        ).mappings().all()
        return [selection.target_from_mapping(row) for row in rows]

    def _auxiliary_phone_targets(
        self,
        *,
        selection: AuthorizedUploadTargetSelection,
        occurred_at: datetime,
        phone: str,
    ) -> list[AuthorizedEvaluationTarget]:
        digest = self.lookup.digest(normalize_phone(phone))
        authorized = selection.statement.cte("authorized_evaluation_aux_phone_targets")
        rows = self.db.execute(
            select(authorized)
            .join(
                StudentAuxiliaryPhone,
                StudentAuxiliaryPhone.student_person_id
                == authorized.c.student_person_id,
            )
            .where(
                StudentAuxiliaryPhone.phone_digest == digest,
                StudentAuxiliaryPhone.valid_from <= occurred_at,
                or_(
                    StudentAuxiliaryPhone.disabled_at.is_(None),
                    StudentAuxiliaryPhone.disabled_at > occurred_at,
                ),
            )
            .distinct()
            .limit(2)
        ).mappings().all()
        return [selection.target_from_mapping(row) for row in rows]

    def match_upload_target(
        self,
        *,
        account_id: UUID,
        project_id: UUID,
        occurred_at: datetime,
        game_id: str | None,
        phone: str | None,
    ) -> AuthorizedEvaluationTarget | None:
        selection = self.access.authorized_upload_targets(
            account_id=account_id,
            project_id=project_id,
            occurred_at=occurred_at,
        )
        if game_id is not None:
            game_targets = self._game_id_targets(
                selection=selection,
                project_id=project_id,
                occurred_at=occurred_at,
                game_id=game_id,
            )
            if len(game_targets) == 1:
                return game_targets[0]
            if game_targets:
                return None
        if phone is not None:
            phone_targets = self._primary_phone_targets(
                selection=selection,
                phone=phone,
            )
            if len(phone_targets) == 1:
                return phone_targets[0]
            if phone_targets:
                return None
            auxiliary_targets = self._auxiliary_phone_targets(
                selection=selection,
                occurred_at=occurred_at,
                phone=phone,
            )
            if len(auxiliary_targets) == 1:
                return auxiliary_targets[0]
            if auxiliary_targets:
                return None
        return None
