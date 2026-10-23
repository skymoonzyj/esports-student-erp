from __future__ import annotations

import hashlib
import hmac
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime
from time import monotonic, sleep

import pytest
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.auth.models import AuthRateLimit
from app.registration.start_limiter import (
    RegistrationStartLimiter,
    RegistrationStartRateLimited,
)

NOW = datetime(2026, 8, 16, 9, tzinfo=UTC)
PEPPER = bytes(range(32))
REQUEST_SOURCE = "2001:db8::816"
WORKER_COUNT = RegistrationStartLimiter.source_minute_limit + 1
IDENTIFIER_DIGESTS = tuple(
    f"registration-start-source-concurrency-{index}" for index in range(WORKER_COUNT)
)


def _rate_limit_key(domain: str, value: str) -> str:
    return hmac.new(
        PEPPER,
        f"{domain}\x00{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


SOURCE_KEY = _rate_limit_key("registration-start-source", REQUEST_SOURCE)
SOURCE_MINUTE_KEY = _rate_limit_key(
    "registration-start-source-minute",
    REQUEST_SOURCE,
)
IDENTIFIER_KEYS = frozenset(
    _rate_limit_key("registration-start-identifier", value) for value in IDENTIFIER_DIGESTS
)
TEST_KEYS = IDENTIFIER_KEYS | {SOURCE_KEY, SOURCE_MINUTE_KEY}


def _event_backend_pid(connection) -> int:
    return int(connection.connection.driver_connection.info.backend_pid)


def _parameters_contain(parameters: object, expected: str) -> bool:
    if isinstance(parameters, Mapping):
        return expected in parameters.values()
    if isinstance(parameters, (tuple, list)):
        return expected in parameters
    return False


def test_same_source_concurrent_boundary_is_exact_and_sessions_remain_usable(
    postgres_engine: Engine,
) -> None:
    concurrent_engine = create_engine(postgres_engine.url, poolclass=NullPool)
    start_barrier = threading.Barrier(WORKER_COUNT)
    workers_ready = threading.Condition()
    source_selects_changed = threading.Condition()
    release_source_selects = threading.Event()
    worker_pids: dict[int, int] = {}
    source_select_pids: set[int] = set()
    first_source_pid: list[int] = []

    def after_cursor_execute(
        connection,
        _cursor,
        statement: str,
        parameters,
        _context,
        _executemany: bool,
    ) -> None:
        lowered = statement.casefold()
        if not (
            "from auth_rate_limits" in lowered
            and "for update" in lowered
            and _parameters_contain(parameters, SOURCE_KEY)
        ):
            return
        backend_pid = _event_backend_pid(connection)
        with source_selects_changed:
            source_select_pids.add(backend_pid)
            if not first_source_pid:
                first_source_pid.append(backend_pid)
            source_selects_changed.notify_all()
        if not release_source_selects.wait(timeout=10):
            raise TimeoutError("source-key SELECT was not released by the test")

    def record(index: int) -> tuple[str, bool, BaseException | None]:
        with Session(concurrent_engine) as db:
            backend_pid = int(db.scalar(text("SELECT pg_backend_pid()")) or 0)
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            with workers_ready:
                worker_pids[index] = backend_pid
                workers_ready.notify_all()
            start_barrier.wait(timeout=10)
            limiter = RegistrationStartLimiter(db, pepper=PEPPER, clock=lambda: NOW)
            try:
                limiter.record(
                    identifier_digest=IDENTIFIER_DIGESTS[index],
                    request_source=REQUEST_SOURCE,
                )
            except RegistrationStartRateLimited:
                db.rollback()
                return "limited", db.scalar(text("SELECT 1")) == 1, None
            except BaseException as exc:  # noqa: BLE001 - asserted in the parent thread.
                db.rollback()
                return "error", db.scalar(text("SELECT 1")) == 1, exc
            return "success", db.scalar(text("SELECT 1")) == 1, None

    def wait_for_serialization_or_missing_lock() -> str:
        deadline = monotonic() + 8
        last_blockers: dict[int, list[int]] = {}
        while monotonic() < deadline:
            with source_selects_changed:
                observed_source_selects = set(source_select_pids)
                first_pid = first_source_pid[0] if first_source_pid else None
            all_pids = set(worker_pids.values())
            if observed_source_selects == all_pids:
                return "missing-source-advisory-lock"
            if first_pid is not None:
                waiting_pids = all_pids - {first_pid}
                with postgres_engine.connect() as observer:
                    rows = observer.execute(
                        text(
                            "SELECT worker_pid, pg_blocking_pids(worker_pid) "
                            "FROM unnest(CAST(:worker_pids AS integer[])) "
                            "AS workers(worker_pid)"
                        ),
                        {"worker_pids": sorted(waiting_pids)},
                    )
                    last_blockers = {
                        int(worker_pid): [int(pid) for pid in blocking_pids]
                        for worker_pid, blocking_pids in rows
                    }
                if waiting_pids and all(last_blockers.get(pid) for pid in waiting_pids):
                    return "source-advisory-lock"
            sleep(0.02)
        pytest.fail(
            "workers neither converged on the unlocked missing source row nor "
            "blocked on source advisory serialization; "
            f"source_select_pids={sorted(source_select_pids)}, "
            f"worker_pids={sorted(worker_pids.values())}, blockers={last_blockers}"
        )

    event.listen(concurrent_engine, "after_cursor_execute", after_cursor_execute)
    executor = ThreadPoolExecutor(max_workers=WORKER_COUNT)
    futures = []
    try:
        futures = [executor.submit(record, index) for index in range(WORKER_COUNT)]
        with workers_ready:
            assert workers_ready.wait_for(
                lambda: len(worker_pids) == WORKER_COUNT,
                timeout=10,
            )
        assert len(set(worker_pids.values())) == WORKER_COUNT

        coordination = wait_for_serialization_or_missing_lock()
        release_source_selects.set()

        done, not_done = wait(futures, timeout=20)
        assert not not_done, f"unfinished registration-start workers: {len(not_done)}"
        assert len(done) == WORKER_COUNT
        outcomes = [future.result() for future in futures]

        operational_errors = [
            error for _status, _usable, error in outcomes if isinstance(error, OperationalError)
        ]
        assert operational_errors == []
        unexpected_errors = [
            f"{type(error).__name__}: {error}"
            for _status, _usable, error in outcomes
            if error is not None
        ]
        assert unexpected_errors == []
        assert coordination == "source-advisory-lock"
        statuses = [status for status, _usable, _error in outcomes]
        assert statuses.count("success") == RegistrationStartLimiter.source_minute_limit
        assert statuses.count("limited") == 1
        assert all(usable for _status, usable, _error in outcomes)

        with Session(postgres_engine) as verification:
            source_rows = list(
                verification.scalars(
                    select(AuthRateLimit).where(AuthRateLimit.key_digest == SOURCE_KEY)
                )
            )
            assert len(source_rows) == 1
            assert source_rows[0].attempts == RegistrationStartLimiter.source_minute_limit
            minute_source_rows = list(
                verification.scalars(
                    select(AuthRateLimit).where(
                        AuthRateLimit.key_digest == SOURCE_MINUTE_KEY
                    )
                )
            )
            assert len(minute_source_rows) == 1
            assert (
                minute_source_rows[0].attempts
                == RegistrationStartLimiter.source_minute_limit
            )
            test_rows = list(
                verification.scalars(
                    select(AuthRateLimit).where(AuthRateLimit.key_digest.in_(TEST_KEYS))
                )
            )
            assert len(test_rows) == RegistrationStartLimiter.source_minute_limit + 2
            assert (
                sum(row.key_digest in IDENTIFIER_KEYS for row in test_rows)
                == RegistrationStartLimiter.source_minute_limit
            )
            assert all(row.attempts == 1 for row in test_rows if row.key_digest in IDENTIFIER_KEYS)
    finally:
        release_source_selects.set()
        executor.shutdown(wait=True, cancel_futures=True)
        event.remove(concurrent_engine, "after_cursor_execute", after_cursor_execute)
        concurrent_engine.dispose()
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(TEST_KEYS)))
