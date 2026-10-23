from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, SessionTransactionOrigin


@contextmanager
def write_transaction(db: Session) -> Iterator[None]:
    transaction = db.get_transaction()
    if transaction is None:
        with db.begin():
            yield
        return
    if transaction.origin is SessionTransactionOrigin.AUTOBEGIN:
        try:
            yield
        except Exception:
            db.rollback()
            raise
        else:
            db.commit()
        return
    with db.begin_nested():
        yield
