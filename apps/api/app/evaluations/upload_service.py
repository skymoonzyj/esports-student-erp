from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.analysis_profile_service import AnalysisProfileService
from app.evaluations.errors import (
    EvaluationResourceNotFound,
    EvaluationUploadLimitReached,
    MediaValidationFailed,
)
from app.evaluations.media_store import PrivateMediaStore
from app.evaluations.task_access import (
    AuthorizedEvaluationTarget,
    EvaluationTaskAccessService,
)
from app.evaluations.task_models import (
    SystemEvaluationUploadSession,
    SystemEvaluationUploadSessionMedia,
)
from app.evaluations.upload_cleanup import drain_quarantine_cleanup, enqueue_quarantine_cleanup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadSessionRecord:
    id: UUID
    project_id: UUID
    status: str
    expires_at: datetime


@dataclass(frozen=True)
class UploadSlotRecord:
    id: UUID
    session_id: UUID
    position: int
    slot_key: str
    quarantine_storage_key: str
    sha256: str
    mime_type: str
    canonical_extension: str
    size_bytes: int
    width_pixels: int
    height_pixels: int


class EvaluationUploadService:
    SESSION_LIFETIME = timedelta(minutes=30)
    CLEANUP_BATCH_SIZE = 8
    STALE_SESSION_BATCH_SIZE = 50
    QUOTA_LOCK = 420_005_206

    def __init__(
        self,
        db: Session,
        *,
        store: PrivateMediaStore,
        access_service: EvaluationTaskAccessService | None = None,
        max_open_sessions_per_account: int = 5,
        max_open_sessions_total: int = 500,
    ) -> None:
        self.db = db
        self.store = store
        self.access_service = access_service or EvaluationTaskAccessService(db)
        self.max_open_sessions_per_account = max_open_sessions_per_account
        self.max_open_sessions_total = max_open_sessions_total

    def _retry_pending_quarantine_cleanup(self, occurred_at: datetime | None = None) -> None:
        drain_quarantine_cleanup(
            self.db, self.store, occurred_at=occurred_at or datetime.now(UTC),
            limit=self.CLEANUP_BATCH_SIZE,
        )

    def schedule_quarantine_cleanup(self, key: str) -> None:
        with write_transaction(self.db):
            enqueue_quarantine_cleanup(self.db, [key])
        self._retry_pending_quarantine_cleanup()

    def expire_stale_sessions(
        self,
        *,
        created_by_user_id: UUID | None = None,
        project_id: UUID | None = None,
        occurred_at: datetime,
    ) -> int:
        self._require_aware(occurred_at)
        self.store.verify_store_identity()
        quarantine_keys: list[str] = []
        expired_count = 0
        with write_transaction(self.db):
            statement = select(SystemEvaluationUploadSession).where(
                SystemEvaluationUploadSession.status == "open",
                SystemEvaluationUploadSession.expires_at <= occurred_at,
            )
            if created_by_user_id is not None:
                statement = statement.where(
                    SystemEvaluationUploadSession.created_by_user_id == created_by_user_id,
                )
            if project_id is not None:
                statement = statement.where(SystemEvaluationUploadSession.project_id == project_id)
            sessions = list(
                self.db.scalars(
                    statement
                    .order_by(
                        SystemEvaluationUploadSession.expires_at,
                        SystemEvaluationUploadSession.id,
                    )
                    .limit(self.STALE_SESSION_BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
            )
            session_ids = [session.id for session in sessions]
            if session_ids:
                slots = list(
                    self.db.scalars(
                        select(SystemEvaluationUploadSessionMedia)
                        .where(
                            SystemEvaluationUploadSessionMedia.session_id.in_(
                                session_ids
                            )
                        )
                        .with_for_update()
                    )
                )
                quarantine_keys = [slot.quarantine_storage_key for slot in slots]
                enqueue_quarantine_cleanup(self.db, quarantine_keys, occurred_at=occurred_at)
                for slot in slots:
                    self.db.delete(slot)
                for upload_session in sessions:
                    upload_session.status = "expired"
                self.db.flush()
                expired_count = len(sessions)
        self._retry_pending_quarantine_cleanup(occurred_at)
        return expired_count

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    @staticmethod
    def _session_record(value: SystemEvaluationUploadSession) -> UploadSessionRecord:
        return UploadSessionRecord(
            id=value.id,
            project_id=value.project_id,
            status=value.status,
            expires_at=value.expires_at,
        )

    @staticmethod
    def _slot_record(value: SystemEvaluationUploadSessionMedia) -> UploadSlotRecord:
        return UploadSlotRecord(
            id=value.id,
            session_id=value.session_id,
            position=value.position,
            slot_key=value.slot_key,
            quarantine_storage_key=value.quarantine_storage_key,
            sha256=value.sha256,
            mime_type=value.mime_type,
            canonical_extension=value.canonical_extension,
            size_bytes=value.size_bytes,
            width_pixels=value.width_pixels,
            height_pixels=value.height_pixels,
        )

    @staticmethod
    def profile_slot_contract(
        profile: ProjectAnalysisProfile,
    ) -> tuple[dict[str, int], set[str]]:
        positions: dict[str, int] = {}
        required: set[str] = set()
        for position, definition in enumerate(profile.media_slots, start=1):
            if not isinstance(definition, dict):
                raise MediaValidationFailed("slot_contract_invalid")
            key = definition.get("key")
            is_required = definition.get("required")
            if (
                not isinstance(key, str)
                or not key
                or key in positions
                or not isinstance(is_required, bool)
            ):
                raise MediaValidationFailed("slot_contract_invalid")
            positions[key] = position
            if is_required:
                required.add(key)
        if not positions or len(positions) > 3:
            raise MediaValidationFailed("slot_contract_invalid")
        return positions, required

    def _slot_position(
        self,
        upload_session: SystemEvaluationUploadSession,
        *,
        slot_key: str,
    ) -> int:
        profile = AnalysisProfileService(self.db).current_at(
            upload_session.project_id,
            occurred_at=upload_session.expires_at - self.SESSION_LIFETIME,
        )
        if profile is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        positions, _required = self.profile_slot_contract(profile)
        try:
            return positions[slot_key]
        except KeyError:
            raise MediaValidationFailed("slot_contract_invalid") from None

    def _require_session_authorization(
        self,
        upload_session: SystemEvaluationUploadSession,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> AuthorizedEvaluationTarget:
        if upload_session.enrollment_id is not None:
            target = self.access_service.require_upload_target(
                account_id=actor_user_id,
                enrollment_id=upload_session.enrollment_id,
                project_id=upload_session.project_id,
                occurred_at=occurred_at,
            )
            if target.student_person_id != upload_session.student_person_id:
                raise EvaluationResourceNotFound("evaluation resource not found")
        else:
            target = self.access_service.require_upload_capability(
                account_id=actor_user_id,
                project_id=upload_session.project_id,
                occurred_at=occurred_at,
            )
        try:
            self.access_service.require_delegated_upload_reason(
                target,
                upload_session.delegated_upload_reason,
            )
        except ValueError:
            raise EvaluationResourceNotFound("evaluation resource not found") from None
        return target

    def create_session(
        self,
        *,
        project_id: UUID,
        created_by_user_id: UUID,
        occurred_at: datetime,
        student_person_id: UUID | None = None,
        enrollment_id: UUID | None = None,
        delegated_upload_reason: str | None = None,
    ) -> UploadSessionRecord:
        self._require_aware(occurred_at)
        self._retry_pending_quarantine_cleanup(occurred_at)
        if (student_person_id is None) != (enrollment_id is None):
            raise EvaluationResourceNotFound("evaluation resource not found")
        if enrollment_id is None:
            target = self.access_service.require_upload_capability(
                account_id=created_by_user_id,
                project_id=project_id,
                occurred_at=occurred_at,
            )
        else:
            target = self.access_service.require_upload_target(
                account_id=created_by_user_id,
                enrollment_id=enrollment_id,
                project_id=project_id,
                occurred_at=occurred_at,
            )
            if target.student_person_id != student_person_id:
                raise EvaluationResourceNotFound("evaluation resource not found")
        delegated_upload_reason = (
            self.access_service.require_delegated_upload_reason(
                target,
                delegated_upload_reason,
            )
        )
        self.expire_stale_sessions(
            created_by_user_id=created_by_user_id,
            project_id=project_id,
            occurred_at=occurred_at,
        )
        with write_transaction(self.db):
            self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": self.QUOTA_LOCK})
            active = select(SystemEvaluationUploadSession.id).where(
                SystemEvaluationUploadSession.status == "open",
                SystemEvaluationUploadSession.expires_at > occurred_at,
            )
            total = self.db.scalar(select(func.count()).select_from(active.subquery())) or 0
            account_total = self.db.scalar(select(func.count()).select_from(
                active.where(SystemEvaluationUploadSession.created_by_user_id == created_by_user_id)
                .subquery()
            )) or 0
            if total >= self.max_open_sessions_total or account_total >= self.max_open_sessions_per_account:
                raise EvaluationUploadLimitReached("open upload session quota reached")
            upload_session = SystemEvaluationUploadSession(
                created_by_user_id=created_by_user_id,
                project_id=project_id,
                student_person_id=student_person_id,
                enrollment_id=enrollment_id,
                status="open",
                delegated_upload_reason=delegated_upload_reason,
                expires_at=occurred_at + self.SESSION_LIFETIME,
            )
            self.db.add(upload_session)
            self.db.flush()
            record = self._session_record(upload_session)
        return record

    def _open_session_for_actor(
        self,
        session_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        lock: bool,
    ) -> SystemEvaluationUploadSession | None:
        statement = select(SystemEvaluationUploadSession).where(
            SystemEvaluationUploadSession.id == session_id,
            SystemEvaluationUploadSession.created_by_user_id == actor_user_id,
            SystemEvaluationUploadSession.status == "open",
            SystemEvaluationUploadSession.expires_at > occurred_at,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.db.scalar(statement)

    def put_slot(
        self,
        session_id: UUID,
        *,
        slot_key: str,
        stream: BinaryIO,
        declared_filename: str,
        declared_mime_type: str,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> UploadSlotRecord:
        self._require_aware(occurred_at)
        self._retry_pending_quarantine_cleanup(occurred_at)
        upload_session = self._open_session_for_actor(
            session_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            lock=False,
        )
        if upload_session is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        self._require_session_authorization(
            upload_session,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        position = self._slot_position(upload_session, slot_key=slot_key)

        self.store.verify_store_identity()
        image = self.store.stage(
            stream,
            declared_filename=declared_filename,
            declared_mime_type=declared_mime_type,
        )
        quarantine_storage_key = self.store.quarantine_storage_key(image)
        old_quarantine_storage_key: str | None = None
        try:
            with write_transaction(self.db):
                locked_session = self._open_session_for_actor(
                    session_id,
                    actor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                    lock=True,
                )
                if locked_session is None:
                    raise EvaluationResourceNotFound(
                        "evaluation resource not found"
                    )
                self._require_session_authorization(
                    locked_session,
                    actor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                )
                position = self._slot_position(
                    locked_session,
                    slot_key=slot_key,
                )
                existing = self.db.scalar(
                    select(SystemEvaluationUploadSessionMedia)
                    .where(
                        SystemEvaluationUploadSessionMedia.session_id == session_id,
                        SystemEvaluationUploadSessionMedia.slot_key == slot_key,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    old_quarantine_storage_key = existing.quarantine_storage_key
                    enqueue_quarantine_cleanup(
                        self.db, [old_quarantine_storage_key], occurred_at=occurred_at,
                    )
                    self.db.delete(existing)
                    self.db.flush()
                slot = SystemEvaluationUploadSessionMedia(
                    session_id=session_id,
                    position=position,
                    slot_key=slot_key,
                    quarantine_storage_key=quarantine_storage_key,
                    sha256=image.sha256,
                    mime_type=image.mime_type,
                    canonical_extension=image.extension,
                    size_bytes=image.byte_size,
                    width_pixels=image.width,
                    height_pixels=image.height,
                )
                self.db.add(slot)
                self.db.flush()
                record = self._slot_record(slot)
        except Exception:
            if self.db.in_transaction():
                self.db.rollback()
            try:
                self.store.discard_staged(image)
            except Exception:  # noqa: BLE001 -- keep the original business failure
                try:
                    with write_transaction(self.db):
                        enqueue_quarantine_cleanup(
                            self.db, [quarantine_storage_key], occurred_at=occurred_at,
                        )
                except Exception:  # noqa: BLE001 -- log no raw media path or database details
                    logger.error("evaluation_upload_cleanup_enqueue_failed")
            raise

        if (
            old_quarantine_storage_key is not None
            and old_quarantine_storage_key != quarantine_storage_key
        ):
            self._retry_pending_quarantine_cleanup(occurred_at)
        return record

    def delete_slot(
        self,
        session_id: UUID,
        *,
        slot_key: str,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self._require_aware(occurred_at)
        self._retry_pending_quarantine_cleanup(occurred_at)
        if self._open_session_for_actor(
            session_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            lock=False,
        ) is None:
            raise EvaluationResourceNotFound("evaluation resource not found")

        upload_session = self._open_session_for_actor(
            session_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            lock=False,
        )
        if upload_session is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        self._require_session_authorization(
            upload_session,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )

        self.store.verify_store_identity()
        quarantine_storage_key: str | None = None
        try:
            with write_transaction(self.db):
                locked_session = self._open_session_for_actor(
                    session_id,
                    actor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                    lock=True,
                )
                if locked_session is None:
                    raise EvaluationResourceNotFound(
                        "evaluation resource not found"
                    )
                self._require_session_authorization(
                    locked_session,
                    actor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                )
                slot = self.db.scalar(
                    select(SystemEvaluationUploadSessionMedia)
                    .where(
                        SystemEvaluationUploadSessionMedia.session_id == session_id,
                        SystemEvaluationUploadSessionMedia.slot_key == slot_key,
                    )
                    .with_for_update()
                )
                if slot is None:
                    raise EvaluationResourceNotFound(
                        "evaluation resource not found"
                    )
                quarantine_storage_key = slot.quarantine_storage_key
                enqueue_quarantine_cleanup(self.db, [quarantine_storage_key], occurred_at=occurred_at)
                self.db.delete(slot)
                self.db.flush()
        except Exception:
            if self.db.in_transaction():
                self.db.rollback()
            raise

        if quarantine_storage_key is not None:
            self._retry_pending_quarantine_cleanup(occurred_at)
