from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.operations.models import OperationsServiceHeartbeat

TEACHING_SCHEDULER_SERVICE_KEY = "teaching_scheduler"


def record_service_heartbeat(
    db: Session,
    *,
    service_key: str,
    succeeded: bool,
    occurred_at: datetime,
    result_count: int | None = None,
    error_code: str | None = None,
) -> None:
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("heartbeat time must be timezone-aware")
    if succeeded and error_code is not None:
        raise ValueError("successful heartbeat cannot include an error code")
    if not succeeded and not error_code:
        raise ValueError("failed heartbeat requires an error code")

    table = OperationsServiceHeartbeat.__table__
    statement = postgresql_insert(OperationsServiceHeartbeat).values(
        service_key=service_key,
        status="healthy" if succeeded else "failed",
        last_seen_at=occurred_at,
        last_success_at=occurred_at if succeeded else None,
        consecutive_failures=0 if succeeded else 1,
        last_error_code=None if succeeded else error_code,
        last_result_count=result_count if succeeded else None,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.service_key],
        set_={
            "status": "healthy" if succeeded else "failed",
            "last_seen_at": occurred_at,
            "last_success_at": (
                occurred_at if succeeded else table.c.last_success_at
            ),
            "consecutive_failures": (
                0 if succeeded else table.c.consecutive_failures + 1
            ),
            "last_error_code": None if succeeded else error_code,
            "last_result_count": result_count if succeeded else table.c.last_result_count,
            "updated_at": occurred_at,
        },
    )
    with write_transaction(db):
        db.execute(statement)
