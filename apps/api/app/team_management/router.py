from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.team_management.access import TeamAccessService
from app.team_management.assignment_service import TeamAssignmentService
from app.team_management.dependencies import (
    get_team_access,
    get_team_assignments,
    get_team_memberships,
    get_team_queries,
)
from app.team_management.errors import TeamServiceError
from app.team_management.membership_service import TeamMembershipService
from app.team_management.person_staff_router import build_person_staff_router
from app.team_management.query_service import TeamQueryService
from app.team_management.schemas import (
    AssignManagerInput,
    AssignStaffInput,
    JoinTeamInput,
    LeaveTeamInput,
    ManagerCandidatePage,
    MyTeamResponse,
    StaffRole,
    TeamAssignmentView,
    TeamCandidatePage,
    TeamContext,
    TeamMemberView,
    TeamMutationResult,
    TeamPage,
    TeamStaffAssignmentView,
    TeamSummary,
    TransferTeamInput,
    UnassignManagerInput,
    UnassignStaffInput,
)
from app.team_management.staff_service import TeamStaffService


class PrivateTeamRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            request.state.private_portal_response = True
            try:
                response = await original(request)
            except TeamServiceError as exc:
                raise ProblemError(status=exc.status, code=exc.code, detail=exc.detail) from exc
            except RequestValidationError as exc:
                raise ProblemError(
                    status=422,
                    code="validation_error",
                    detail="队伍请求参数无效，请检查后重试",
                    errors=[
                        {
                            "field": ".".join(str(part) for part in error["loc"]),
                            "message": "字段格式、范围或内容不符合要求",
                        }
                        for error in exc.errors()
                    ],
                ) from exc
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        return handler


router = APIRouter(
    prefix="/team-management", tags=["team-management"], route_class=PrivateTeamRoute
)
Actor = Annotated[AuthContext, Depends(require_standard_account)]
Queries = Annotated[TeamQueryService, Depends(get_team_queries)]
Memberships = Annotated[TeamMembershipService, Depends(get_team_memberships)]
Assignments = Annotated[TeamAssignmentService, Depends(get_team_assignments)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
CandidateSize = Annotated[int, Query(ge=1, le=20)]
SearchQuery = Annotated[str, Query(min_length=1, max_length=120)]


def get_team_staff(db: Annotated[Session, Depends(get_db)]) -> TeamStaffService:
    return TeamStaffService(db)


StaffAssignments = Annotated[TeamStaffService, Depends(get_team_staff)]


@router.get("/context", response_model=TeamContext)
def context(
    actor: Actor, access: Annotated[TeamAccessService, Depends(get_team_access)]
) -> TeamContext:
    return access.context(actor)


@router.get("/me", response_model=MyTeamResponse)
def my_team(
    actor: Actor, service: Queries, page: PageNumber = 1, page_size: PageSize = 20
) -> MyTeamResponse:
    return service.my_team(actor, page=page, page_size=page_size)


@router.get("/teams", response_model=TeamPage[TeamSummary])
def list_teams(
    actor: Actor,
    service: Queries,
    project_id: UUID | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> TeamPage[TeamSummary]:
    return service.list_teams(actor, project_id=project_id, page=page, page_size=page_size)


@router.get("/teams/{team_id}/memberships", response_model=TeamPage[TeamMemberView])
def memberships(
    team_id: UUID,
    actor: Actor,
    service: Queries,
    status: Literal["current", "left", "all"] = "current",
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> TeamPage[TeamMemberView]:
    return service.list_memberships(actor, team_id, status=status, page=page, page_size=page_size)


@router.get("/teams/{team_id}/candidates", response_model=TeamCandidatePage)
def candidates(
    team_id: UUID,
    actor: Actor,
    service: Queries,
    query: SearchQuery,
    cursor: str | None = None,
    page_size: CandidateSize = 20,
) -> TeamCandidatePage:
    return service.candidates(
        actor, team_id, kind="member", query=query, cursor=cursor, page_size=page_size
    )


@router.get("/teams/{team_id}/manager-candidates", response_model=ManagerCandidatePage)
def manager_candidates(
    team_id: UUID,
    actor: Actor,
    service: Queries,
    query: SearchQuery,
    cursor: str | None = None,
    page_size: CandidateSize = 20,
) -> ManagerCandidatePage:
    page = service.candidates(
        actor, team_id, kind="manager", query=query, cursor=cursor, page_size=page_size
    )
    return ManagerCandidatePage.model_validate(page.model_dump())


@router.get("/teams/{team_id}/manager-assignments", response_model=TeamPage[TeamAssignmentView])
def assignments(
    team_id: UUID, actor: Actor, service: Queries, page: PageNumber = 1, page_size: PageSize = 20
) -> TeamPage[TeamAssignmentView]:
    return service.list_assignments(actor, team_id, page=page, page_size=page_size)


@router.post("/teams/{team_id}/memberships", response_model=TeamMutationResult)
def join(
    team_id: UUID, input: JoinTeamInput, actor: Actor, service: Memberships
) -> TeamMutationResult:
    return service.join(actor, team_id, input)


@router.post("/memberships/{membership_id}/leave", response_model=TeamMutationResult)
def leave(
    membership_id: UUID, input: LeaveTeamInput, actor: Actor, service: Memberships
) -> TeamMutationResult:
    return service.leave(actor, membership_id, input)


@router.post("/memberships/{membership_id}/transfer", response_model=TeamMutationResult)
def transfer(
    membership_id: UUID, input: TransferTeamInput, actor: Actor, service: Memberships
) -> TeamMutationResult:
    return service.transfer(actor, membership_id, input)


@router.post("/teams/{team_id}/manager-assignments", response_model=TeamMutationResult)
def assign(
    team_id: UUID, input: AssignManagerInput, actor: Actor, service: Assignments
) -> TeamMutationResult:
    return service.assign(actor, team_id, input)


@router.post("/manager-assignments/{assignment_id}/end", response_model=TeamMutationResult)
def unassign(
    assignment_id: UUID, input: UnassignManagerInput, actor: Actor, service: Assignments
) -> TeamMutationResult:
    return service.unassign(actor, assignment_id, input)


@router.get("/teams/{team_id}/staff-candidates", response_model=ManagerCandidatePage)
def staff_candidates(
    team_id: UUID,
    actor: Actor,
    service: Queries,
    staff_role: StaffRole,
    query: SearchQuery,
    cursor: str | None = None,
    page_size: CandidateSize = 20,
) -> ManagerCandidatePage:
    page = service.candidates(
        actor,
        team_id,
        kind="staff",
        staff_role=staff_role,
        query=query,
        cursor=cursor,
        page_size=page_size,
    )
    return ManagerCandidatePage.model_validate(page.model_dump())


@router.get("/teams/{team_id}/staff-assignments", response_model=TeamPage[TeamStaffAssignmentView])
def staff_assignments(
    team_id: UUID,
    actor: Actor,
    service: Queries,
    status: Literal["current", "ended"] = "current",
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> TeamPage[TeamStaffAssignmentView]:
    return service.list_staff_assignments(
        actor, team_id, status=status, page=page, page_size=page_size
    )


@router.post("/teams/{team_id}/staff-assignments", response_model=TeamMutationResult)
def assign_staff(
    team_id: UUID, input: AssignStaffInput, actor: Actor, service: StaffAssignments
) -> TeamMutationResult:
    return service.assign(actor, team_id, input)


@router.post("/staff-assignments/{assignment_id}/end", response_model=TeamMutationResult)
def unassign_staff(
    assignment_id: UUID, input: UnassignStaffInput, actor: Actor, service: StaffAssignments
) -> TeamMutationResult:
    return service.unassign(actor, assignment_id, input)


router.include_router(build_person_staff_router(PrivateTeamRoute))
