"""Shared formal/practice lifecycle projections and fresh-start reconciliation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config_schemas import SuiteId, TaskConfig, TestId
from .errors import AssessmentConflict
from .models import AssessmentAttempt, AssessmentPracticeRun
from .schemas import (
    AttemptView,
    ControlSettings,
    EnvironmentSnapshot,
    Mode,
    RunState,
)


@dataclass(frozen=True, slots=True)
class RunOccupancy:
    mode: Mode
    attempt_id: UUID
    session_id: UUID | None
    suite_id: SuiteId
    test_id: TestId
    run_until: datetime
    submit_until: datetime


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def require_submission_elapsed(
    created_at: datetime,
    config: TaskConfig,
    now: datetime,
) -> None:
    """Reject a first save before the client could finish the issued task."""

    _aware(created_at, "created_at")
    _aware(now, "now")
    if now < created_at + timedelta(milliseconds=3_000 + config.duration_ms):
        raise AssessmentConflict(
            "测试尚未完成，请等待完整测试结束后再保存",
            code="esports_result_too_early",
        )


def effective_run_state(
    state: str,
    run_until: datetime,
    submit_until: datetime,
    now: datetime,
) -> RunState:
    """Project deadline-derived state without mutating persistence."""

    _aware(run_until, "run_until")
    _aware(submit_until, "submit_until")
    _aware(now, "now")
    if state not in {"running", "awaiting_submission", "interrupted", "expired", "submitted"}:
        raise ValueError("unknown run state")
    if state in {"submitted", "interrupted", "expired"}:
        return cast(RunState, state)
    if now >= submit_until:
        return "expired"
    return "awaiting_submission" if now >= run_until else "running"


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        _aware(value, "operation datetime")
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("operation payload is not canonically serializable")


def operation_digest(
    mode: Literal["formal", "practice"],
    action: str,
    target: UUID | str | None,
    payload: BaseModel | dict[str, Any],
) -> str:
    """Hash a route-bound request in the independent assessment namespace."""

    if not re.fullmatch(r"[a-z][a-z0-9_]*", action):
        raise ValueError("operation action must be a non-empty ASCII identifier")
    canonical = json.dumps(
        {
            "domain": "erp.esports_assessments.v1",
            "mode": mode,
            "action": action,
            "target": _jsonable(target),
            "payload": _jsonable(payload),
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def attempt_view(
    row: AssessmentAttempt | AssessmentPracticeRun,
    now: datetime,
) -> AttemptView:
    """Serialize either persisted run type while projecting deadline state."""

    config = TaskConfig.model_validate(row.config_snapshot)
    formal = isinstance(row, AssessmentAttempt)
    session_id = row.session_id if isinstance(row, AssessmentAttempt) else None
    return AttemptView(
        id=row.id,
        mode="formal" if formal else "practice",
        session_id=session_id,
        suite_id=config.suite_id,
        test_id=config.test_id,
        state=effective_run_state(row.state, row.run_until, row.submit_until, now),
        server_now=now,
        issued_at=row.created_at,
        run_until=row.run_until,
        submit_until=row.submit_until,
        seed=row.seed,
        config=config,
        settings=ControlSettings.model_validate(row.settings_snapshot),
        environment=EnvironmentSnapshot.model_validate(row.environment_snapshot),
    )


def reconcile_account_occupancy(
    db: Session,
    account_id: UUID,
    now: datetime,
) -> RunOccupancy | None:
    """Normalize stale rows and return the one run still consuming the account lease.

    Callers must already hold the account assessment advisory mutex. This helper
    is deliberately write-only lifecycle work and is never used by GET queries.
    """

    _aware(now, "now")
    formal_rows = list(
        db.scalars(
            select(AssessmentAttempt)
            .where(
                AssessmentAttempt.created_by_user_id == account_id,
                AssessmentAttempt.state.in_(("running", "awaiting_submission")),
            )
            .order_by(AssessmentAttempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    practice_rows = list(
        db.scalars(
            select(AssessmentPracticeRun)
            .where(
                AssessmentPracticeRun.created_by_user_id == account_id,
                AssessmentPracticeRun.state.in_(("running", "awaiting_submission")),
            )
            .order_by(AssessmentPracticeRun.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    active: list[RunOccupancy] = []
    changed = False
    for mode, rows in (("formal", formal_rows), ("practice", practice_rows)):
        for row in rows:
            projected = effective_run_state(row.state, row.run_until, row.submit_until, now)
            if row.state != projected:
                row.state = projected
                changed = True
            if projected == "running":
                config = TaskConfig.model_validate(row.config_snapshot)
                active.append(
                    RunOccupancy(
                        mode=cast(Mode, mode),
                        attempt_id=row.id,
                        session_id=row.session_id if isinstance(row, AssessmentAttempt) else None,
                        suite_id=config.suite_id,
                        test_id=config.test_id,
                        run_until=row.run_until,
                        submit_until=row.submit_until,
                    )
                )
    if changed:
        db.flush()
    if len(active) > 1:
        raise RuntimeError("account has multiple active esports assessment runs")
    return active[0] if active else None
