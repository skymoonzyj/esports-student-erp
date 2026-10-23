from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.rbac.service import RbacService

router = APIRouter(tags=["audit"])


class AuditEventResponse(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID | None
    purpose: str | None
    outcome: Literal["allowed", "denied", "failed"]
    request_id: str
    metadata: dict[str, Any]
    created_at: datetime


class AuditEventPage(BaseModel):
    items: list[AuditEventResponse]
    page: int
    page_size: int
    total: int


@router.get("/audit-events", response_model=AuditEventPage)
def list_audit_events(
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    actor_user_id: UUID | None = None,
    action: Annotated[str | None, Query(max_length=160)] = None,
    resource_type: Annotated[str | None, Query(max_length=120)] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AuditEventPage:
    decision = RbacService(db).decide(
        user_id=context.account.id,
        permission="audit.read",
        resource_tokens={"global"},
    )
    if not decision.allowed:
        raise ProblemError(
            status=403,
            code="permission_denied",
            detail="没有执行此操作的权限",
        )
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ProblemError(
            status=422,
            code="invalid_date_range",
            detail="开始时间不能晚于结束时间",
        )
    service = AuditService(db)
    events, total = service.list_events(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return AuditEventPage(
        items=[
            AuditEventResponse(
                id=event.id,
                actor_user_id=event.actor_user_id,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                purpose=service.public_purpose(event.purpose),
                outcome=cast(Literal["allowed", "denied", "failed"], event.outcome),
                request_id=event.request_id,
                metadata=service.public_metadata(event.event_metadata),
                created_at=event.created_at,
            )
            for event in events
        ],
        page=page,
        page_size=page_size,
        total=total,
    )
