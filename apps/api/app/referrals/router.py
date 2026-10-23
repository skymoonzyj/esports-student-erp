from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.referrals.access import InvitedHousingAccess
from app.referrals.query_service import InvitedHousingQueryService
from app.referrals.schemas import (
    InvitationSourcesResponse,
    InvitedHousingPage,
    InvitedHousingStatus,
    SourceKind,
)


class PrivateReferralRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private_handler(request: Request) -> Response:
            request.state.private_portal_response = True
            response = await original(request)
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        return private_handler


router = APIRouter(prefix="/referrals/me", tags=["referrals"], route_class=PrivateReferralRoute)
Context = Annotated[AuthContext, Depends(require_standard_account)]
Db = Annotated[Session, Depends(get_db)]


@router.get("/sources", response_model=InvitationSourcesResponse)
def sources(request: Request, context: Context, db: Db) -> InvitationSourcesResponse:
    available = InvitedHousingAccess(db).list_sources(
        user_id=context.account.id,
        actor_person_id=context.account.person_id,
        as_of=datetime.now(UTC),
    )
    AuditService(db).append(
        actor_user_id=context.account.id,
        action="referrals.sources.read",
        resource_type="invitation_source",
        resource_id=None,
        purpose="查看邀请来源",
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata={"result_count": len(available)},
    )
    db.commit()
    return InvitationSourcesResponse(sources=available)


@router.get("/housing", response_model=InvitedHousingPage)
def housing(
    request: Request,
    context: Context,
    db: Db,
    source_kind: SourceKind = "all",
    organization_id: UUID | None = None,
    status: InvitedHousingStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> InvitedHousingPage:
    return InvitedHousingQueryService(db).list_housing(
        user_id=context.account.id,
        actor_person_id=context.account.person_id,
        source_kind=source_kind,
        organization_id=organization_id,
        status=status,
        page=page,
        page_size=page_size,
        as_of=datetime.now(UTC),
        request_id=str(request.state.request_id),
    )
