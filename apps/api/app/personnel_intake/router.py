from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.identity.schemas import Page
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.approval_service import PersonnelApprovalService
from app.personnel_intake.dependencies import (
    get_approval_service,
    get_intake_access,
    get_intake_service,
)
from app.personnel_intake.editor_context import AssistedEditorContextQuery
from app.personnel_intake.queries import ManagedPeopleQuery
from app.personnel_intake.schemas import (
    ApprovalResult,
    AssistedContext,
    AssistedEditorContext,
    AssistedProfileCorrection,
    CoachResponsibilityInput,
    IntakeInput,
    IntakeListItem,
    IntakeQueueView,
    IntakeStatus,
    IntakeView,
    ManagedPeopleFilters,
    ManagedPeoplePage,
    PersonKind,
)
from app.personnel_intake.service import PersonnelIntakeService


class PrivateIntakeRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            request.state.private_portal_response = True
            try:
                response = await original(request)
            except RequestValidationError as exc:
                raise ProblemError(
                    status=422,
                    code="validation_error",
                    detail="建档请求参数无效，请检查后重试",
                    errors=[{"field": "request", "message": "字段格式、范围或内容不符合要求"}],
                ) from exc
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        return handler


class ExpectedVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class ResubmitInput(IntakeInput):
    expected_version: int = Field(ge=1)


router = APIRouter(
    prefix="/personnel-intake", tags=["personnel-intake"], route_class=PrivateIntakeRoute
)
Actor = Annotated[AuthContext, Depends(require_standard_account)]
Service = Annotated[PersonnelIntakeService, Depends(get_intake_service)]
ApprovalService = Annotated[PersonnelApprovalService, Depends(get_approval_service)]


class ReturnInput(ExpectedVersion):
    reason: str = Field(min_length=1, max_length=500)


class DirectCreateInput(IntakeInput):
    idempotency_key: UUID


@router.post("/requests/{request_id}/approve", response_model=ApprovalResult)
def approve(
    request_id: UUID, actor: Actor, service: ApprovalService, input: ExpectedVersion
) -> ApprovalResult:
    return service.approve(actor, request_id, input.expected_version)


@router.post("/requests/{request_id}/return", response_model=IntakeView)
def return_request(
    request_id: UUID, actor: Actor, service: ApprovalService, input: ReturnInput
) -> IntakeView:
    return service.return_request(actor, request_id, input.expected_version, input.reason)


@router.post("/direct-create", response_model=ApprovalResult, status_code=201)
def direct_create(
    actor: Actor, service: ApprovalService, input: DirectCreateInput
) -> ApprovalResult:
    return service.direct_create(
        actor,
        IntakeInput(**{key: getattr(input, key) for key in IntakeInput.model_fields}),
        input.idempotency_key,
    )


@router.post("/people/{person_id}/corrections", response_model=ApprovalResult)
def correct_profile(
    person_id: UUID, actor: Actor, service: ApprovalService, input: AssistedProfileCorrection
) -> ApprovalResult:
    return service.correct_profile(actor, person_id, input)


@router.get("/people/{person_id}/editor-context", response_model=AssistedEditorContext)
def editor_context(
    person_id: UUID, actor: Actor, db: Annotated[Session, Depends(get_db)]
) -> AssistedEditorContext:
    return AssistedEditorContextQuery(db).get(actor, person_id)


@router.put("/people/{person_id}/coaches", status_code=204)
def replace_coaches(
    person_id: UUID, actor: Actor, service: ApprovalService, input: CoachResponsibilityInput
) -> Response:
    from app.personnel_intake.responsibilities import CoachResponsibilityService

    CoachResponsibilityService(service.db).replace(
        actor, person_id, input.coach_person_ids, input.expected_version, input.reason
    )
    return Response(status_code=204)


@router.get("/context", response_model=AssistedContext)
def context(
    actor: Actor, access: Annotated[PersonnelIntakeAccess, Depends(get_intake_access)]
) -> AssistedContext:
    return access.context(actor)


@router.get("/managed-people", response_model=ManagedPeoplePage)
def managed_people(
    actor: Actor,
    db: Annotated[Session, Depends(get_db)],
    filters: Annotated[ManagedPeopleFilters, Query()],
) -> ManagedPeoplePage:
    return ManagedPeopleQuery(db).list(actor, filters)


@router.post("/requests", response_model=IntakeView, status_code=201)
def submit(actor: Actor, service: Service, input: IntakeInput) -> IntakeView:
    return service.submit(actor, input)


@router.get("/requests", response_model=Page[IntakeListItem])
def list_requests(
    actor: Actor,
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: IntakeQueueView | None = None,
    status: IntakeStatus | None = None,
    kind: PersonKind | None = None,
    submitter_query: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=80), Query()
    ] = None,
) -> Page[IntakeListItem]:
    return service.list_requests(
        actor,
        page=page,
        page_size=page_size,
        view=view,
        status=status,
        kind=kind,
        submitter_query=submitter_query,
    )


@router.get("/requests/{request_id}", response_model=IntakeView)
def detail(request_id: UUID, actor: Actor, service: Service) -> IntakeView:
    return service.detail(actor, request_id)


@router.post("/requests/{request_id}/resubmit", response_model=IntakeView)
def resubmit(request_id: UUID, actor: Actor, service: Service, input: ResubmitInput) -> IntakeView:
    return service.resubmit(
        actor,
        request_id,
        input.expected_version,
        IntakeInput(**{key: getattr(input, key) for key in IntakeInput.model_fields}),
    )


@router.post("/requests/{request_id}/withdraw", response_model=IntakeView)
def withdraw(
    request_id: UUID, actor: Actor, service: Service, input: ExpectedVersion
) -> IntakeView:
    return service.withdraw(actor, request_id, input.expected_version)
