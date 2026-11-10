from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.identity.router import get_identity_service
from app.identity.service import IdentityService
from app.team_management.access import TeamAccessService
from app.team_management.assignment_service import TeamAssignmentService
from app.team_management.membership_service import TeamMembershipService
from app.team_management.query_service import TeamQueryService

Database = Annotated[Session, Depends(get_db)]


def get_team_access(db: Database) -> TeamAccessService:
    return TeamAccessService(db)


def get_team_queries(
    db: Database, identity: Annotated[IdentityService, Depends(get_identity_service)]
) -> TeamQueryService:
    return TeamQueryService(db, cipher=identity.cipher, lookup=identity.lookup)


def get_team_memberships(
    db: Database, identity: Annotated[IdentityService, Depends(get_identity_service)]
) -> TeamMembershipService:
    return TeamMembershipService(db, cipher=identity.cipher, lookup=identity.lookup)


def get_team_assignments(db: Database) -> TeamAssignmentService:
    return TeamAssignmentService(db)
