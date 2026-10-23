from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.team_management.allocation import TeamAllocationService
from app.team_management.router import PrivateTeamRoute
from app.team_management.schemas import TeamAllocationInput, TeamAllocationProjection

router = APIRouter(tags=["people"], route_class=PrivateTeamRoute)


@router.patch("/people/{person_id}/team-allocation", response_model=TeamAllocationProjection)
def update_team_allocation(
    person_id: UUID,
    input: TeamAllocationInput,
    actor: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> TeamAllocationProjection:
    return TeamAllocationService(db).update(actor, person_id, input)
