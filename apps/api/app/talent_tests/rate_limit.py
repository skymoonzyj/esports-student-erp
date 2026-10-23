from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.models import AuthRateLimit


class TalentRateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("操作过于频繁，请稍后重试")


class TalentWriteLimiter:
    """Account-local fixed window, persisted independently of business writes."""

    def __init__(self, session_factory: Callable[[], Session], pepper: bytes) -> None:
        self.session_factory = session_factory
        self.pepper = pepper

    def record(self, account_id: UUID, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        digest = hmac.new(
            self.pepper, f"talent-write\0{account_id}".encode(), hashlib.sha256
        ).hexdigest()
        lock_key = int.from_bytes(bytes.fromhex(digest[:16]), "big", signed=True)
        retry_after = None
        with self.session_factory() as db, db.begin():
            db.execute(select(func.pg_advisory_xact_lock(lock_key)))
            row = db.scalar(
                select(AuthRateLimit).where(AuthRateLimit.key_digest == digest).with_for_update()
            )
            if row is None:
                db.add(
                    AuthRateLimit(
                        key_digest=digest, attempts=1, window_started_at=now, updated_at=now
                    )
                )
            elif now >= row.window_started_at + timedelta(seconds=60):
                row.window_started_at = now
                row.attempts = 1
                row.blocked_until = None
                row.updated_at = now
            elif row.attempts >= 30:
                retry_after = max(
                    1,
                    math.ceil(
                        (row.window_started_at + timedelta(seconds=60) - now).total_seconds()
                    ),
                )
            else:
                row.attempts += 1
                row.updated_at = now
            # Shared table: retain the existing auth service's seven-day horizon.
            # Skip locked rows so cleanup cannot deadlock concurrent account locks.
            stale = (
                select(AuthRateLimit.key_digest)
                .where(AuthRateLimit.updated_at < now - timedelta(days=7))
                .order_by(AuthRateLimit.updated_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            db.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(stale)))
        if retry_after is not None:
            raise TalentRateLimited(retry_after)
