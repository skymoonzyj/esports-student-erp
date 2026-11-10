from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.appearance.schemas import AppearanceUpdate, AppearanceView
from app.appearance.service import AppearanceService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError


class AppearanceRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            request.state.private_portal_response = True
            try:
                response = await original(request)
            except RequestValidationError:
                # Do not reflect arbitrary field names, input CSS/HTML/URLs or
                # Pydantic context back into logs and operations error displays.
                raise ProblemError(
                    status=422, code="validation_error", detail="系统外观请求格式无效"
                ) from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


router = APIRouter(tags=["appearance"], route_class=AppearanceRoute)
Database = Annotated[Session, Depends(get_db)]
Actor = Annotated[AuthContext, Depends(require_standard_account)]


@router.get("/appearance", response_model=AppearanceView)
def public_appearance(db: Database) -> AppearanceView:
    return AppearanceService(db).read()


@router.get("/operations/appearance", response_model=AppearanceView)
def managed_appearance(db: Database, context: Actor) -> AppearanceView:
    service = AppearanceService(db)
    service.require_manager(context)
    return service.read()


@router.put("/operations/appearance", response_model=AppearanceView)
def publish_appearance(
    command: AppearanceUpdate, request: Request, db: Database, context: Actor
) -> AppearanceView:
    return AppearanceService(db).update(context, command, request_id=str(request.state.request_id))
