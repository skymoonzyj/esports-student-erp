from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.database import get_db


def get_food_reporting_db(
    request_db: Annotated[Session, Depends(get_db)],
) -> Iterator[Session]:
    bind = request_db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    with Session(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    ) as session:
        connection = session.connection(
            execution_options={"isolation_level": "REPEATABLE READ"}
        )
        if connection.get_isolation_level().upper() != "REPEATABLE READ":
            session.rollback()
            raise RuntimeError("food reporting requires repeatable read")
        try:
            yield session
        finally:
            if session.in_transaction():
                session.rollback()
