from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.core import transactions


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def disable_sqlite_legacy_transactions(connection, _record):
        connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def explicit_begin(connection):
        connection.exec_driver_sql("BEGIN")

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE test_writes (value INTEGER PRIMARY KEY)"))
    yield engine
    engine.dispose()


@pytest.mark.parametrize("preexisting_read", [False, True])
def test_atomic_business_and_audit_failure_roll_back_together(engine, preexisting_read):
    with Session(engine) as db:
        if preexisting_read:
            db.execute(text("SELECT 1"))
        with (
            pytest.raises(RuntimeError, match="audit unavailable"),
            transactions.atomic_write_transaction(db),
        ):
            with transactions.write_transaction(db):
                db.execute(text("INSERT INTO test_writes VALUES (1)"))
            raise RuntimeError("audit unavailable")
        assert not db.in_transaction()
    with engine.connect() as observer:
        assert observer.scalar(text("SELECT count(*) FROM test_writes")) == 0


def test_atomic_success_commits_business_and_audit_once(engine):
    commits = []
    with Session(engine) as db:
        event.listen(
            db, "after_commit", lambda session: commits.append(session.in_nested_transaction())
        )
        db.execute(text("SELECT 1"))
        with transactions.atomic_write_transaction(db):
            with transactions.write_transaction(db):
                db.execute(text("INSERT INTO test_writes VALUES (1)"))
            db.execute(text("INSERT INTO test_writes VALUES (2)"))
        assert commits.count(False) == 1
    with engine.connect() as observer:
        assert observer.scalar(text("SELECT count(*) FROM test_writes")) == 2


def test_atomic_wrapper_leaves_explicit_outer_transaction_owned_by_caller(engine):
    with Session(engine) as db:
        outer = db.begin()
        with transactions.atomic_write_transaction(db), transactions.write_transaction(db):
            db.execute(text("INSERT INTO test_writes VALUES (1)"))
        assert outer.is_active
        outer.rollback()
    with engine.connect() as observer:
        assert observer.scalar(text("SELECT count(*) FROM test_writes")) == 0
