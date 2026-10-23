from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.housing.api_support import (
    PrivateHousingRoute,
    _register_sensitive_read_audit,
    _set_sensitive_read_result_count,
)
from app.housing.dependencies import get_housing_portal_service
from app.housing.portal_service import HousingPortalService
from app.housing.schemas import HousingPortalResponse

router = APIRouter(tags=["housing"], route_class=PrivateHousingRoute)


def _housing_portal_context(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[HousingPortalService, Depends(get_housing_portal_service)],
) -> AuthContext:
    _register_sensitive_read_audit(
        request,
        service.db,
        context,
        action="housing.portal.read",
    )
    return context


@router.get("/me/housing", response_model=HousingPortalResponse)
def get_personal_housing(
    request: Request,
    context: Annotated[AuthContext, Depends(_housing_portal_context)],
    service: Annotated[HousingPortalService, Depends(get_housing_portal_service)],
) -> HousingPortalResponse:
    instant = datetime.now(UTC)
    result = service.get_housing(
        user_id=context.account.id,
        actor_person_id=context.person.id,
        as_of=instant,
    )
    _set_sensitive_read_result_count(request, len(result.subjects))
    return result
