"""Private personnel staff roster and maintenance routes."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.identity.router import get_identity_service
from app.identity.service import IdentityService
from app.team_management.person_staff_query import PersonStaffQuery
from app.team_management.person_staff_schemas import (
    PersonStaffAssignInput,
    PersonStaffCandidatePage,
    PersonStaffEndInput,
    PersonStaffReconfirmInput,
    PersonStaffRow,
    StaffRole,
)
from app.team_management.person_staff_service import PersonStaffService
from app.team_management.schemas import TeamMutationResult, TeamPage

Database = Annotated[Session, Depends(get_db)]
Identity = Annotated[IdentityService, Depends(get_identity_service)]
Actor = Annotated[AuthContext, Depends(require_standard_account)]


def get_person_staff(db: Database, identity: Identity) -> PersonStaffService:
    return PersonStaffService(db, lookup=identity.lookup)


def get_person_staff_query(db: Database, identity: Identity) -> PersonStaffQuery:
    return PersonStaffQuery(db, lookup=identity.lookup)


Commands = Annotated[PersonStaffService, Depends(get_person_staff)]
Queries = Annotated[PersonStaffQuery, Depends(get_person_staff_query)]


def roster(
    team_id: UUID,
    actor: Actor,
    service: Queries,
    status: Literal["current", "ended"] = "current",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TeamPage[PersonStaffRow]:
    return service.list(actor, team_id, status=status, page=page, page_size=page_size)


def candidates(
    team_id: UUID,
    actor: Actor,
    service: Queries,
    staff_role: StaffRole,
    query: Annotated[str, Query(min_length=1, max_length=120)],
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=20)] = 20,
) -> PersonStaffCandidatePage:
    return service.candidates(
        actor, team_id, staff_role=staff_role, query=query, cursor=cursor, page_size=page_size
    )


def assign(
    team_id: UUID, input: PersonStaffAssignInput, actor: Actor, service: Commands
) -> TeamMutationResult:
    return service.assign_person(actor, team_id, input)


def end(
    position_id: UUID, input: PersonStaffEndInput, actor: Actor, service: Commands
) -> TeamMutationResult:
    return service.end(actor, position_id, input)


def reconfirm(
    position_id: UUID, input: PersonStaffReconfirmInput, actor: Actor, service: Commands
) -> TeamMutationResult:
    return service.reconfirm(actor, position_id, input)


def build_person_staff_router(route_class: type[APIRoute]) -> APIRouter:
    """Receive the shared privacy route wrapper without importing its owner."""
    router = APIRouter(route_class=route_class)
    router.add_api_route(
        "/teams/{team_id}/staff-positions",
        roster,
        methods=["GET"],
        response_model=TeamPage[PersonStaffRow],
    )
    router.add_api_route(
        "/teams/{team_id}/person-staff-candidates",
        candidates,
        methods=["GET"],
        response_model=PersonStaffCandidatePage,
    )
    router.add_api_route(
        "/teams/{team_id}/person-staff-positions",
        assign,
        methods=["POST"],
        response_model=TeamMutationResult,
    )
    router.add_api_route(
        "/person-staff-positions/{position_id}/end",
        end,
        methods=["POST"],
        response_model=TeamMutationResult,
    )
    router.add_api_route(
        "/person-staff-positions/{position_id}/reconfirm",
        reconfirm,
        methods=["POST"],
        response_model=TeamMutationResult,
    )
    return router
