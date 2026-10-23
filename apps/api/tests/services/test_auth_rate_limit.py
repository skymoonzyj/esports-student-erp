from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.models import AuthRateLimit
from app.auth.service import AuthRateLimiter


def test_concurrent_failure_updates_do_not_lose_attempts(postgres_engine: Engine) -> None:
    key = "a" * 64
    now = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
    with Session(postgres_engine) as setup, setup.begin():
        setup.add(
            AuthRateLimit(
                key_digest=key,
                attempts=0,
                window_started_at=now,
                blocked_until=None,
                updated_at=now,
            )
        )

    worker_started = threading.Event()
    worker_errors: list[BaseException] = []

    def record_second_failure() -> None:
        try:
            with Session(postgres_engine) as worker, worker.begin():
                limiter = AuthRateLimiter(worker, pepper=bytes(range(32)), clock=lambda: now)
                worker_started.set()
                limiter.record_failure(key)
        except (RuntimeError, SQLAlchemyError) as exc:  # pragma: no cover
            worker_errors.append(exc)

    with Session(postgres_engine) as first, first.begin():
        row = first.scalar(
            select(AuthRateLimit).where(AuthRateLimit.key_digest == key).with_for_update()
        )
        assert row is not None
        row.attempts = 1
        first.flush()
        worker = threading.Thread(target=record_second_failure)
        worker.start()
        assert worker_started.wait(timeout=2)
        time.sleep(0.1)

    worker.join(timeout=5)
    assert not worker.is_alive()
    assert worker_errors == []
    try:
        with Session(postgres_engine) as verification:
            final_attempts = verification.get(AuthRateLimit, key).attempts  # type: ignore[union-attr]
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            row = cleanup.get(AuthRateLimit, key)
            if row is not None:
                cleanup.delete(row)

    assert final_attempts == 2
