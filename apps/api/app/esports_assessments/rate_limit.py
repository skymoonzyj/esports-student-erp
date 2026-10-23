"""Independent committed write budgets for esports assessment APIs."""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.models import AuthRateLimit

from .errors import AssessmentRateLimited

OperationKind = Literal["write", "formal_create", "practice_create"]
_WINDOWS: dict[str, tuple[int, int]] = {
    "total": (30, 60),
    "formal_create": (6, 600),
    "practice_create": (12, 60),
}


class AssessmentWriteLimiter:
    """Persist fixed-window counters outside the business transaction."""

    def __init__(self, session_factory: Callable[[], Session], pepper: bytes) -> None:
        if not pepper:
            raise ValueError("rate-limit pepper is required")
        self.session_factory = session_factory
        self.pepper = pepper

    def _digest(self, account_id: UUID, bucket: str) -> str:
        return hmac.new(
            self.pepper,
            f"esports-assessments-write\0{bucket}\0{account_id}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _lock_key(digest: str) -> int:
        return int.from_bytes(bytes.fromhex(digest[:16]), "big", signed=True)

    @staticmethod
    def _consume(
        db: Session,
        digest: str,
        now: datetime,
        limit: int,
        seconds: int,
    ) -> None:
        row = db.scalar(
            select(AuthRateLimit).where(AuthRateLimit.key_digest == digest).with_for_update()
        )
        window = timedelta(seconds=seconds)
        if row is None:
            db.add(
                AuthRateLimit(
                    key_digest=digest,
                    attempts=1,
                    window_started_at=now,
                    blocked_until=None,
                    updated_at=now,
                )
            )
            return
        if now >= row.window_started_at + window:
            row.window_started_at = now
            row.attempts = 1
            row.blocked_until = None
            row.updated_at = now
            return
        if row.attempts >= limit:
            retry_after = max(
                1,
                math.ceil((row.window_started_at + window - now).total_seconds()),
            )
            raise AssessmentRateLimited(retry_after)
        row.attempts += 1
        row.updated_at = now

    def record(self, account_id: UUID, operation_kind: OperationKind, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if operation_kind not in {"write", "formal_create", "practice_create"}:
            raise ValueError("unknown assessment operation kind")
        buckets: list[str] = [operation_kind] if operation_kind != "write" else []
        buckets.append("total")
        digests = {bucket: self._digest(account_id, bucket) for bucket in buckets}
        with self.session_factory() as db, db.begin():
            for lock_key in sorted(self._lock_key(value) for value in digests.values()):
                db.scalar(select(func.pg_advisory_xact_lock(lock_key)))
            for bucket in buckets:
                limit, seconds = _WINDOWS[bucket]
                self._consume(db, digests[bucket], now, limit, seconds)
            stale = (
                select(AuthRateLimit.key_digest)
                .where(AuthRateLimit.updated_at < now - timedelta(days=7))
                .order_by(AuthRateLimit.updated_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            db.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(stale)))
