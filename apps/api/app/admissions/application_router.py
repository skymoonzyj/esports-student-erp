from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.admissions.api_support import raise_admissions_error
from app.admissions.application_service import EnrollmentApplicationService
from app.admissions.dependencies import get_enrollment_application_service
from app.admissions.errors import AdmissionsServiceError
from app.admissions.routing import PrivateAdmissionsRoute
from app.admissions.schemas import (
    EnrollmentApplicationCreate,
    EnrollmentApplicationResponse,
    EnrollmentCatalogResponse,
)
from app.audit.service import AuditMetadataRejected
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.time import business_today

router = APIRouter(tags=["admissions"], route_class=PrivateAdmissionsRoute)


@router.get("/enrollment-catalog", response_model=EnrollmentCatalogResponse)
def get_enrollment_catalog(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[
        EnrollmentApplicationService,
        Depends(get_enrollment_application_service),
    ],
) -> EnrollmentCatalogResponse:
    try:
        return service.get_catalog(
            user_id=context.account.id,
            actor_person_id=context.person.id,
            reference_date=business_today(),
        )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)


@router.post(
    "/enrollment-applications",
    response_model=EnrollmentApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_enrollment_application(
    input: EnrollmentApplicationCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[
        EnrollmentApplicationService,
        Depends(get_enrollment_application_service),
    ],
) -> EnrollmentApplicationResponse:
    try:
        return service.create_application(
            input,
            user_id=context.account.id,
            actor_person_id=context.person.id,
            reference_date=business_today(),
            request_id=str(request.state.request_id),
        )
    except AdmissionsServiceError as exc:
        raise_admissions_error(exc)
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="admissions_audit_failed",
            detail="报名申请审计记录失败",
        ) from exc
