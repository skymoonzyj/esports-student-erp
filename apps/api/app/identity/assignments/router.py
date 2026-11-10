from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.identity.assignments.schemas import AssignmentInput, AssignmentView
from app.identity.assignments.service import PersonAssignmentService
from app.identity.router import PrivateIdentityRoute, get_identity_service
from app.identity.service import IdentityService

router = APIRouter(tags=["people"], route_class=PrivateIdentityRoute)


def get_service(
    identity: Annotated[IdentityService, Depends(get_identity_service)],
) -> PersonAssignmentService:
    return PersonAssignmentService(identity.db, cipher=identity.cipher, lookup=identity.lookup)


@router.get("/people/{person_id}/assignments", response_model=AssignmentView)
def get_assignments(
    person_id: UUID,
    actor: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PersonAssignmentService, Depends(get_service)],
) -> AssignmentView:
    return service.get(actor, person_id)


@router.put("/people/{person_id}/assignments", response_model=AssignmentView)
def replace_assignments(
    person_id: UUID,
    input: AssignmentInput,
    actor: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PersonAssignmentService, Depends(get_service)],
) -> AssignmentView:
    return service.replace(actor, person_id, input)
