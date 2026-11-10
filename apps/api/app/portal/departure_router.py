from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.housing.api_support import _raise_housing_error
from app.housing.errors import HousingServiceError
from app.portal.departure_dates import DepartureWindow
from app.portal.departure_schemas import DepartureRemindersResponse
from app.portal.departure_service import PortalDepartureService
from app.portal.dependencies import get_portal_departure_service

router = APIRouter(tags=["portal"])


@router.get("/portal/departure-reminders", response_model=DepartureRemindersResponse)
def list_departure_reminders(
    actor: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalDepartureService, Depends(get_portal_departure_service)],
    window: DepartureWindow = "three_days",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> DepartureRemindersResponse:
    try:
        return service.list_reminders(
            actor=actor,
            now=datetime.now(UTC),
            window=window,
            page=page,
            page_size=page_size,
        )
    except HousingServiceError as exc:
        # Reuse only the existing sanitized mapping, not the auditing route
        # wrapper: reminder reads must not write housing business/audit state.
        _raise_housing_error(exc)
