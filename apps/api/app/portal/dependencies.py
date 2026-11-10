import base64
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db
from app.portal.coach_service import PortalCoachService
from app.portal.context_service import PortalContextService
from app.portal.departure_service import PortalDepartureService
from app.portal.learning_service import PortalLearningService
from app.portal.payment_service import PortalPaymentService
from app.security.encryption import EnvelopeCipher, LookupDigest


def get_portal_departure_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> PortalDepartureService:
    settings: Settings = request.app.state.settings
    return PortalDepartureService(
        db,
        cipher=EnvelopeCipher(
            base64.b64decode(
                settings.field_encryption_master_key.get_secret_value(),
                altchars=b"-_",
                validate=True,
            )
        ),
        lookup=LookupDigest(
            base64.b64decode(
                settings.field_lookup_key.get_secret_value(),
                altchars=b"-_",
                validate=True,
            )
        ),
    )


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
