from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.analysis_profile_service import AnalysisProfileService
from app.evaluations.errors import EvaluationResourceNotFound, MediaValidationFailed
from app.evaluations.idempotency import (
    EvaluationIdempotencyDigests,
    EvaluationIdempotencyService,
)
from app.evaluations.media_models import EvaluationMediaAsset
from app.evaluations.media_service import EvaluationMediaService
from app.evaluations.media_store import PrivateMediaStore, ValidatedImage
from app.evaluations.task_access import (
    AuthorizedEvaluationTarget,
    EvaluationTaskAccessService,
)
from app.evaluations.task_models import (
    SystemEvaluationEvent,
    SystemEvaluationTask,
    SystemEvaluationTaskMedia,
    SystemEvaluationUploadSession,
    SystemEvaluationUploadSessionMedia,
)
from app.evaluations.upload_cleanup import drain_quarantine_cleanup, enqueue_quarantine_cleanup
from app.evaluations.upload_service import EvaluationUploadService


@dataclass(frozen=True)
class EvaluationTaskTarget:
    student_person_id: UUID | None
    enrollment_id: UUID | None
    cohort_id: UUID | None
    class_id: UUID | None
    upload_window_start: datetime
    upload_window_end: datetime

    @property
    def is_confirmed(self) -> bool:
        values = (
            self.student_person_id,
            self.enrollment_id,
            self.cohort_id,
            self.class_id,
        )
        if all(value is None for value in values):
            return False
        if all(value is not None for value in values):
            return True
        raise ValueError("evaluation task target must be complete")


@dataclass(frozen=True)
class EvaluationTaskRecord:
    id: UUID
    public_number: str
    status: str
    lock_version: int


class EvaluationTaskService:
    def __init__(
        self,
        db: Session,
        *,
        store: PrivateMediaStore,
        media_service: EvaluationMediaService | None = None,
        idempotency_service: EvaluationIdempotencyService | None = None,
        access_service: EvaluationTaskAccessService | None = None,
    ) -> None:
        self.db = db
        self.store = store
        self.media_service = media_service or EvaluationMediaService(db, store=store)
        self.idempotency_service = idempotency_service
        self.access_service = access_service or EvaluationTaskAccessService(db)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")

    @staticmethod
    def _profile_snapshot(profile: ProjectAnalysisProfile) -> dict[str, object]:
        return {
            "mode": profile.mode,
            "adapter_id": profile.adapter_id,
            "adapter_version": profile.adapter_version,
            "scoring_rule_version": profile.scoring_rule_version,
            "required_fields": list(profile.required_fields),
            "media_slots": list(profile.media_slots),
            "result_schema_version": profile.result_schema_version,
            "confidence_thresholds": dict(profile.confidence_thresholds),
            "auto_publish_warning_levels": list(
                profile.auto_publish_warning_levels
            ),
            "publish_mode": profile.publish_mode,
        }

    @staticmethod
    def _final_status(
        *,
        target_confirmed: bool,
        profile: ProjectAnalysisProfile,
    ) -> str:
        if not target_confirmed:
            return "pending_match"
        return "queued" if profile.mode == "automatic" else "manual_input"

    def _cancel_failed_promotion(
        self,
        upload_session_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> None:
        quarantine_keys: list[str] = []
        self.store.verify_store_identity()
        with write_transaction(self.db):
            upload_session = self.db.scalar(
                select(SystemEvaluationUploadSession)
                .where(
                    SystemEvaluationUploadSession.id == upload_session_id,
                    SystemEvaluationUploadSession.created_by_user_id
                    == actor_user_id,
                )
                .with_for_update()
            )
            if upload_session is None or upload_session.status != "open":
                return
            slots = list(
                self.db.scalars(
                    select(SystemEvaluationUploadSessionMedia)
                    .where(
                        SystemEvaluationUploadSessionMedia.session_id
                        == upload_session_id
                    )
                    .with_for_update()
                )
            )
            quarantine_keys = [slot.quarantine_storage_key for slot in slots]
            enqueue_quarantine_cleanup(self.db, quarantine_keys, occurred_at=occurred_at)
            for slot in slots:
                self.db.delete(slot)
            upload_session.status = "cancelled"
            self.db.flush()
        drain_quarantine_cleanup(self.db, self.store, occurred_at=occurred_at)

    def _validated_images(
        self,
        slots: list[SystemEvaluationUploadSessionMedia],
    ) -> list[tuple[str, ValidatedImage]]:
        self.store.verify_store_identity()
        return [
            (
                slot.slot_key,
                self.store.resume_staged(
                    slot.quarantine_storage_key,
                    sha256=slot.sha256,
                    mime_type=cast(
                        Literal["image/jpeg", "image/png", "image/webp"],
                        slot.mime_type,
                    ),
                    extension=cast(
                        Literal["jpg", "png", "webp"],
                        slot.canonical_extension,
                    ),
                    byte_size=slot.size_bytes,
                    width=slot.width_pixels,
                    height=slot.height_pixels,
                ),
            )
            for slot in slots
        ]

    @staticmethod
    def _request_matches_target(
        request: EvaluationTaskTarget,
        target: AuthorizedEvaluationTarget,
    ) -> bool:
        return (
            request.student_person_id == target.student_person_id
            and request.enrollment_id == target.enrollment_id
            and request.cohort_id == target.cohort_id
            and request.class_id == target.class_id
        )

    def _authorize_task_target(
        self,
        upload_session: SystemEvaluationUploadSession,
        *,
        actor_user_id: UUID,
        requested_target: EvaluationTaskTarget,
        occurred_at: datetime,
    ) -> tuple[AuthorizedEvaluationTarget | None, AuthorizedEvaluationTarget]:
        request_confirmed = requested_target.is_confirmed
        if upload_session.enrollment_id is not None:
            confirmed_target = self.access_service.require_upload_target(
                account_id=actor_user_id,
                enrollment_id=upload_session.enrollment_id,
                project_id=upload_session.project_id,
                occurred_at=occurred_at,
            )
            if confirmed_target.student_person_id != upload_session.student_person_id:
                raise EvaluationResourceNotFound("evaluation resource not found")
            if request_confirmed and not self._request_matches_target(
                requested_target,
                confirmed_target,
            ):
                raise EvaluationResourceNotFound("evaluation resource not found")
            authorization_target = confirmed_target
        elif request_confirmed:
            assert requested_target.enrollment_id is not None
            confirmed_target = self.access_service.require_upload_target(
                account_id=actor_user_id,
                enrollment_id=requested_target.enrollment_id,
                project_id=upload_session.project_id,
                occurred_at=occurred_at,
            )
            if not self._request_matches_target(requested_target, confirmed_target):
                raise EvaluationResourceNotFound("evaluation resource not found")
            authorization_target = confirmed_target
        else:
            confirmed_target = None
            authorization_target = self.access_service.require_upload_capability(
                account_id=actor_user_id,
                project_id=upload_session.project_id,
                occurred_at=occurred_at,
            )
        try:
            self.access_service.require_delegated_upload_reason(
                authorization_target,
                upload_session.delegated_upload_reason,
            )
        except ValueError:
            raise EvaluationResourceNotFound("evaluation resource not found") from None
        return confirmed_target, authorization_target

    def create_task(
        self,
        upload_session_id: UUID,
        *,
        actor_user_id: UUID,
        target: EvaluationTaskTarget,
        occurred_at: datetime,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> EvaluationTaskRecord:
        self._require_aware(occurred_at)
        idempotency_digests: EvaluationIdempotencyDigests | None = None
        if idempotency_key is not None and self.idempotency_service is None:
            raise RuntimeError("evaluation idempotency service is required")
        promotion_started = False
        try:
            with write_transaction(self.db):
                upload_session = self.db.scalar(
                    select(SystemEvaluationUploadSession)
                    .where(
                        SystemEvaluationUploadSession.id == upload_session_id,
                        SystemEvaluationUploadSession.created_by_user_id
                        == actor_user_id,
                    )
                    .with_for_update()
                )
                if upload_session is None:
                    raise EvaluationResourceNotFound(
                        "evaluation resource not found"
                    )
                confirmed_target, authorization_target = (
                    self._authorize_task_target(
                        upload_session,
                        actor_user_id=actor_user_id,
                        requested_target=target,
                        occurred_at=occurred_at,
                    )
                )
                profile = AnalysisProfileService(self.db).current_at(
                    upload_session.project_id,
                    occurred_at=(
                        upload_session.expires_at
                        - EvaluationUploadService.SESSION_LIFETIME
                    ),
                )
                if profile is None:
                    raise EvaluationResourceNotFound(
                        "evaluation resource not found"
                    )
                if idempotency_key is not None:
                    assert self.idempotency_service is not None
                    idempotency_digests = self.idempotency_service.prepare(
                        scope="evaluation.task.create",
                        actor_user_id=actor_user_id,
                        key=idempotency_key,
                        payload={
                            "upload_session_id": str(upload_session_id),
                            "student_person_id": (
                                str(confirmed_target.student_person_id)
                                if confirmed_target is not None
                                else None
                            ),
                            "enrollment_id": (
                                str(confirmed_target.enrollment_id)
                                if confirmed_target is not None
                                else None
                            ),
                            "cohort_id": (
                                str(confirmed_target.cohort_id)
                                if confirmed_target is not None
                                else None
                            ),
                            "class_id": (
                                str(confirmed_target.class_id)
                                if confirmed_target is not None
                                else None
                            ),
                        },
                    )
                if idempotency_digests is not None:
                    assert self.idempotency_service is not None
                    existing_resource_id = (
                        self.idempotency_service.existing_resource_id(
                            idempotency_digests,
                            occurred_at=occurred_at,
                        )
                    )
                    if existing_resource_id is not None:
                        existing_task = self.db.get(
                            SystemEvaluationTask,
                            existing_resource_id,
                        )
                        if existing_task is None:
                            raise RuntimeError(
                                "evaluation idempotency resource is missing"
                            )
                        return EvaluationTaskRecord(
                            id=existing_task.id,
                            public_number=existing_task.public_number,
                            status=existing_task.status,
                            lock_version=existing_task.lock_version,
                        )
                if (
                    upload_session.status != "open"
                    or upload_session.expires_at <= occurred_at
                ):
                    raise EvaluationResourceNotFound(
                        "evaluation resource not found"
                    )
                slot_positions, required_slots = (
                    EvaluationUploadService.profile_slot_contract(profile)
                )
                slots = list(
                    self.db.scalars(
                        select(SystemEvaluationUploadSessionMedia)
                        .where(
                            SystemEvaluationUploadSessionMedia.session_id
                            == upload_session_id
                        )
                        .order_by(SystemEvaluationUploadSessionMedia.position)
                        .with_for_update()
                    )
                )
                uploaded_slot_keys = {slot.slot_key for slot in slots}
                if (
                    not required_slots <= uploaded_slot_keys
                    or not uploaded_slot_keys <= set(slot_positions)
                    or any(
                        slot.position != slot_positions[slot.slot_key]
                        for slot in slots
                    )
                ):
                    raise MediaValidationFailed("slot_contract_invalid")
                images = self._validated_images(slots)
                promotion_started = True
                registered = self.media_service.register_validated_images_in_transaction(
                    images,
                    uploaded_by_user_id=actor_user_id,
                    project_id=upload_session.project_id,
                )

                final_status = self._final_status(
                    target_confirmed=confirmed_target is not None,
                    profile=profile,
                )
                task = SystemEvaluationTask(
                    public_number=f"EV-{uuid4().hex[:20].upper()}",
                    upload_session_id=upload_session.id,
                    created_by_user_id=actor_user_id,
                    student_person_id=(
                        confirmed_target.student_person_id
                        if confirmed_target is not None
                        else None
                    ),
                    enrollment_id=(
                        confirmed_target.enrollment_id
                        if confirmed_target is not None
                        else None
                    ),
                    cohort_id=(
                        confirmed_target.cohort_id
                        if confirmed_target is not None
                        else None
                    ),
                    project_id=upload_session.project_id,
                    class_id=(
                        confirmed_target.class_id
                        if confirmed_target is not None
                        else None
                    ),
                    analysis_profile_id=profile.id,
                    analysis_profile_version=profile.version,
                    analysis_config_snapshot=self._profile_snapshot(profile),
                    upload_window_start=authorization_target.upload_window.starts_at,
                    upload_window_end=authorization_target.upload_window.ends_at,
                    status="uploaded",
                    lock_version=1,
                    current_attempt=0,
                    automatic_attempts=0,
                    matched_at=occurred_at if confirmed_target is not None else None,
                    matched_by_user_id=(
                        actor_user_id if confirmed_target is not None else None
                    ),
                )
                self.db.add(task)
                self.db.flush()

                registered_by_slot = {asset.slot_key: asset for asset in registered}
                assets = list(
                    self.db.scalars(
                        select(EvaluationMediaAsset).where(
                            EvaluationMediaAsset.id.in_(
                                [asset.id for asset in registered]
                            )
                        )
                    )
                )
                for asset in assets:
                    asset.delegated_upload_reason = (
                        upload_session.delegated_upload_reason
                    )
                    if confirmed_target is not None:
                        asset.student_person_id = confirmed_target.student_person_id
                        asset.enrollment_id = confirmed_target.enrollment_id
                        asset.class_id = confirmed_target.class_id
                        asset.matched_at = occurred_at
                        asset.matched_by_user_id = actor_user_id
                        asset.match_reason = "upload_target_confirmed"
                self.db.flush()
                for slot in slots:
                    registered_asset = registered_by_slot[slot.slot_key]
                    self.db.add(
                        SystemEvaluationTaskMedia(
                            task_id=task.id,
                            media_id=registered_asset.id,
                            position=slot.position,
                            slot_key=slot.slot_key,
                        )
                    )
                self.db.flush()

                self.db.add_all(
                    [
                        SystemEvaluationEvent(
                            task_id=task.id,
                            sequence=1,
                            event_type="status_changed",
                            from_status="uploaded",
                            to_status="identifying",
                            actor_user_id=actor_user_id,
                            event_metadata={},
                            occurred_at=occurred_at,
                        ),
                        SystemEvaluationEvent(
                            task_id=task.id,
                            sequence=2,
                            event_type="status_changed",
                            from_status="identifying",
                            to_status=final_status,
                            actor_user_id=actor_user_id,
                            event_metadata={},
                            occurred_at=occurred_at,
                        ),
                    ]
                )
                task.status = final_status
                upload_session.status = "committed"
                upload_session.committed_at = occurred_at
                enqueue_quarantine_cleanup(
                    self.db, [slot.quarantine_storage_key for slot in slots], occurred_at=occurred_at,
                )
                for slot in slots:
                    self.db.delete(slot)
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="evaluation.task.create",
                    resource_type="system_evaluation_task",
                    resource_id=task.id,
                    purpose=None,
                    outcome="allowed",
                    request_id=request_id,
                    metadata={
                        "upload_session_id": str(upload_session.id),
                        "task_id": str(task.id),
                        "task_status": final_status,
                        "evaluation_action": "create",
                        "task_version": 1,
                        "media_count": len(slots),
                    },
                )
                if idempotency_digests is not None:
                    assert self.idempotency_service is not None
                    self.idempotency_service.record(
                        idempotency_digests,
                        resource_id=task.id,
                        occurred_at=occurred_at,
                    )
                self.db.flush()
                record = EvaluationTaskRecord(
                    id=task.id,
                    public_number=task.public_number,
                    status=task.status,
                    lock_version=task.lock_version,
                )
        except Exception:
            if self.db.in_transaction():
                self.db.rollback()
            if promotion_started:
                self._cancel_failed_promotion(
                    upload_session_id,
                    actor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                )
            raise
        drain_quarantine_cleanup(self.db, self.store, occurred_at=occurred_at)
        return record
