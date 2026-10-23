from __future__ import annotations

import hashlib
import hmac
import ipaddress
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.models import AuthRateLimit
from app.core.transactions import write_transaction


class RegistrationStartRateLimited(ValueError):
    code = "verification_rate_limited"


class RegistrationStartLimiter:
    identifier_limit = 5
    source_limit = 20
    source_minute_limit = 10
    window = timedelta(hours=1)
    source_minute_window = timedelta(minutes=1)

    def __init__(
        self,
        db: Session,
        *,
        pepper: bytes,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(pepper) != 32:
            raise ValueError("registration start pepper must be 32 bytes")
        self.db = db
        self.pepper = pepper
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registration start clock must be timezone-aware")
        return value

    def _digest(self, domain: str, value: str) -> str:
        return hmac.new(
            self.pepper,
            f"{domain}\x00{value}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _normalize_source(value: str) -> str:
        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError:
            return "unknown"

    @staticmethod
    def _lock_key(key_digest: str) -> int:
        return int.from_bytes(
            hashlib.sha256(key_digest.encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )

    def _record_key(
        self,
        *,
        key_digest: str,
        limit: int,
        window: timedelta,
        now: datetime,
    ) -> None:
        row = self.db.scalar(
            select(AuthRateLimit)
            .where(AuthRateLimit.key_digest == key_digest)
            .with_for_update()
        )
        if row is None:
            self.db.add(
                AuthRateLimit(
                    key_digest=key_digest,
                    attempts=1,
                    window_started_at=now,
                    blocked_until=None,
                    updated_at=now,
                )
            )
            return
        if row.window_started_at <= now - window:
            row.attempts = 1
            row.window_started_at = now
            row.blocked_until = None
            row.updated_at = now
            return
        if row.attempts >= limit:
            raise RegistrationStartRateLimited(
                "registration start rate limit exceeded"
            )
        row.attempts += 1
        row.updated_at = now

    def record(self, *, identifier_digest: str, request_source: str) -> None:
        now = self._now()
        normalized_source = self._normalize_source(request_source)
        keys = (
            (
                self._digest("registration-start-identifier", identifier_digest),
                self.identifier_limit,
                self.window,
            ),
            (
                self._digest(
                    "registration-start-source",
                    normalized_source,
                ),
                self.source_limit,
                self.window,
            ),
            (
                self._digest(
                    "registration-start-source-minute",
                    normalized_source,
                ),
                self.source_minute_limit,
                self.source_minute_window,
            ),
        )
        with write_transaction(self.db):
            stale_keys = (
                select(AuthRateLimit.key_digest)
                .where(AuthRateLimit.updated_at < now - timedelta(days=7))
                .order_by(AuthRateLimit.updated_at)
                .limit(100)
            )
            self.db.execute(
                delete(AuthRateLimit).where(
                    AuthRateLimit.key_digest.in_(stale_keys)
                )
            )
            for key_digest, _limit, _window in sorted(keys):
                self.db.execute(
                    select(func.pg_advisory_xact_lock(self._lock_key(key_digest)))
                )
            for key_digest, limit, window in keys:
                self._record_key(
                    key_digest=key_digest,
                    limit=limit,
                    window=window,
                    now=now,
                )
            self.db.flush()
