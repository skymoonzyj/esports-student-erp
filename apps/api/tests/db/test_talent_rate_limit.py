from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.auth.models import AuthRateLimit
from app.talent_tests.rate_limit import TalentRateLimited, TalentWriteLimiter


@pytest.fixture(autouse=True)
def cleanup_committed_rate_limits(postgres_engine):
    factory = sessionmaker(bind=postgres_engine)
    with factory() as db:
        before = set(db.scalars(select(AuthRateLimit.key_digest)))
    yield
    # These records intentionally escape the business rollback fixture.
    with factory.begin() as db:
        added = set(db.scalars(select(AuthRateLimit.key_digest))) - before
        db.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(added)))


def test_budget_survives_business_rollback(postgres_engine):
    factory = sessionmaker(bind=postgres_engine)
    account_id, now = uuid4(), datetime.now(UTC)
    with factory() as business:
        for _ in range(30):
            TalentWriteLimiter(factory, b"t" * 32).record(account_id, now)
        business.rollback()
    with pytest.raises(TalentRateLimited) as caught:
        TalentWriteLimiter(factory, b"t" * 32).record(account_id, now)
    assert caught.value.retry_after == 60


def test_two_instances_share_atomic_budget_and_accounts_do_not(postgres_engine):
    factory = sessionmaker(bind=postgres_engine)
    account_id, now = uuid4(), datetime.now(UTC)
    limiters = [TalentWriteLimiter(factory, b"t" * 32) for _ in range(2)]

    def record(index):
        try:
            limiters[index % 2].record(account_id, now)
            return True
        except TalentRateLimited:
            return False

    with ThreadPoolExecutor(max_workers=12) as workers:
        assert sum(workers.map(record, range(31))) == 30
    limiters[0].record(uuid4(), now)


def test_rejections_do_not_extend_window_and_retry_rounds_up(postgres_engine):
    factory = sessionmaker(bind=postgres_engine)
    limiter = TalentWriteLimiter(factory, b"t" * 32)
    account_id, now = uuid4(), datetime.now(UTC)
    for _ in range(30):
        limiter.record(account_id, now)
    with pytest.raises(TalentRateLimited) as caught:
        limiter.record(account_id, now + timedelta(seconds=59, milliseconds=100))
    assert caught.value.retry_after == 1
    limiter.record(account_id, now + timedelta(seconds=60))
    for _ in range(29):
        limiter.record(account_id, now + timedelta(seconds=60))
    with pytest.raises(TalentRateLimited):
        limiter.record(account_id, now + timedelta(seconds=60))


def test_bounded_cleanup_preserves_recent_auth_records(postgres_engine):
    factory = sessionmaker(bind=postgres_engine)
    now = datetime.now(UTC)
    prefix = str(uuid4())
    with factory.begin() as db:
        for index in range(105):
            db.add(
                AuthRateLimit(
                    key_digest=f"{prefix}-{index}",
                    attempts=1,
                    window_started_at=now - timedelta(days=8),
                    updated_at=now - timedelta(days=8),
                )
            )
        db.add(AuthRateLimit(key_digest=prefix, attempts=4, window_started_at=now, updated_at=now))
    TalentWriteLimiter(factory, b"t" * 32).record(uuid4(), now)
    with factory() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(AuthRateLimit).where(AuthRateLimit.key_digest.like(f"{prefix}-%"))
                    )
                )
            )
            == 5
        )
        assert db.get(AuthRateLimit, prefix).attempts == 4
