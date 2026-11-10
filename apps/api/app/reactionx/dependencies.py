from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.reactionx.models import ReactionxConnector
from app.reactionx.pairing import PairingService

Database = Annotated[Session, Depends(get_db)]
Actor = Annotated[AuthContext, Depends(require_standard_account)]


def require_connector(request: Request, db: Database) -> ReactionxConnector:
    return pairing_service(request, db).authenticate(request.headers.get("Authorization"))


def require_pairing_connector(request: Request, db: Database) -> ReactionxConnector:
    return pairing_service(request, db).authenticate(
        request.headers.get("Authorization"), allow_pending=True
    )


def pairing_service(request: Request, db: Session) -> PairingService:
    return PairingService(db, origin=request.app.state.settings.reactionx_public_origin)


Connector = Annotated[ReactionxConnector, Depends(require_connector)]
PairingConnector = Annotated[ReactionxConnector, Depends(require_pairing_connector)]
