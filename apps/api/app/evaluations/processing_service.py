from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.evaluations.adapter_registry import supported_adapters as registered_adapters
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationLeaseInvalid,
    EvaluationResultInvalid,
)
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.processing_models import SystemEvaluationAttempt
from app.evaluations.processing_schemas import (
    SystemEvaluationFailureInput,
    SystemEvaluationResultInput,
)
from app.evaluations.result_validation import validate_result_content
from app.evaluations.review_models import SystemEvaluationResultVersion
from app.evaluations.schemas import is_public_analysis_key
from app.evaluations.task_models import (
    SystemEvaluationEvent,
    SystemEvaluationTask,
    SystemEvaluationTaskMedia,
)
from app.evaluations.worker_ticket import WorkerMediaTicketSigner

_WORKER_IDENTITY = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}\Z")
_LEASE_DOMAIN = b"evaluation-worker-lease-v1\x00"
_RESULT_IDEMPOTENCY_DOMAIN = b"evaluation-worker-result-v1\x00"
_FAILURE_IDEMPOTENCY_DOMAIN = b"evaluation-worker-failure-v1\x00"
_RETRYABLE_FAILURES = frozenset(
    {"network", "worker_interrupted", "ocr_timeout", "temporary_service"}
)


@dataclass(frozen=True)
class EvaluationClaim:
    task_id: UUID
    attempt: int
    task_version: int
    lease_token: str
    lease_expires_at: datetime
    adapter_id: str
    adapter_version: str
    scoring_rule_version: str
    result_schema_version: str
    required_fields: tuple[str, ...]
    confidence_thresholds: dict[str, float]
    media: tuple[EvaluationClaimMedia, ...]


@dataclass(frozen=True)
class EvaluationClaimMedia:
    media_id: UUID
    position: int
    slot_key: str
    mime_type: str
    size_bytes: int
    ticket: str


@dataclass(frozen=True)
class EvaluationLeaseRenewal:
    task_id: UUID
    attempt: int
    task_version: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class EvaluationResultSubmission:
    task_id: UUID
    result_id: UUID
    result_version: int
    task_status: str
    task_version: int


@dataclass(frozen=True)
class EvaluationFailureSubmission:
    task_id: UUID
    attempt: int
    task_status: str
    task_version: int
    retryable: bool
    available_at: datetime | None
    failure_code: str | None


class EvaluationProcessingService:
    LEASE_DURATION = timedelta(minutes=5)

    def __init__(
        self,
        db: Session,
        *,
        token_pepper: bytes,
        include_test_adapters: bool = False,
    ) -> None:
        if len(token_pepper) < 32:
            raise ValueError("token_pepper must contain at least 32 bytes")
        self.db = db
        self.token_pepper = token_pepper
        self.include_test_adapters = include_test_adapters

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    @staticmethod
    def _require_worker_identity(value: str) -> None:
        if _WORKER_IDENTITY.fullmatch(value) is None:
            raise ValueError("worker_identity is invalid")

    def _lease_digest(self, token: str) -> str:
        return hmac.new(
            self.token_pepper,
            _LEASE_DOMAIN + token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _result_idempotency_digest(
        self,
        input: SystemEvaluationResultInput,
    ) -> str:
        canonical = json.dumps(
            input.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(
            self.token_pepper,
            _RESULT_IDEMPOTENCY_DOMAIN + canonical,
            hashlib.sha256,
        ).hexdigest()

    def _failure_idempotency_digest(
        self,
        input: SystemEvaluationFailureInput,
    ) -> str:
        canonical = json.dumps(
            input.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(
            self.token_pepper,
            _FAILURE_IDEMPOTENCY_DOMAIN + canonical,
            hashlib.sha256,
        ).hexdigest()

    def _adapter_pairs(self, keys: set[str]) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for adapter_id, adapter_version in registered_adapters(
            include_test_only=self.include_test_adapters
        ):
            major = adapter_version.partition(".")[0]
            if f"{adapter_id}.v{major}" in keys:
                pairs.add((adapter_id, adapter_version))
        return pairs

    def _next_event_sequence(self, task_id: UUID) -> int:
        return int(
            self.db.scalar(
                select(func.coalesce(func.max(SystemEvaluationEvent.sequence), 0) + 1)
                .where(SystemEvaluationEvent.task_id == task_id)
            )
            or 1
        )

    @staticmethod
    def _snapshot_value(snapshot: dict[str, object], key: str) -> str:
        value = snapshot.get(key)
        if not isinstance(value, str) or not value:
            raise EvaluationLeaseInvalid("evaluation lease is invalid")
        return value

    @staticmethod
    def _claim_result_config(
        snapshot: dict[str, object],
    ) -> tuple[tuple[str, ...], dict[str, float]]:
        raw_fields = snapshot.get("required_fields")
        raw_thresholds = snapshot.get("confidence_thresholds")
        if (
            not isinstance(raw_fields, list)
            or not raw_fields
            or any(not is_public_analysis_key(field) for field in raw_fields)
            or len(raw_fields) != len(set(raw_fields))
            or not isinstance(raw_thresholds, dict)
            or set(raw_thresholds) != set(raw_fields)
        ):
            raise EvaluationLeaseInvalid("evaluation lease is invalid")
        thresholds: dict[str, float] = {}
        for key, value in raw_thresholds.items():
            if (
                not isinstance(key, str)
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise EvaluationLeaseInvalid("evaluation lease is invalid")
            thresholds[key] = float(value)
        return tuple(raw_fields), thresholds

    def _recover_one_expired_lease(
        self,
        *,
        occurred_at: datetime,
        request_id: str,
    ) -> None:
        task = self.db.scalar(
            select(SystemEvaluationTask)
            .join(
                SystemEvaluationAttempt,
                and_(
                    SystemEvaluationAttempt.task_id == SystemEvaluationTask.id,
                    SystemEvaluationAttempt.attempt
                    == SystemEvaluationTask.current_attempt,
                ),
            )
            .where(
                SystemEvaluationTask.status == "processing",
                SystemEvaluationAttempt.status == "active",
                SystemEvaluationAttempt.lease_expires_at <= occurred_at,
            )
            .order_by(
                SystemEvaluationAttempt.lease_expires_at,
                SystemEvaluationTask.id,
            )
            .with_for_update(of=SystemEvaluationTask, skip_locked=True)
            .limit(1)
        )
        if task is None:
            return
        attempt = self.db.scalar(
            select(SystemEvaluationAttempt)
            .where(
                SystemEvaluationAttempt.task_id == task.id,
                SystemEvaluationAttempt.attempt == task.current_attempt,
            )
            .with_for_update()
        )
        if (
            attempt is None
            or attempt.status != "active"
            or attempt.lease_expires_at > occurred_at
        ):
            return
        attempt.status = "expired"
        attempt.completed_at = occurred_at
        attempt.error_class = "worker_interrupted"
        attempt.retryable = True
        attempt.safe_error_summary = "worker lease expired"

        if attempt.attempt < 3:
            final_status = "queued"
            task.available_at = occurred_at
            task.failure_code = None
            task.manual_queue_reason = None
        else:
            final_status = "manual_input"
            task.available_at = None
            task.failure_code = "automatic_retries_exhausted"
            task.manual_queue_reason = task.failure_code
        task.status = final_status
        task.lock_version += 1
        sequence = self._next_event_sequence(task.id)
        self.db.add_all(
            [
                SystemEvaluationEvent(
                    task_id=task.id,
                    sequence=sequence,
                    event_type="worker_lease_expired",
                    from_status="processing",
                    to_status="failed",
                    event_metadata={
                        "attempt": attempt.attempt,
                        "task_version": task.lock_version,
                    },
                    occurred_at=occurred_at,
                ),
                SystemEvaluationEvent(
                    task_id=task.id,
                    sequence=sequence + 1,
                    event_type=(
                        "worker_retry_scheduled"
                        if final_status == "queued"
                        else "worker_manual_input_required"
                    ),
                    from_status="failed",
                    to_status=final_status,
                    event_metadata={
                        "attempt": attempt.attempt,
                        "task_version": task.lock_version,
                    },
                    occurred_at=occurred_at,
                ),
            ]
        )
        AuditService(self.db).append(
            actor_user_id=None,
            action="evaluation.task.failure_recorded",
            resource_type="system_evaluation_task",
            resource_id=task.id,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata={
                "task_id": str(task.id),
                "attempt_id": str(attempt.id),
                "attempt": attempt.attempt,
                "task_version": task.lock_version,
                "task_status": final_status,
                "attempt_status": "expired",
                "evaluation_action": "record_failure",
            },
        )
        self.db.flush()

    def claim_next(
        self,
        *,
        worker_identity: str,
        supported_adapters: set[str],
        occurred_at: datetime,
        request_id: str = "evaluation-worker-claim",
    ) -> EvaluationClaim | None:
        self._require_aware(occurred_at)
        self._require_worker_identity(worker_identity)
        pairs = self._adapter_pairs(supported_adapters)
        if not pairs:
            return None

        adapter_conditions = [
            and_(
                SystemEvaluationTask.analysis_config_snapshot["adapter_id"].astext
                == adapter_id,
                SystemEvaluationTask.analysis_config_snapshot["adapter_version"].astext
                == adapter_version,
            )
            for adapter_id, adapter_version in sorted(pairs)
        ]
        with write_transaction(self.db):
            self._recover_one_expired_lease(
                occurred_at=occurred_at,
                request_id=request_id,
            )
            task = self.db.scalar(
                select(SystemEvaluationTask)
                .where(
                    SystemEvaluationTask.status == "queued",
                    SystemEvaluationTask.current_attempt < 3,
                    or_(
                        SystemEvaluationTask.available_at.is_(None),
                        SystemEvaluationTask.available_at <= occurred_at,
                    ),
                    SystemEvaluationTask.analysis_config_snapshot["mode"].astext
                    == "automatic",
                    or_(*adapter_conditions),
                )
                .order_by(
                    SystemEvaluationTask.available_at.asc().nulls_first(),
                    SystemEvaluationTask.created_at,
                    SystemEvaluationTask.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return None

            snapshot = dict(task.analysis_config_snapshot)
            adapter_id = self._snapshot_value(snapshot, "adapter_id")
            adapter_version = self._snapshot_value(snapshot, "adapter_version")
            scoring_rule_version = self._snapshot_value(
                snapshot,
                "scoring_rule_version",
            )
            result_schema_version = self._snapshot_value(
                snapshot,
                "result_schema_version",
            )
            required_fields, confidence_thresholds = self._claim_result_config(
                snapshot
            )
            attempt_number = task.current_attempt + 1
            task.status = "processing"
            task.current_attempt = attempt_number
            task.automatic_attempts += 1
            task.available_at = None
            task.processing_started_at = task.processing_started_at or occurred_at
            task.lock_version += 1

            lease_token = secrets.token_urlsafe(32)
            lease_expires_at = occurred_at + self.LEASE_DURATION
            attempt = SystemEvaluationAttempt(
                task_id=task.id,
                attempt=attempt_number,
                task_lock_version=task.lock_version,
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                scoring_rule_version=scoring_rule_version,
                result_schema_version=result_schema_version,
                worker_identity=worker_identity,
                lease_token_digest=self._lease_digest(lease_token),
                claimed_at=occurred_at,
                lease_expires_at=lease_expires_at,
                status="active",
            )
            self.db.add(attempt)
            self.db.flush()

            media_rows = self.db.execute(
                select(
                    SystemEvaluationTaskMedia,
                    EvaluationMediaAsset,
                    EvaluationMediaBlob,
                )
                .join(
                    EvaluationMediaAsset,
                    EvaluationMediaAsset.id == SystemEvaluationTaskMedia.media_id,
                )
                .join(
                    EvaluationMediaBlob,
                    EvaluationMediaBlob.id == EvaluationMediaAsset.blob_id,
                )
                .where(SystemEvaluationTaskMedia.task_id == task.id)
                .order_by(SystemEvaluationTaskMedia.position)
            ).all()
            if not 1 <= len(media_rows) <= 3 or any(
                not is_public_analysis_key(task_media.slot_key)
                for task_media, _asset, _blob in media_rows
            ):
                raise EvaluationLeaseInvalid("evaluation lease is invalid")
            ticket_signer = WorkerMediaTicketSigner(self.token_pepper)
            media = tuple(
                EvaluationClaimMedia(
                    media_id=task_media.media_id,
                    position=task_media.position,
                    slot_key=task_media.slot_key,
                    mime_type=blob.mime_type,
                    size_bytes=blob.size_bytes,
                    ticket=ticket_signer.issue(
                        task_id=task.id,
                        attempt=attempt_number,
                        task_version=task.lock_version,
                        media_id=task_media.media_id,
                        expires_at=lease_expires_at,
                        worker_identity=worker_identity,
                        lease_token_digest=attempt.lease_token_digest,
                    ),
                )
                for task_media, _asset, blob in media_rows
            )

            self.db.add(
                SystemEvaluationEvent(
                    task_id=task.id,
                    sequence=self._next_event_sequence(task.id),
                    event_type="worker_claimed",
                    from_status="queued",
                    to_status="processing",
                    event_metadata={
                        "attempt": attempt_number,
                        "task_version": task.lock_version,
                    },
                    occurred_at=occurred_at,
                )
            )
            AuditService(self.db).append(
                actor_user_id=None,
                action="evaluation.task.claimed",
                resource_type="system_evaluation_task",
                resource_id=task.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "task_id": str(task.id),
                    "attempt_id": str(attempt.id),
                    "attempt": attempt_number,
                    "task_version": task.lock_version,
                    "task_status": "processing",
                    "attempt_status": "active",
                    "evaluation_action": "claim",
                },
            )
            self.db.flush()
            claim = EvaluationClaim(
                task_id=task.id,
                attempt=attempt_number,
                task_version=task.lock_version,
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                scoring_rule_version=scoring_rule_version,
                result_schema_version=result_schema_version,
                required_fields=required_fields,
                confidence_thresholds=confidence_thresholds,
                media=media,
            )
        return claim

    def _active_lease(
        self,
        *,
        task_id: UUID,
        attempt_number: int,
        task_version: int,
        worker_identity: str,
        lease_token: str,
        occurred_at: datetime,
    ) -> tuple[SystemEvaluationTask, SystemEvaluationAttempt]:
        task = self.db.scalar(
            select(SystemEvaluationTask)
            .where(SystemEvaluationTask.id == task_id)
            .with_for_update()
        )
        attempt = self.db.scalar(
            select(SystemEvaluationAttempt)
            .where(
                SystemEvaluationAttempt.task_id == task_id,
                SystemEvaluationAttempt.attempt == attempt_number,
            )
            .with_for_update()
        )
        if (
            task is None
            or attempt is None
            or task.status != "processing"
            or task.current_attempt != attempt_number
            or task.lock_version != task_version
            or attempt.status != "active"
            or not self._attempt_credentials_match(
                attempt,
                task_version=task_version,
                worker_identity=worker_identity,
                lease_token=lease_token,
                occurred_at=occurred_at,
            )
        ):
            raise EvaluationLeaseInvalid("evaluation lease is invalid")
        return task, attempt

    def _attempt_credentials_match(
        self,
        attempt: SystemEvaluationAttempt,
        *,
        task_version: int,
        worker_identity: str,
        lease_token: str,
        occurred_at: datetime,
    ) -> bool:
        return bool(
            attempt.task_lock_version == task_version
            and attempt.worker_identity == worker_identity
            and attempt.lease_expires_at > occurred_at
            and hmac.compare_digest(
                attempt.lease_token_digest,
                self._lease_digest(lease_token),
            )
        )

    def validate_result_lease(
        self,
        *,
        task_id: UUID,
        attempt: int,
        task_version: int,
        worker_identity: str,
        lease_token: str,
        occurred_at: datetime,
    ) -> None:
        self._require_aware(occurred_at)
        self._require_worker_identity(worker_identity)
        with write_transaction(self.db):
            task = self.db.scalar(
                select(SystemEvaluationTask)
                .where(SystemEvaluationTask.id == task_id)
                .with_for_update()
            )
            stored_attempt = self.db.scalar(
                select(SystemEvaluationAttempt)
                .where(
                    SystemEvaluationAttempt.task_id == task_id,
                    SystemEvaluationAttempt.attempt == attempt,
                )
                .with_for_update()
            )
            if (
                task is None
                or stored_attempt is None
                or not self._attempt_credentials_match(
                    stored_attempt,
                    task_version=task_version,
                    worker_identity=worker_identity,
                    lease_token=lease_token,
                    occurred_at=occurred_at,
                )
                or (
                    stored_attempt.status == "active"
                    and (
                        task.status != "processing"
                        or task.current_attempt != attempt
                        or task.lock_version != task_version
                    )
                )
                or stored_attempt.status not in {"active", "succeeded"}
            ):
                raise EvaluationLeaseInvalid("evaluation lease is invalid")

    def validate_failure_lease(
        self,
        *,
        task_id: UUID,
        attempt: int,
        task_version: int,
        worker_identity: str,
        lease_token: str,
        occurred_at: datetime,
    ) -> None:
        self._require_aware(occurred_at)
        self._require_worker_identity(worker_identity)
        with write_transaction(self.db):
            self._active_lease(
                task_id=task_id,
                attempt_number=attempt,
                task_version=task_version,
                worker_identity=worker_identity,
                lease_token=lease_token,
                occurred_at=occurred_at,
            )

    def renew(
        self,
        *,
        task_id: UUID,
        attempt: int,
        task_version: int,
        worker_identity: str,
        lease_token: str,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationLeaseRenewal:
        self._require_aware(occurred_at)
        self._require_worker_identity(worker_identity)
        with write_transaction(self.db):
            task, stored_attempt = self._active_lease(
                task_id=task_id,
                attempt_number=attempt,
                task_version=task_version,
                worker_identity=worker_identity,
                lease_token=lease_token,
                occurred_at=occurred_at,
            )
            if (
                stored_attempt.renewed_at is None
                or occurred_at > stored_attempt.renewed_at
            ):
                stored_attempt.renewed_at = occurred_at
            stored_attempt.lease_expires_at = max(
                stored_attempt.lease_expires_at,
                occurred_at + self.LEASE_DURATION,
            )
            self.db.add(
                SystemEvaluationEvent(
                    task_id=task.id,
                    sequence=self._next_event_sequence(task.id),
                    event_type="worker_lease_renewed",
                    event_metadata={
                        "attempt": attempt,
                        "task_version": task_version,
                    },
                    occurred_at=occurred_at,
                )
            )
            AuditService(self.db).append(
                actor_user_id=None,
                action="evaluation.task.lease_renewed",
                resource_type="system_evaluation_task",
                resource_id=task.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "task_id": str(task.id),
                    "attempt_id": str(stored_attempt.id),
                    "attempt": attempt,
                    "task_version": task_version,
                    "task_status": "processing",
                    "attempt_status": "active",
                    "evaluation_action": "renew",
                },
            )
            self.db.flush()
            renewal = EvaluationLeaseRenewal(
                task_id=task.id,
                attempt=attempt,
                task_version=task_version,
                lease_expires_at=stored_attempt.lease_expires_at,
            )
        return renewal

    @staticmethod
    def _validate_result_contract(
        task: SystemEvaluationTask,
        attempt: SystemEvaluationAttempt,
        input: SystemEvaluationResultInput,
    ) -> None:
        snapshot = dict(task.analysis_config_snapshot)
        versions = (
            ("adapter_id", input.adapter_id, attempt.adapter_id),
            ("adapter_version", input.adapter_version, attempt.adapter_version),
            (
                "scoring_rule_version",
                input.scoring_rule_version,
                attempt.scoring_rule_version,
            ),
            (
                "result_schema_version",
                input.result_schema_version,
                attempt.result_schema_version,
            ),
        )
        if any(
            snapshot.get(key) != supplied or stored != supplied
            for key, supplied, stored in versions
        ):
            raise EvaluationResultInvalid("evaluation result is invalid")
        validate_result_content(
            snapshot,
            fields=input.fields,
            confidences=input.confidences,
            missing_fields=input.missing_fields,
            warnings=[item.model_dump(mode="python") for item in input.warnings],
            components=[
                item.model_dump(mode="python") for item in input.components
            ],
            score=input.score,
            grade=input.grade,
            advice=input.advice,
            require_complete=True,
        )

    def _finished_result_replay(
        self,
        *,
        task: SystemEvaluationTask | None,
        attempt: SystemEvaluationAttempt | None,
        task_version: int,
        worker_identity: str,
        lease_token: str,
        occurred_at: datetime,
        idempotency_digest: str,
    ) -> EvaluationResultSubmission | None:
        if attempt is None or attempt.status != "succeeded":
            return None
        if (
            task is None
            or not self._attempt_credentials_match(
                attempt,
                task_version=task_version,
                worker_identity=worker_identity,
                lease_token=lease_token,
                occurred_at=occurred_at,
            )
        ):
            raise EvaluationLeaseInvalid("evaluation lease is invalid")
        if attempt.result_idempotency_digest is None or not hmac.compare_digest(
            attempt.result_idempotency_digest,
            idempotency_digest,
        ):
            raise EvaluationIdempotencyConflict(
                "evaluation result idempotency conflict"
            )
        event = self.db.scalar(
            select(SystemEvaluationEvent)
            .where(
                SystemEvaluationEvent.task_id == task.id,
                SystemEvaluationEvent.event_type == "worker_result_submitted",
                SystemEvaluationEvent.result_version_id.is_not(None),
                SystemEvaluationEvent.event_metadata["attempt"].as_integer()
                == attempt.attempt,
            )
            .order_by(SystemEvaluationEvent.sequence)
            .limit(1)
        )
        if event is None or event.result_version_id is None:
            raise EvaluationLeaseInvalid("evaluation lease is invalid")
        result = self.db.get(SystemEvaluationResultVersion, event.result_version_id)
        original_task_version = event.event_metadata.get("task_version")
        if (
            result is None
            or result.task_id != task.id
            or event.to_status != "needs_review"
            or not isinstance(original_task_version, int)
            or isinstance(original_task_version, bool)
        ):
            raise EvaluationLeaseInvalid("evaluation lease is invalid")
        return EvaluationResultSubmission(
            task_id=task.id,
            result_id=result.id,
            result_version=result.version,
            task_status="needs_review",
            task_version=original_task_version,
        )

    def submit_result(
        self,
        *,
        task_id: UUID,
        attempt: int,
        task_version: int,
        worker_identity: str,
        lease_token: str,
        input: SystemEvaluationResultInput,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationResultSubmission:
        self._require_aware(occurred_at)
        self._require_worker_identity(worker_identity)
        idempotency_digest = self._result_idempotency_digest(input)
        with write_transaction(self.db):
            task = self.db.scalar(
                select(SystemEvaluationTask)
                .where(SystemEvaluationTask.id == task_id)
                .with_for_update()
            )
            stored_attempt = self.db.scalar(
                select(SystemEvaluationAttempt)
                .where(
                    SystemEvaluationAttempt.task_id == task_id,
                    SystemEvaluationAttempt.attempt == attempt,
                )
                .with_for_update()
            )
            replay = self._finished_result_replay(
                task=task,
                attempt=stored_attempt,
                task_version=task_version,
                worker_identity=worker_identity,
                lease_token=lease_token,
                occurred_at=occurred_at,
                idempotency_digest=idempotency_digest,
            )
            if replay is not None:
                return replay
            active_task, active_attempt = self._active_lease(
                task_id=task_id,
                attempt_number=attempt,
                task_version=task_version,
                worker_identity=worker_identity,
                lease_token=lease_token,
                occurred_at=occurred_at,
            )
            self._validate_result_contract(active_task, active_attempt, input)
            result_version = int(
                self.db.scalar(
                    select(
                        func.coalesce(
                            func.max(SystemEvaluationResultVersion.version),
                            0,
                        )
                        + 1
                    ).where(SystemEvaluationResultVersion.task_id == task_id)
                )
                or 1
            )
            result = SystemEvaluationResultVersion(
                task_id=task_id,
                version=result_version,
                source="automatic",
                fields=dict(input.fields),
                confidences=dict(input.confidences),
                warnings=[item.model_dump(mode="json") for item in input.warnings],
                components=[
                    item.model_dump(mode="json") for item in input.components
                ],
                missing_fields=list(input.missing_fields),
                score=Decimal(str(input.score)),
                grade=input.grade,
                advice=input.advice,
                adapter_id=input.adapter_id,
                adapter_version=input.adapter_version,
                scoring_rule_version=input.scoring_rule_version,
                result_schema_version=input.result_schema_version,
                created_by_user_id=None,
                previous_version_id=active_task.current_result_version_id,
                review_decision="pending",
            )
            self.db.add(result)
            self.db.flush()

            active_attempt.status = "succeeded"
            active_attempt.completed_at = occurred_at
            active_attempt.result_idempotency_digest = idempotency_digest
            active_task.status = "needs_review"
            active_task.current_result_version_id = result.id
            active_task.lock_version += 1
            self.db.add(
                SystemEvaluationEvent(
                    task_id=active_task.id,
                    sequence=self._next_event_sequence(active_task.id),
                    event_type="worker_result_submitted",
                    from_status="processing",
                    to_status="needs_review",
                    result_version_id=result.id,
                    event_metadata={
                        "attempt": attempt,
                        "task_version": active_task.lock_version,
                        "result_version": result.version,
                    },
                    occurred_at=occurred_at,
                )
            )
            AuditService(self.db).append(
                actor_user_id=None,
                action="evaluation.task.result_submitted",
                resource_type="system_evaluation_task",
                resource_id=active_task.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "task_id": str(active_task.id),
                    "attempt_id": str(active_attempt.id),
                    "result_version_id": str(result.id),
                    "attempt": attempt,
                    "task_version": active_task.lock_version,
                    "result_version": result.version,
                    "task_status": "needs_review",
                    "attempt_status": "succeeded",
                    "evaluation_action": "submit_result",
                    "adapter_version": input.adapter_version,
                    "scoring_rule_version": input.scoring_rule_version,
                    "result_schema_version": input.result_schema_version,
                },
            )
            self.db.flush()
            submission = EvaluationResultSubmission(
                task_id=active_task.id,
                result_id=result.id,
                result_version=result.version,
                task_status=active_task.status,
                task_version=active_task.lock_version,
            )
        return submission

    def submit_failure(
        self,
        *,
        task_id: UUID,
        attempt: int,
        task_version: int,
        worker_identity: str,
        lease_token: str,
        input: SystemEvaluationFailureInput,
        occurred_at: datetime,
        request_id: str,
    ) -> EvaluationFailureSubmission:
        self._require_aware(occurred_at)
        self._require_worker_identity(worker_identity)
        retryable = input.error_class in _RETRYABLE_FAILURES
        with write_transaction(self.db):
            task, stored_attempt = self._active_lease(
                task_id=task_id,
                attempt_number=attempt,
                task_version=task_version,
                worker_identity=worker_identity,
                lease_token=lease_token,
                occurred_at=occurred_at,
            )
            stored_attempt.status = "failed"
            stored_attempt.completed_at = occurred_at
            stored_attempt.error_class = input.error_class
            stored_attempt.retryable = retryable
            stored_attempt.safe_error_summary = input.safe_error_summary
            stored_attempt.trace_id = input.trace_id
            stored_attempt.result_idempotency_digest = (
                self._failure_idempotency_digest(input)
            )

            if retryable and attempt < 3:
                final_status = "queued"
                available_at = occurred_at + timedelta(
                    seconds=30 * 2 ** (attempt - 1)
                )
                failure_code = None
            else:
                final_status = "manual_input"
                available_at = None
                failure_code = (
                    "automatic_retries_exhausted"
                    if retryable
                    else input.error_class
                )

            task.status = final_status
            task.available_at = available_at
            task.failure_code = failure_code
            task.manual_queue_reason = failure_code
            task.lock_version += 1
            sequence = self._next_event_sequence(task.id)
            self.db.add_all(
                [
                    SystemEvaluationEvent(
                        task_id=task.id,
                        sequence=sequence,
                        event_type="worker_failure_recorded",
                        from_status="processing",
                        to_status="failed",
                        event_metadata={
                            "attempt": attempt,
                            "task_version": task.lock_version,
                        },
                        occurred_at=occurred_at,
                    ),
                    SystemEvaluationEvent(
                        task_id=task.id,
                        sequence=sequence + 1,
                        event_type=(
                            "worker_retry_scheduled"
                            if final_status == "queued"
                            else "worker_manual_input_required"
                        ),
                        from_status="failed",
                        to_status=final_status,
                        event_metadata={
                            "attempt": attempt,
                            "task_version": task.lock_version,
                        },
                        occurred_at=occurred_at,
                    ),
                ]
            )
            AuditService(self.db).append(
                actor_user_id=None,
                action="evaluation.task.failure_recorded",
                resource_type="system_evaluation_task",
                resource_id=task.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "task_id": str(task.id),
                    "attempt_id": str(stored_attempt.id),
                    "attempt": attempt,
                    "task_version": task.lock_version,
                    "task_status": final_status,
                    "attempt_status": "failed",
                    "evaluation_action": "record_failure",
                },
            )
            self.db.flush()
            submission = EvaluationFailureSubmission(
                task_id=task.id,
                attempt=attempt,
                task_status=final_status,
                task_version=task.lock_version,
                retryable=retryable,
                available_at=available_at,
                failure_code=failure_code,
            )
        return submission
