from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_today
from app.portal.coach_service import PortalCoachService
from app.portal.context_service import PortalContextService
from app.portal.dependencies import (
    get_portal_coach_service,
    get_portal_context_service,
    get_portal_learning_service,
    get_portal_payment_service,
)
from app.portal.errors import (
    CoachCohortNotFound,
    FamilyStudentNotFound,
    PortalPermissionDenied,
    PortalServiceError,
)
from app.portal.learning_service import PortalLearningService
from app.portal.payment_service import PortalPaymentService
from app.portal.schemas import (
    CoachNextCohortsResponse,
    LearningStudentsResponse,
    LearningTeachingResponse,
    PortalContextResponse,
    SelfPaymentsResponse,
)

router = APIRouter(tags=["portal"])


def _raise_portal_error(exc: PortalServiceError) -> NoReturn:
    if isinstance(exc, PortalPermissionDenied):
        raise ProblemError(
            status=403,
            code=exc.code,
            detail="没有访问此门户的权限",
        ) from exc
    if isinstance(exc, CoachCohortNotFound):
        raise ProblemError(
            status=404,
            code=exc.code,
            detail="下一期营期不存在",
        ) from exc
    if isinstance(exc, FamilyStudentNotFound):
        raise ProblemError(
            status=404,
            code=exc.code,
            detail="学员教学信息不存在",
        ) from exc
    raise AssertionError(f"unmapped portal error: {type(exc).__name__}")


@router.get("/portal/context", response_model=PortalContextResponse)
def get_portal_context(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalContextService, Depends(get_portal_context_service)],
) -> PortalContextResponse:
    return service.get_context(
        user_id=context.account.id,
        actor_person_id=context.person.id,
        occurred_at=datetime.now(UTC),
    )


@router.get("/coach/next-cohorts", response_model=CoachNextCohortsResponse)
def get_coach_next_cohorts(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalCoachService, Depends(get_portal_coach_service)],
    anchor_cohort_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CoachNextCohortsResponse:
    try:
        return service.list_next(
            user_id=context.account.id,
            actor_person_id=context.person.id,
            now=datetime.now(UTC),
            anchor_cohort_id=anchor_cohort_id,
            page=page,
            page_size=page_size,
        )
    except PortalServiceError as exc:
        _raise_portal_error(exc)


@router.get("/family/students", response_model=LearningStudentsResponse)
def list_family_students(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalLearningService, Depends(get_portal_learning_service)],
) -> LearningStudentsResponse:
    try:
        return service.list_students(
            user_id=context.account.id,
            actor_person_id=context.person.id,
            reference_date=business_today(),
        )
    except PortalServiceError as exc:
        _raise_portal_error(exc)


@router.get(
    "/family/students/{student_id}/teaching",
    response_model=LearningTeachingResponse,
)
def get_family_student_teaching(
    student_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalLearningService, Depends(get_portal_learning_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LearningTeachingResponse:
    try:
        return service.get_teaching(
            user_id=context.account.id,
            actor_person_id=context.person.id,
            student_person_id=student_id,
            reference_date=business_today(),
            page=page,
            page_size=page_size,
        )
    except PortalServiceError as exc:
        _raise_portal_error(exc)


@router.get("/me/payments", response_model=SelfPaymentsResponse)
def get_self_payments(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalPaymentService, Depends(get_portal_payment_service)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SelfPaymentsResponse:
    try:
        result = service.list_payments(
            user_id=context.account.id,
            actor_person_id=context.person.id,
            page=page,
            page_size=page_size,
        )
    except PortalServiceError as exc:
        _raise_portal_error(exc)
    AuditService(db).append(
        actor_user_id=context.account.id,
        action="portal.payment.list_amounts",
        resource_type="payment",
        resource_id=None,
        purpose=None,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata={"result_count": len(result.items)},
    )
    db.commit()
    return result
