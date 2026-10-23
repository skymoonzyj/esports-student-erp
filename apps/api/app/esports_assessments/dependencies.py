"""FastAPI dependencies for authorized, independently budgeted writes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import _secret_bytes, require_standard_account
from app.auth.service import AuthContext
from app.core.database import SessionLocal, get_db

from .access import AssessmentAccess
from .rate_limit import AssessmentWriteLimiter, OperationKind

Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[AuthContext, Depends(require_standard_account)]


def get_assessment_write_limiter(request: Request) -> AssessmentWriteLimiter:
    return AssessmentWriteLimiter(
        SessionLocal,
        _secret_bytes(request.app.state.settings, "session_token_pepper"),
    )


Limiter = Annotated[AssessmentWriteLimiter, Depends(get_assessment_write_limiter)]


def _authorized_budget(
    actor: AuthContext,
    db: Session,
    limiter: AssessmentWriteLimiter,
    kind: OperationKind,
) -> AuthContext:
    now = datetime.now(UTC)
    AssessmentAccess(db, actor, now).require_take()
    limiter.record(actor.account.id, kind, now)
    return actor


def require_assessment_writer(actor: Actor, db: Db, limiter: Limiter) -> AuthContext:
    return _authorized_budget(actor, db, limiter, "write")


def require_formal_creator(actor: Actor, db: Db, limiter: Limiter) -> AuthContext:
    return _authorized_budget(actor, db, limiter, "formal_create")


def require_practice_creator(actor: Actor, db: Db, limiter: Limiter) -> AuthContext:
    return _authorized_budget(actor, db, limiter, "practice_create")


Writer = Annotated[AuthContext, Depends(require_assessment_writer)]
FormalCreator = Annotated[AuthContext, Depends(require_formal_creator)]
PracticeCreator = Annotated[AuthContext, Depends(require_practice_creator)]
