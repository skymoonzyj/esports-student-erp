from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import event, select
from sqlalchemy.engine import Connection, Transaction
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, SessionTransaction

from app.audit.service import AuditOutcome, AuditService
from app.core.transactions import write_transaction
from app.evaluations.access import EvaluationAccessService, EvaluationResource
from app.evaluations.errors import MediaAssetNotFound, MediaValidationFailed
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.media_store import (
    MediaSettlementBudget,
    PrivateMediaStore,
    StoredImageFiles,
    ValidatedImage,
)
from app.teaching.models import TeachingClass


@dataclass(frozen=True)
class RegisteredMediaAsset:
    id: UUID
    project_id: UUID


@dataclass(frozen=True)
class RegisteredSlotMediaAsset:
    id: UUID
    project_id: UUID
    slot_key: str


@dataclass(frozen=True)
class ProtectedMediaFile:
    path: Path
    mime_type: str
    headers: dict[str, str]

    def close(self) -> None:
        close_path = getattr(self.path, "close", None)
        if callable(close_path):
            close_path()


@dataclass(frozen=True)
class MediaStorageStatus:
    ready: bool
    media_writable: bool
    quarantine_writable: bool
    max_bytes: int
    max_pixels: int
    preview_max_edge: int


@dataclass
class _PendingMediaFiles:
    store: PrivateMediaStore
    files: StoredImageFiles
    committed: bool | None = None
    settled: bool = False

    def remove(self, *, budget: MediaSettlementBudget | None = None) -> None:
        if self.settled:
            return
        if self.committed is True:
            self.release(budget=budget)
            return
        self.committed = False
        self.store.remove_if_created(self.files, budget=budget)
        self.settled = True

    def release(self, *, budget: MediaSettlementBudget | None = None) -> None:
        if self.settled:
            return
        if self.committed is False:
            self.remove(budget=budget)
            return
        self.committed = True
        self.store.release(self.files, budget=budget)
        self.settled = True


@dataclass
class _MediaTransactionCompensations:
    pending: dict[SessionTransaction, list[_PendingMediaFiles]] = field(default_factory=dict)
    committed: set[SessionTransaction] = field(default_factory=set)


_MEDIA_TRANSACTION_COMPENSATIONS = "app.evaluations.media_service.transaction_compensations"


def _transaction_compensations(db: Session) -> _MediaTransactionCompensations:
    current = db.info.get(_MEDIA_TRANSACTION_COMPENSATIONS)
    if isinstance(current, _MediaTransactionCompensations):
        return current
    compensations = _MediaTransactionCompensations()
    db.info[_MEDIA_TRANSACTION_COMPENSATIONS] = compensations
    event.listen(
        db,
        "after_commit",
        _mark_media_transaction_committed,
        insert=True,
    )
    event.listen(db, "after_begin", _retry_media_transaction_settlements)
    event.listen(db, "after_transaction_end", _settle_media_transaction)
    return compensations


def _mark_media_transaction_committed(db: Session) -> None:
    current = db.info.get(_MEDIA_TRANSACTION_COMPENSATIONS)
    if not isinstance(current, _MediaTransactionCompensations):
        return
    transaction = db.get_nested_transaction() or db.get_transaction()
    if transaction is None:
        return
    pending = current.pending.get(transaction)
    if pending is None:
        return
    current.committed.add(transaction)
    if transaction.parent is None:
        for files in pending:
            files.committed = True


def _settle_all_media_files(
    pending: list[_PendingMediaFiles],
    *,
    committed: bool,
) -> None:
    first_error: Exception | None = None
    budget = MediaSettlementBudget()
    for files in pending:
        try:
            if committed:
                files.release(budget=budget)
            else:
                files.remove(budget=budget)
        except Exception as exc:  # noqa: BLE001 - settle every owned file first
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def _settle_media_transaction(
    db: Session,
    transaction: SessionTransaction,
) -> None:
    current = db.info.get(_MEDIA_TRANSACTION_COMPENSATIONS)
    if not isinstance(current, _MediaTransactionCompensations):
        return
    _settle_media_transaction_entry(current, transaction)


def _settle_media_transaction_entry(
    current: _MediaTransactionCompensations,
    transaction: SessionTransaction,
) -> None:
    pending = current.pending.get(transaction, [])
    committed = transaction in current.committed
    if committed and transaction.parent is not None:
        current.pending.pop(transaction, None)
        current.committed.discard(transaction)
        current.pending.setdefault(transaction.parent, []).extend(pending)
        return
    _settle_all_media_files(pending, committed=committed)
    current.pending.pop(transaction, None)
    current.committed.discard(transaction)


def _retry_media_transaction_settlements(
    db: Session,
    transaction: SessionTransaction,
    connection: Connection,
) -> None:
    del transaction, connection
    current = db.info.get(_MEDIA_TRANSACTION_COMPENSATIONS)
    if not isinstance(current, _MediaTransactionCompensations):
        return
    for ended_transaction in list(current.pending):
        if ended_transaction.is_active:
            continue
        _settle_media_transaction_entry(current, ended_transaction)


def _track_media_files(
    db: Session,
    *,
    store: PrivateMediaStore,
    files: StoredImageFiles,
) -> _PendingMediaFiles:
    transaction = db.get_nested_transaction() or db.get_transaction()
    if transaction is None:
        raise RuntimeError("media files require an active database transaction")
    pending = _PendingMediaFiles(store=store, files=files)
    _transaction_compensations(db).pending.setdefault(transaction, []).append(pending)
    return pending


def _reject_external_connection_transaction(
    db: Session,
    *,
    store: PrivateMediaStore,
    image: ValidatedImage,
) -> None:
    bind = db.get_bind(mapper=EvaluationMediaBlob)
    if (
        not isinstance(bind, Connection)
        or not bind.in_transaction()
        or _session_owns_connection_transaction(db, bind)
    ):
        return
    store.discard_staged(image)
    raise RuntimeError("media persistence does not support an externally managed transaction")


def _session_owns_connection_transaction(db: Session, bind: Connection) -> bool:
    transaction = db.get_transaction()
    if transaction is None:
        return False
    connection_states = cast(
        dict[object, tuple[Connection, Transaction, bool, bool]],
        getattr(transaction, "_connections", {}),
    )
    state = connection_states.get(bind)
    if state is None:
        return False
    connection_transaction = state[1]
    should_commit = state[2]
    return should_commit and connection_transaction is bind.get_transaction()


class EvaluationMediaService:
    def __init__(self, db: Session, *, store: PrivateMediaStore) -> None:
        self.db = db
        self.store = store

    @staticmethod
    def _constraint_name(exc: IntegrityError) -> str | None:
        value = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        return value if isinstance(value, str) else None

    @staticmethod
    def _blob_is_reusable(blob: EvaluationMediaBlob) -> bool:
        return (
            blob.storage_status == "available"
            and blob.validation_status == "validated"
            and blob.retention_status == "retained"
        )

    def _create_asset(
        self,
        *,
        blob_id: UUID,
        uploaded_by_user_id: UUID,
        project_id: UUID,
    ) -> EvaluationMediaAsset:
        asset = EvaluationMediaAsset(
            blob_id=blob_id,
            uploaded_by_user_id=uploaded_by_user_id,
            project_id=project_id,
        )
        self.db.add(asset)
        self.db.flush()
        return asset

    @staticmethod
    def _registered(asset: EvaluationMediaAsset) -> RegisteredMediaAsset:
        return RegisteredMediaAsset(id=asset.id, project_id=asset.project_id)

    def _register_with_blob(
        self,
        blob: EvaluationMediaBlob,
        *,
        uploaded_by_user_id: UUID,
        project_id: UUID,
    ) -> RegisteredMediaAsset:
        with write_transaction(self.db):
            locked_blob = self.db.scalar(
                select(EvaluationMediaBlob)
                .where(EvaluationMediaBlob.id == blob.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if locked_blob is None or not self._blob_is_reusable(locked_blob):
                raise MediaValidationFailed("content_unavailable")
            asset = self._create_asset(
                blob_id=locked_blob.id,
                uploaded_by_user_id=uploaded_by_user_id,
                project_id=project_id,
            )
        return self._registered(asset)

    def register_validated_image(
        self,
        image: ValidatedImage,
        *,
        uploaded_by_user_id: UUID,
        project_id: UUID,
    ) -> RegisteredMediaAsset:
        _reject_external_connection_transaction(
            self.db,
            store=self.store,
            image=image,
        )
        try:
            existing = self.db.scalar(
                select(EvaluationMediaBlob).where(
                    EvaluationMediaBlob.sha256 == image.sha256,
                )
            )
        except Exception:
            self.store.discard_staged(image)
            raise
        if existing is not None:
            self.store.discard_staged(image)
            if not self._blob_is_reusable(existing):
                raise MediaValidationFailed("content_unavailable")
            return self._register_with_blob(
                existing,
                uploaded_by_user_id=uploaded_by_user_id,
                project_id=project_id,
            )

        blob_id = uuid4()
        try:
            files = self.store.persist(image, blob_id=blob_id)
        except Exception:
            self.store.discard_staged(image)
            raise
        pending_files: _PendingMediaFiles | None = None
        try:
            with write_transaction(self.db):
                blob = EvaluationMediaBlob(
                    id=blob_id,
                    storage_key=files.storage_key,
                    preview_storage_key=files.preview_storage_key,
                    sha256=image.sha256,
                    mime_type=image.mime_type,
                    canonical_extension=image.extension,
                    size_bytes=image.byte_size,
                    width_pixels=image.width,
                    height_pixels=image.height,
                    storage_status="available",
                    validation_status="validated",
                    retention_status="retained",
                )
                self.db.add(blob)
                self.db.flush()
                asset = self._create_asset(
                    blob_id=blob.id,
                    uploaded_by_user_id=uploaded_by_user_id,
                    project_id=project_id,
                )
                self.store.finalize(files)
                pending_files = _track_media_files(
                    self.db,
                    store=self.store,
                    files=files,
                )
            return self._registered(asset)
        except IntegrityError as exc:
            if pending_files is None:
                self.store.remove_if_created(files)
            else:
                pending_files.remove()
            if self._constraint_name(exc) != "uq_evaluation_media_blobs_sha256":
                raise
            winner = self.db.scalar(
                select(EvaluationMediaBlob).where(
                    EvaluationMediaBlob.sha256 == image.sha256,
                )
            )
            if winner is None:
                raise MediaValidationFailed("content_unavailable") from None
            if not self._blob_is_reusable(winner):
                raise MediaValidationFailed("content_unavailable") from None
            return self._register_with_blob(
                winner,
                uploaded_by_user_id=uploaded_by_user_id,
                project_id=project_id,
            )
        except Exception:
            if pending_files is None:
                self.store.remove_if_created(files)
            else:
                pending_files.remove()
            raise

    def _register_validated_image_in_transaction(
        self,
        image: ValidatedImage,
        *,
        uploaded_by_user_id: UUID,
        project_id: UUID,
    ) -> RegisteredMediaAsset:
        existing = self.db.scalar(
            select(EvaluationMediaBlob)
            .where(EvaluationMediaBlob.sha256 == image.sha256)
            .with_for_update()
        )
        if existing is not None:
            self.store.discard_staged(image)
            if not self._blob_is_reusable(existing):
                raise MediaValidationFailed("content_unavailable")
            asset = self._create_asset(
                blob_id=existing.id,
                uploaded_by_user_id=uploaded_by_user_id,
                project_id=project_id,
            )
            return self._registered(asset)

        blob_id = uuid4()
        files = self.store.persist(image, blob_id=blob_id)
        pending_files: _PendingMediaFiles | None = None
        try:
            blob = EvaluationMediaBlob(
                id=blob_id,
                storage_key=files.storage_key,
                preview_storage_key=files.preview_storage_key,
                sha256=image.sha256,
                mime_type=image.mime_type,
                canonical_extension=image.extension,
                size_bytes=image.byte_size,
                width_pixels=image.width,
                height_pixels=image.height,
                storage_status="available",
                validation_status="validated",
                retention_status="retained",
            )
            self.db.add(blob)
            self.db.flush()
            asset = self._create_asset(
                blob_id=blob.id,
                uploaded_by_user_id=uploaded_by_user_id,
                project_id=project_id,
            )
            self.store.finalize(files)
            pending_files = _track_media_files(
                self.db,
                store=self.store,
                files=files,
            )
            return self._registered(asset)
        except Exception:
            if pending_files is None:
                self.store.remove_if_created(files)
            else:
                pending_files.remove()
            raise

    def register_validated_images_in_transaction(
        self,
        images: Sequence[tuple[str, ValidatedImage]],
        *,
        uploaded_by_user_id: UUID,
        project_id: UUID,
    ) -> list[RegisteredSlotMediaAsset]:
        items = list(images)
        if self.db.get_transaction() is None:
            for _, image in items:
                self.store.discard_staged(image)
            raise RuntimeError("media registration requires a caller-owned transaction")

        registered: list[RegisteredSlotMediaAsset] = []
        try:
            for slot_key, image in items:
                _reject_external_connection_transaction(
                    self.db,
                    store=self.store,
                    image=image,
                )
                asset = self._register_validated_image_in_transaction(
                    image,
                    uploaded_by_user_id=uploaded_by_user_id,
                    project_id=project_id,
                )
                registered.append(
                    RegisteredSlotMediaAsset(
                        id=asset.id,
                        project_id=asset.project_id,
                        slot_key=slot_key,
                    )
                )
        except Exception:
            for _, image in items:
                self.store.discard_staged(image)
            raise
        return registered

    def _asset_and_blob(
        self,
        media_id: UUID,
    ) -> tuple[EvaluationMediaAsset, EvaluationMediaBlob] | None:
        row = self.db.execute(
            select(EvaluationMediaAsset, EvaluationMediaBlob)
            .join(
                EvaluationMediaBlob,
                EvaluationMediaBlob.id == EvaluationMediaAsset.blob_id,
            )
            .where(EvaluationMediaAsset.id == media_id)
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    def _allowed(
        self,
        asset: EvaluationMediaAsset,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        cohort_id = None
        if asset.class_id is not None:
            cohort_id = self.db.scalar(
                select(TeachingClass.cohort_id).where(TeachingClass.id == asset.class_id)
            )
        resource = EvaluationResource(
            student_person_id=asset.student_person_id,
            cohort_id=cohort_id,
            project_id=asset.project_id,
            class_id=asset.class_id,
            enrollment_id=asset.enrollment_id,
        )
        access = EvaluationAccessService(self.db)
        return all(
            access.decide(
                account_id=actor_user_id,
                permission=permission,
                resource=resource,
                occurred_at=occurred_at,
            ).allowed
            for permission in ("evaluation_center.access", "evaluation_media.original.read")
        )

    def _audit_original(
        self,
        media_id: UUID,
        *,
        actor_user_id: UUID,
        outcome: AuditOutcome,
        request_id: str,
    ) -> None:
        with write_transaction(self.db):
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="evaluation.media.original.read",
                resource_type="evaluation_media_asset",
                resource_id=media_id,
                purpose=None,
                outcome=outcome,
                request_id=request_id,
                metadata={"media_id": str(media_id)},
            )

    @staticmethod
    def _headers(extension: str) -> dict[str, str]:
        return {
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": (f'inline; filename="evaluation-image.{extension}"'),
        }

    def _protected_file(
        self,
        media_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        preview: bool,
    ) -> ProtectedMediaFile:
        row = self._asset_and_blob(media_id)
        if row is None:
            raise MediaAssetNotFound("media asset not found")
        asset, blob = row
        if (
            blob.storage_status != "available"
            or blob.validation_status != "validated"
            or blob.retention_status != "retained"
        ):
            raise MediaAssetNotFound("media asset not found")
        if not self._allowed(
            asset,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        ):
            raise MediaAssetNotFound("media asset not found")
        storage_key = blob.preview_storage_key if preview else blob.storage_key
        try:
            path = self.store.resolve(storage_key)
        except ValueError as exc:
            raise MediaAssetNotFound("media asset not found") from exc
        extension = "webp" if preview else blob.canonical_extension
        mime_type = "image/webp" if preview else blob.mime_type
        return ProtectedMediaFile(
            path=path,
            mime_type=mime_type,
            headers=self._headers(extension),
        )

    def read_original(
        self,
        media_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> ProtectedMediaFile:
        try:
            protected = self._protected_file(
                media_id,
                actor_user_id=actor_user_id,
                occurred_at=occurred_at,
                preview=False,
            )
        except MediaAssetNotFound:
            self._audit_original(
                media_id,
                actor_user_id=actor_user_id,
                outcome="denied",
                request_id=request_id,
            )
            raise
        try:
            self._audit_original(
                media_id,
                actor_user_id=actor_user_id,
                outcome="allowed",
                request_id=request_id,
            )
        except Exception:
            protected.close()
            raise
        return protected

    def audit_original_denied(
        self,
        media_id: UUID,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> None:
        self._audit_original(
            media_id,
            actor_user_id=actor_user_id,
            outcome="denied",
            request_id=request_id,
        )

    def read_preview(
        self,
        media_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> ProtectedMediaFile:
        return self._protected_file(
            media_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            preview=True,
        )

    def storage_status(self) -> MediaStorageStatus:
        return MediaStorageStatus(
            ready=self.store.ready,
            media_writable=self.store.media_writable,
            quarantine_writable=self.store.quarantine_writable,
            max_bytes=self.store.max_bytes,
            max_pixels=self.store.max_pixels,
            preview_max_edge=self.store.preview_max_edge,
        )
