from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.evaluations.errors import EvaluationIdempotencyConflict
from app.evaluations.task_models import EvaluationIdempotencyKey


@dataclass(frozen=True)
class EvaluationIdempotencyDigests:
    scope: str
    actor_key: str
    key_digest: str
    payload_digest: str


class EvaluationIdempotencyService:
    RETENTION = timedelta(days=7)

    def __init__(self, db: Session, *, digest_key: bytes) -> None:
        if len(digest_key) < 32:
            raise ValueError("idempotency digest key must contain at least 32 bytes")
        self.db = db
        self.digest_key = digest_key

    def _digest(self, *, domain: bytes, value: bytes) -> str:
        return hmac.new(
            self.digest_key,
            domain + b"\x00" + value,
            hashlib.sha256,
        ).hexdigest()

    def prepare(
        self,
        *,
        scope: str,
        actor_user_id: UUID,
        key: str,
        payload: Mapping[str, object],
    ) -> EvaluationIdempotencyDigests:
        if not scope or not 1 <= len(key) <= 200:
            raise ValueError("idempotency key is invalid")
        canonical_payload = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return EvaluationIdempotencyDigests(
            scope=scope,
            actor_key=str(actor_user_id),
            key_digest=self._digest(
                domain=b"evaluation-idempotency-key-v1",
                value=key.encode("utf-8"),
            ),
            payload_digest=self._digest(
                domain=b"evaluation-idempotency-payload-v1",
                value=canonical_payload,
            ),
        )

    @staticmethod
    def _transaction_lock_key(digests: EvaluationIdempotencyDigests) -> int:
        lock_material = (
            f"{digests.scope}\x00{digests.actor_key}\x00{digests.key_digest}"
        ).encode()
        return int.from_bytes(
            hashlib.sha256(lock_material).digest()[:8],
            byteorder="big",
            signed=True,
        )

    def existing_resource_id(
        self,
        digests: EvaluationIdempotencyDigests,
        *,
        occurred_at: datetime,
    ) -> UUID | None:
        self.db.execute(
            select(func.pg_advisory_xact_lock(self._transaction_lock_key(digests)))
        )
        row = self.db.scalar(
            select(EvaluationIdempotencyKey)
            .where(
                EvaluationIdempotencyKey.scope == digests.scope,
                EvaluationIdempotencyKey.actor_key == digests.actor_key,
                EvaluationIdempotencyKey.key_digest == digests.key_digest,
            )
            .with_for_update()
        )
        if row is None:
            return None
        if row.expires_at <= occurred_at:
            self.db.delete(row)
            self.db.flush()
            return None
        if not hmac.compare_digest(row.payload_digest, digests.payload_digest):
            raise EvaluationIdempotencyConflict("idempotency payload changed")
        return row.resource_id

    def record(
        self,
        digests: EvaluationIdempotencyDigests,
        *,
        resource_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self.db.add(
            EvaluationIdempotencyKey(
                scope=digests.scope,
                actor_key=digests.actor_key,
                key_digest=digests.key_digest,
                payload_digest=digests.payload_digest,
                resource_id=resource_id,
                created_at=occurred_at,
                expires_at=occurred_at + self.RETENTION,
            )
        )
        self.db.flush()
