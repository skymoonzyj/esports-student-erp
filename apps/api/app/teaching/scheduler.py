from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.teaching.cohort_service import CohortService
from app.teaching.models import Cohort

SCHEDULER_LOCK_KEY = 0x455250_54454143


def new_scheduler_request_id() -> str:
    return f"scheduler-{uuid4()}"


def reconcile_due_cohorts(
    db: Session,
    *,
    now: datetime,
    request_id_factory: Callable[[], str] = new_scheduler_request_id,
) -> int:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scheduler now must include an offset")

    with db.begin():
        acquired = db.scalar(select(func.pg_try_advisory_xact_lock(SCHEDULER_LOCK_KEY)))
        if not acquired:
            return 0

        due_at = case(
            (Cohort.status == "active", Cohort.ends_at),
            else_=Cohort.starts_at,
        )
        candidates = list(
            db.scalars(
                select(Cohort)
                .where(
                    or_(
                        and_(
                            Cohort.auto_start_enabled.is_(True),
                            Cohort.status.in_(("open", "upcoming")),
                            Cohort.starts_at <= now,
                        ),
                        and_(
                            Cohort.auto_complete_enabled.is_(True),
                            Cohort.status == "active",
                            Cohort.ends_at <= now,
                        ),
                    )
                )
                .order_by(due_at, Cohort.status, Cohort.id)
                .with_for_update(skip_locked=True)
            )
        )
        service = CohortService(db)
        changed = sum(
            service._reconcile_locked(
                cohort,
                now=now,
                trigger="scheduler",
                request_id=request_id_factory(),
            )
            for cohort in candidates
        )
        db.flush()
        return changed
