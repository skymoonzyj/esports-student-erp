from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, SessionTransactionOrigin

_ATOMIC_OWNER = "erp_atomic_write_transaction"


@contextmanager
def atomic_write_transaction(db: Session) -> Iterator[None]:
    """Keep service writes and their required audit in one unit of work.

    Authentication reads often start AUTOBEGIN before a route reaches its
    business write. Mark that transaction as explicitly owned so nested
    service helpers cannot commit it before the route appends its audit.
    An explicit caller-owned transaction is still only given a savepoint.
    """
    with write_transaction(db):
        previous_owner = db.info.get(_ATOMIC_OWNER)
        db.info[_ATOMIC_OWNER] = db.get_transaction()
        try:
            yield
        finally:
            if previous_owner is None:
                db.info.pop(_ATOMIC_OWNER, None)
            else:
                db.info[_ATOMIC_OWNER] = previous_owner


@contextmanager
def write_transaction(db: Session) -> Iterator[None]:
    transaction = db.get_transaction()
    if transaction is None:
        with db.begin():
            yield
        return
    if db.info.get(_ATOMIC_OWNER) is transaction:
        with db.begin_nested():
            yield
        return
    if transaction.origin is SessionTransactionOrigin.AUTOBEGIN:
        try:
            yield
        except Exception:
            db.rollback()
            raise
        try:
            db.commit()
        except Exception:
            try:
                db.rollback()
            except InvalidRequestError:
                # An after_commit listener may raise after the database commit is
                # already durable. Preserve that original error instead of
                # replacing it with a rollback-from-COMMITTED-state error.
                pass
            raise
        return
    with db.begin_nested():
        yield
