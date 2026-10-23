from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.revenue_share.organization_manager_schemas import (
    OrganizationManagerCandidatePage,
    OrganizationManagerResponse,
)
from app.revenue_share.organization_manager_service import OrganizationManagerService

Context = Annotated[AuthContext, Depends(require_standard_account)]
Db = Annotated[Session, Depends(get_db)]


def _private(request: Request, response: Response) -> None:
    request.state.private_portal_response = True
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"


class PrivateOrganizationManagerRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private_handler(request: Request) -> Response:
            # Dependencies and validation can fail before the endpoint runs.
            request.state.private_portal_response = True
            response = await original(request)
            _private(request, response)
            return response

        return private_handler


router = APIRouter(
    prefix="/revenue-share", tags=["revenue-share"], route_class=PrivateOrganizationManagerRoute
)


@router.get("/organization-manager-candidates", response_model=OrganizationManagerCandidatePage)
def candidates(
    request: Request,
    response: Response,
    context: Context,
    db: Db,
    q: Annotated[str, Query(max_length=120)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OrganizationManagerCandidatePage:
    _private(request, response)
    return OrganizationManagerService(db).list_candidates(
        actor_user_id=context.account.id,
        q=q,
        page=page,
        page_size=page_size,
        request_id=str(request.state.request_id),
    )


@router.get(
    "/organizations/{organization_id}/managers", response_model=list[OrganizationManagerResponse]
)
def list_managers(
    organization_id: UUID, request: Request, response: Response, context: Context, db: Db
) -> list[OrganizationManagerResponse]:
    _private(request, response)
    return OrganizationManagerService(db).list_managers(
        actor_user_id=context.account.id,
        organization_id=organization_id,
        request_id=str(request.state.request_id),
    )


@router.put(
    "/organizations/{organization_id}/managers/{account_id}",
    response_model=OrganizationManagerResponse,
)
def grant_manager(
    organization_id: UUID,
    account_id: UUID,
    request: Request,
    response: Response,
    context: Context,
    db: Db,
) -> OrganizationManagerResponse:
    _private(request, response)
    return OrganizationManagerService(db).grant_manager(
        actor_user_id=context.account.id,
        organization_id=organization_id,
        account_id=account_id,
        request_id=str(request.state.request_id),
    )


@router.delete("/organizations/{organization_id}/managers/{account_id}", status_code=204)
def revoke_manager(
    organization_id: UUID,
    account_id: UUID,
    request: Request,
    response: Response,
    context: Context,
    db: Db,
) -> Response:
    _private(request, response)
    OrganizationManagerService(db).revoke_manager(
        actor_user_id=context.account.id,
        organization_id=organization_id,
        account_id=account_id,
        request_id=str(request.state.request_id),
    )
    response.status_code = 204
    return response
