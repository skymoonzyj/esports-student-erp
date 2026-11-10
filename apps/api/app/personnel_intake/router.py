from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints, model_validator
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_today
from app.identity.schemas import Page
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.approval_service import PersonnelApprovalService
from app.personnel_intake.dependencies import (
    get_approval_service,
    get_intake_access,
    get_intake_service,
)
from app.personnel_intake.editor_context import AssistedEditorContextQuery
from app.personnel_intake.identity_selection import ManagedIdentityInput, StaffTeamSelection
from app.personnel_intake.identity_service import resolve_management_identity
from app.personnel_intake.queries import ManagedPeopleQuery
from app.personnel_intake.readiness import AssistedReadiness, AssistedReadinessPolicy
from app.personnel_intake.role_intents import (
    RoleIntentApproval,
    RoleIntentPage,
    RoleIntentService,
    RoleIntentView,
)
from app.personnel_intake.schemas import (
    ActivationRoleOptions,
    ActivationRoleUpdate,
    ActivationRoleView,
    ApprovalResult,
    AssistedContext,
    AssistedEditorContext,
    AssistedPersonFields,
    AssistedProfileCorrection,
    CoachResponsibilityInput,
    DirectPersonInput,
    IntakeInput,
    IntakeListItem,
    IntakeQueueView,
    IntakeStatus,
    IntakeView,
    ManagedPeopleFilters,
    ManagedPeoplePage,
    PendingIntakeInput,
    PendingPersonFields,
    PendingProfileCorrection,
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


class ManagedIdentityFields(BaseModel):
    kind: PersonKind | None = None
    coach_person_ids: list[UUID] = Field(default_factory=list)
    team_id: UUID | None = None
    idempotency_key: UUID
    activation_role_keys: list[StrictStr] = Field(default_factory=list)
    identity_selection: ManagedIdentityInput | None = None
    staff_teams: list[StaffTeamSelection] = Field(default_factory=list)

    @model_validator(mode="after")
    def resolve_identity(self) -> "ManagedIdentityFields":
        resolved, groups, _ = resolve_management_identity(
            kind=self.kind, identity=self.identity_selection,
            role_keys=self.activation_role_keys if "activation_role_keys" in self.model_fields_set else None,
            staff_teams=self.staff_teams,
        )
        if resolved is not None:
            self.kind = resolved.kind
        self.staff_teams = groups
        if groups and getattr(self, "team_allocation_status", None) == "not_required":
            raise ValueError("已有工作人员归属不能选择无需分队")
        return self


class DirectCreateInput(AssistedPersonFields, ManagedIdentityFields):
    pass


class DirectCreatePendingInput(PendingPersonFields, ManagedIdentityFields):
    team_allocation_status: Literal["pending", "not_required"] = "pending"


@router.get("/management-identity-context")
def management_identity_context(actor: Actor, service: ApprovalService) -> dict[str, Any]:
    from app.personnel_intake.role_grants import ActivationRoleService
    from app.team_management.person_staff_query import PersonStaffQuery

    roles = ActivationRoleService(service.db, lookup=service.lookup).options(actor, for_intake=True)
    teams = PersonStaffQuery(service.db, lookup=service.lookup).staff_team_options(actor, for_intake=True)
    return {"role_options": roles.items, "can_assign_staff_teams": teams.can_assign,
            "staff_team_reason": teams.reason, "staff_team_options": teams.items}


@router.get("/role-intents", response_model=RoleIntentPage)
def role_intents(actor: Actor, service: ApprovalService,
                 page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)) -> RoleIntentPage:
    return RoleIntentService(service.db, lookup=service.lookup).list(actor, page=page, page_size=page_size)


@router.get("/people/{person_id}/role-intent", response_model=RoleIntentView | None)
def role_intent(person_id: UUID, actor: Actor, service: ApprovalService) -> RoleIntentView | None:
    return RoleIntentService(service.db, lookup=service.lookup).get(actor, person_id)


@router.post("/people/{person_id}/role-intent/approve", response_model=RoleIntentView)
def approve_role_intent(person_id: UUID, actor: Actor, service: ApprovalService, input: RoleIntentApproval) -> RoleIntentView:
    return RoleIntentService(service.db, lookup=service.lookup).approve(actor, person_id, input)


@router.get("/activation-role-options", response_model=ActivationRoleOptions)
def activation_role_options(actor: Actor, service: ApprovalService) -> ActivationRoleOptions:
    from app.personnel_intake.role_grants import ActivationRoleService

    return ActivationRoleService(service.db, lookup=service.lookup).options(actor)


@router.get("/people/{person_id}/activation-roles", response_model=ActivationRoleView)
def activation_roles(person_id: UUID, actor: Actor, service: ApprovalService) -> ActivationRoleView:
    from app.personnel_intake.role_grants import ActivationRoleService

    return ActivationRoleService(service.db, lookup=service.lookup).get(actor, person_id)


@router.put("/people/{person_id}/activation-roles", response_model=ActivationRoleView)
def update_activation_roles(
    person_id: UUID, actor: Actor, service: ApprovalService, input: ActivationRoleUpdate
) -> ActivationRoleView:
    from app.personnel_intake.role_grants import ActivationRoleService

    return ActivationRoleService(service.db, lookup=service.lookup).update(actor, person_id, input)


@router.post("/direct-create-pending", response_model=ApprovalResult, status_code=201)
def direct_create_pending(
    actor: Actor, service: ApprovalService, input: DirectCreatePendingInput
) -> ApprovalResult:
    return service.direct_create_pending(
        actor,
        PendingIntakeInput(**{key: getattr(input, key) for key in PendingIntakeInput.model_fields}),
        input.idempotency_key,
        activation_role_keys=input.activation_role_keys if "activation_role_keys" in input.model_fields_set else None,
        identity_selection=input.identity_selection, staff_teams=input.staff_teams,
    )


@router.post("/people/{person_id}/pending-corrections", response_model=ApprovalResult)
def correct_pending_profile(
    person_id: UUID, actor: Actor, service: ApprovalService, input: PendingProfileCorrection
) -> ApprovalResult:
    return service.correct_pending_profile(actor, person_id, input)


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
        DirectPersonInput(**{key: getattr(input, key) for key in DirectPersonInput.model_fields}),
        input.idempotency_key,
        activation_role_keys=input.activation_role_keys if "activation_role_keys" in input.model_fields_set else None,
        identity_selection=input.identity_selection, staff_teams=input.staff_teams,
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


@router.get("/people/{person_id}/readiness", response_model=AssistedReadiness)
def readiness(person_id: UUID, actor: Actor, service: ApprovalService) -> AssistedReadiness:
    # Same target-specific administrator editor boundary; does not expose public
    # identity existence, guardian details, or housing data to an unauthenticated caller.
    AssistedEditorContextQuery(service.db).get(actor, person_id)
    return AssistedReadinessPolicy(
        service.db, cipher=service.cipher, lookup=service.lookup
    ).evaluate_many([person_id], reference_date=business_today())[person_id]


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
