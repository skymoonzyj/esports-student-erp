from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 9, 22, 3, tzinfo=UTC)


def test_total_write_budget_commits_independently_of_business_rollback(postgres_engine) -> None:
    from app.esports_assessments.errors import AssessmentRateLimited
    from app.esports_assessments.rate_limit import AssessmentWriteLimiter

    factory = sessionmaker(bind=postgres_engine)
    limiter = AssessmentWriteLimiter(factory, bytes(range(32)))
    account_id = uuid4()
    with Session(postgres_engine) as business:
        transaction = business.begin()
        limiter.record(account_id, "write", NOW)
        transaction.rollback()
    for _ in range(29):
        limiter.record(account_id, "write", NOW)
    with pytest.raises(AssessmentRateLimited) as error:
        limiter.record(account_id, "write", NOW)
    assert error.value.retry_after == 60
    limiter.record(account_id, "write", NOW + timedelta(seconds=60))


def test_specific_create_budgets_are_checked_before_shared_total(postgres_engine) -> None:
    from app.esports_assessments.errors import AssessmentRateLimited
    from app.esports_assessments.rate_limit import AssessmentWriteLimiter

    factory = sessionmaker(bind=postgres_engine)
    limiter = AssessmentWriteLimiter(factory, bytes(range(32)))
    formal_account, practice_account = uuid4(), uuid4()
    for _ in range(6):
        limiter.record(formal_account, "formal_create", NOW)
    with pytest.raises(AssessmentRateLimited) as formal:
        limiter.record(formal_account, "formal_create", NOW)
    assert formal.value.retry_after == 600
    for _ in range(24):
        limiter.record(formal_account, "write", NOW)
    with pytest.raises(AssessmentRateLimited):
        limiter.record(formal_account, "write", NOW)

    for _ in range(12):
        limiter.record(practice_account, "practice_create", NOW)
    with pytest.raises(AssessmentRateLimited) as practice:
        limiter.record(practice_account, "practice_create", NOW)
    assert practice.value.retry_after == 60


def test_concurrent_thirty_first_write_cannot_bypass_budget(postgres_engine) -> None:
    from app.esports_assessments.errors import AssessmentRateLimited
    from app.esports_assessments.rate_limit import AssessmentWriteLimiter

    factory = sessionmaker(bind=postgres_engine)
    account_id = uuid4()
    limiter = AssessmentWriteLimiter(factory, bytes(range(32)))
    for _ in range(29):
        limiter.record(account_id, "write", NOW)
    barrier = Barrier(2)

    def record() -> str:
        barrier.wait(timeout=5)
        try:
            AssessmentWriteLimiter(factory, bytes(range(32))).record(account_id, "write", NOW)
        except AssessmentRateLimited:
            return "limited"
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(record) for _ in range(2)]
        outcomes = sorted(future.result(timeout=10) for future in futures)

    assert outcomes == ["accepted", "limited"]
