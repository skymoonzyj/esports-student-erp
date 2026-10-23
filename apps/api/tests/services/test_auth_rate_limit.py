from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.models import AuthRateLimit
from app.auth.service import AuthRateLimiter


def test_source_key_digest_normalizes_ipv6_and_is_domain_separated() -> None:
    with Session() as db:
        limiter = AuthRateLimiter(
            db,
            pepper=bytes(range(32)),
            clock=lambda: datetime(2026, 7, 26, 10, 0, tzinfo=UTC),
        )

        expanded = limiter.source_key_digest(
            "2001:0db8:0000:0000:0000:0000:0000:0001"
        )
        compressed = limiter.source_key_digest("2001:db8::1")

        assert expanded == compressed
        assert expanded != limiter.key_digest("source", "2001:db8::1")


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


def test_concurrent_first_failures_create_one_row_and_count_both(
    postgres_engine: Engine,
) -> None:
    key = "b" * 64
    now = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
    start = threading.Barrier(3)
    worker_errors: list[BaseException] = []

    def record_failure() -> None:
        try:
            with Session(postgres_engine) as worker, worker.begin():
                limiter = AuthRateLimiter(
                    worker,
                    pepper=bytes(range(32)),
                    clock=lambda: now,
                )
                start.wait(timeout=2)
                limiter.record_failure(key)
        except (RuntimeError, SQLAlchemyError, threading.BrokenBarrierError) as exc:
            worker_errors.append(exc)

    workers = [threading.Thread(target=record_failure) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.wait(timeout=2)
    for worker in workers:
        worker.join(timeout=5)

    try:
        with Session(postgres_engine) as verification:
            row = verification.get(AuthRateLimit, key)
            assert row is not None
            attempts = row.attempts
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            row = cleanup.get(AuthRateLimit, key)
            if row is not None:
                cleanup.delete(row)

    assert all(not worker.is_alive() for worker in workers)
    assert worker_errors == []
    assert attempts == 2


def test_concurrent_combined_pair_and_source_failures_reach_both_thresholds(
    postgres_engine: Engine,
) -> None:
    pair_key = "d" * 64
    source_key = "e" * 64
    now = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
    start = threading.Barrier(3)
    worker_errors: list[BaseException] = []

    def record_login_failure() -> None:
        try:
            with Session(postgres_engine) as worker, worker.begin():
                limiter = AuthRateLimiter(
                    worker,
                    pepper=bytes(range(32)),
                    clock=lambda: now,
                )
                start.wait(timeout=2)
                limiter.record_failures(
                    {
                        pair_key: (
                            2,
                            limiter.block_duration,
                            limiter.window_duration,
                        ),
                        source_key: (
                            2,
                            limiter.source_block_duration,
                            limiter.source_window_duration,
                        ),
                    }
                )
        except (RuntimeError, SQLAlchemyError, threading.BrokenBarrierError) as exc:
            worker_errors.append(exc)

    workers = [threading.Thread(target=record_login_failure) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.wait(timeout=2)
    for worker in workers:
        worker.join(timeout=5)

    try:
        with Session(postgres_engine) as verification:
            rows = {
                row.key_digest: row
                for row in verification.scalars(
                    select(AuthRateLimit).where(
                        AuthRateLimit.key_digest.in_((pair_key, source_key))
                    )
                )
            }
            assert rows[pair_key].attempts == rows[source_key].attempts == 2
            assert rows[pair_key].blocked_until == now + timedelta(minutes=15)
            assert rows[source_key].blocked_until == now + timedelta(minutes=1)
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            for key in (pair_key, source_key):
                row = cleanup.get(AuthRateLimit, key)
                if row is not None:
                    cleanup.delete(row)

    assert all(not worker.is_alive() for worker in workers)
    assert worker_errors == []


def test_block_check_refreshes_state_changed_by_an_independent_session(
    postgres_engine: Engine,
) -> None:
    key = "c" * 64
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

    try:
        with Session(postgres_engine) as observer:
            cached = observer.get(AuthRateLimit, key)
            assert cached is not None
            assert cached.blocked_until is None

            with Session(postgres_engine) as writer, writer.begin():
                limiter = AuthRateLimiter(
                    writer,
                    pepper=bytes(range(32)),
                    clock=lambda: now,
                )
                for _ in range(5):
                    limiter.record_failure(key)

            observer_limiter = AuthRateLimiter(
                observer,
                pepper=bytes(range(32)),
                clock=lambda: now,
            )
            assert observer_limiter.is_blocked(key) is True
            assert cached.attempts == 5
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            row = cleanup.get(AuthRateLimit, key)
            if row is not None:
                cleanup.delete(row)
