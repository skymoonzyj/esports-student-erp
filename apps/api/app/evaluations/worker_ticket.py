from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evaluations.errors import EvaluationResourceNotFound
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.media_service import ProtectedMediaFile
from app.evaluations.media_store import PrivateMediaStore
from app.evaluations.processing_models import SystemEvaluationAttempt
from app.evaluations.task_models import SystemEvaluationTask, SystemEvaluationTaskMedia

_TICKET_DOMAIN = b"evaluation-worker-media-ticket-v1\x00"
_TICKET_KEYS = frozenset({"task_id", "attempt", "task_version", "media_id", "exp"})


@dataclass(frozen=True)
class WorkerMediaTicketPayload:
    task_id: UUID
    attempt: int
    task_version: int
    media_id: UUID
    expires_at: datetime


class WorkerMediaTicketSigner:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("worker media ticket secret must contain at least 32 bytes")
        self.secret = secret

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        try:
            return base64.b64decode(
                value + padding,
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, binascii.Error) as exc:
            raise EvaluationResourceNotFound("evaluation resource not found") from exc

    @staticmethod
    def _canonical(payload: WorkerMediaTicketPayload) -> bytes:
        return json.dumps(
            {
                "attempt": payload.attempt,
                "exp": int(payload.expires_at.timestamp()),
                "media_id": str(payload.media_id),
                "task_id": str(payload.task_id),
                "task_version": payload.task_version,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")

    def _signature(
        self,
        payload: bytes,
        *,
        worker_identity: str,
        lease_token_digest: str,
    ) -> bytes:
        associated = (
            worker_identity.encode("utf-8")
            + b"\x00"
            + lease_token_digest.encode("ascii")
            + b"\x00"
        )
        return hmac.new(
            self.secret,
            _TICKET_DOMAIN + associated + payload,
            hashlib.sha256,
        ).digest()

    def issue(
        self,
        *,
        task_id: UUID,
        attempt: int,
        task_version: int,
        media_id: UUID,
        expires_at: datetime,
        worker_identity: str,
        lease_token_digest: str,
    ) -> str:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("ticket expiry must be timezone-aware")
        payload = self._canonical(
            WorkerMediaTicketPayload(
                task_id=task_id,
                attempt=attempt,
                task_version=task_version,
                media_id=media_id,
                expires_at=expires_at,
            )
        )
        signature = self._signature(
            payload,
            worker_identity=worker_identity,
            lease_token_digest=lease_token_digest,
        )
        return f"{self._encode(payload)}.{self._encode(signature)}"

    def parse(self, ticket: str) -> tuple[WorkerMediaTicketPayload, bytes, bytes]:
        if len(ticket) > 2048 or ticket.count(".") != 1:
            raise EvaluationResourceNotFound("evaluation resource not found")
        encoded_payload, encoded_signature = ticket.split(".", 1)
        payload_bytes = self._decode(encoded_payload)
        signature = self._decode(encoded_signature)
        if len(signature) != hashlib.sha256().digest_size:
            raise EvaluationResourceNotFound("evaluation resource not found")
        try:
            raw = json.loads(payload_bytes)
            if not isinstance(raw, dict) or set(raw) != _TICKET_KEYS:
                raise ValueError
            if (
                not isinstance(raw["attempt"], int)
                or isinstance(raw["attempt"], bool)
                or not 1 <= raw["attempt"] <= 3
                or not isinstance(raw["task_version"], int)
                or isinstance(raw["task_version"], bool)
                or raw["task_version"] < 1
                or not isinstance(raw["exp"], int)
                or isinstance(raw["exp"], bool)
            ):
                raise ValueError
            task_id = UUID(raw["task_id"])
            media_id = UUID(raw["media_id"])
            if raw["task_id"] != str(task_id) or raw["media_id"] != str(media_id):
                raise ValueError
            payload = WorkerMediaTicketPayload(
                task_id=task_id,
                attempt=raw["attempt"],
                task_version=raw["task_version"],
                media_id=media_id,
                expires_at=datetime.fromtimestamp(raw["exp"], tz=UTC),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise EvaluationResourceNotFound("evaluation resource not found") from exc
        if not hmac.compare_digest(self._canonical(payload), payload_bytes):
            raise EvaluationResourceNotFound("evaluation resource not found")
        return payload, payload_bytes, signature

    def verify(
        self,
        payload_bytes: bytes,
        signature: bytes,
        *,
        worker_identity: str,
        lease_token_digest: str,
    ) -> bool:
        expected = self._signature(
            payload_bytes,
            worker_identity=worker_identity,
            lease_token_digest=lease_token_digest,
        )
        return hmac.compare_digest(expected, signature)


class WorkerMediaTicketService:
    def __init__(
        self,
        db: Session,
        *,
        store: PrivateMediaStore,
        secret: bytes,
    ) -> None:
        self.db = db
        self.store = store
        self.signer = WorkerMediaTicketSigner(secret)

    @staticmethod
    def _not_found() -> EvaluationResourceNotFound:
        return EvaluationResourceNotFound("evaluation resource not found")

    def read_original(
        self,
        *,
        task_id: UUID,
        attempt: int,
        task_version: int,
        media_id: UUID,
        worker_identity: str,
        ticket: str,
        occurred_at: datetime,
    ) -> ProtectedMediaFile:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise self._not_found()
        payload, payload_bytes, signature = self.signer.parse(ticket)
        if (
            payload.task_id != task_id
            or payload.attempt != attempt
            or payload.task_version != task_version
            or payload.media_id != media_id
            or payload.expires_at <= occurred_at
        ):
            raise self._not_found()

        task = self.db.get(SystemEvaluationTask, task_id)
        stored_attempt = self.db.scalar(
            select(SystemEvaluationAttempt).where(
                SystemEvaluationAttempt.task_id == task_id,
                SystemEvaluationAttempt.attempt == attempt,
            )
        )
        if (
            task is None
            or stored_attempt is None
            or task.status != "processing"
            or task.current_attempt != attempt
            or task.lock_version != task_version
            or stored_attempt.task_lock_version != task_version
            or stored_attempt.status != "active"
            or stored_attempt.worker_identity != worker_identity
            or stored_attempt.lease_expires_at <= occurred_at
            or not self.signer.verify(
                payload_bytes,
                signature,
                worker_identity=worker_identity,
                lease_token_digest=stored_attempt.lease_token_digest,
            )
        ):
            raise self._not_found()

        row = self.db.execute(
            select(EvaluationMediaAsset, EvaluationMediaBlob)
            .join(
                SystemEvaluationTaskMedia,
                SystemEvaluationTaskMedia.media_id == EvaluationMediaAsset.id,
            )
            .join(
                EvaluationMediaBlob,
                EvaluationMediaBlob.id == EvaluationMediaAsset.blob_id,
            )
            .where(
                SystemEvaluationTaskMedia.task_id == task_id,
                SystemEvaluationTaskMedia.media_id == media_id,
            )
        ).one_or_none()
        if row is None:
            raise self._not_found()
        _asset, blob = row
        if (
            blob.storage_status != "available"
            or blob.validation_status != "validated"
            or blob.retention_status != "retained"
        ):
            raise self._not_found()
        try:
            path = self.store.resolve(blob.storage_key)
        except ValueError as exc:
            raise self._not_found() from exc
        extension = cast(str, blob.canonical_extension)
        return ProtectedMediaFile(
            path=path,
            mime_type=blob.mime_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": (
                    f'inline; filename="evaluation-image.{extension}"'
                ),
            },
        )
