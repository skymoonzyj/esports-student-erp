from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.portal.coach_service import PortalCoachService
from app.portal.context_service import PortalContextService
from app.portal.learning_service import PortalLearningService
from app.portal.payment_service import PortalPaymentService


def get_portal_context_service(
    db: Annotated[Session, Depends(get_db)],
) -> PortalContextService:
    return PortalContextService(db)


def get_portal_coach_service(
    db: Annotated[Session, Depends(get_db)],
) -> PortalCoachService:
    return PortalCoachService(db)


def get_portal_learning_service(
    db: Annotated[Session, Depends(get_db)],
) -> PortalLearningService:
    return PortalLearningService(db)


def get_portal_payment_service(
    db: Annotated[Session, Depends(get_db)],
) -> PortalPaymentService:
    return PortalPaymentService(db)
