from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.team_management.access import TeamAccessService
from app.team_management.assignment_service import TeamAssignmentService
from app.team_management.membership_service import TeamMembershipService
from app.team_management.query_service import TeamQueryService

Database = Annotated[Session, Depends(get_db)]


def get_team_access(db: Database) -> TeamAccessService:
    return TeamAccessService(db)


def get_team_queries(db: Database) -> TeamQueryService:
    return TeamQueryService(db)


def get_team_memberships(db: Database) -> TeamMembershipService:
    return TeamMembershipService(db)


def get_team_assignments(db: Database) -> TeamAssignmentService:
    return TeamAssignmentService(db)
