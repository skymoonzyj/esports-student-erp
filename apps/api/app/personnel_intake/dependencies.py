from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import _secret_bytes
from app.core.database import get_db
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.approval_service import PersonnelApprovalService
from app.personnel_intake.service import PersonnelIntakeService
from app.security.encryption import EnvelopeCipher, LookupDigest


def get_intake_service(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> PersonnelIntakeService:
    settings = request.app.state.settings
    return PersonnelIntakeService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
    )


def get_intake_access(db: Annotated[Session, Depends(get_db)]) -> PersonnelIntakeAccess:
    return PersonnelIntakeAccess(db)


def get_approval_service(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> PersonnelApprovalService:
    settings = request.app.state.settings
    return PersonnelApprovalService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
    )
