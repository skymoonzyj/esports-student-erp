from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.housing.api_support import (
    LockedHousingAuthorizationDenied,
    PrivateHousingRoute,
    _deny_mutation,
    _failed_mutation,
    _mark_request_audited,
    _raise_housing_error,
)
from app.housing.dependencies import get_housing_cipher, get_housing_lookup
from app.housing.errors import (
    HousingPersistenceFailure,
    HousingResourceNotFound,
    HousingServiceError,
    HousingStayAlreadyVoided,
)
from app.housing.history_void_schemas import StayHistoryVoidRequest, StayHistoryVoidResult
from app.housing.history_void_service import HousingHistoryVoidService
from app.security.encryption import EnvelopeCipher, LookupDigest

router = APIRouter(prefix="/housing", tags=["housing"], route_class=PrivateHousingRoute)
_ACTION = "housing.stay.void_history"


class _AuditArgs(TypedDict):
    action: str
    resource_type: str
    resource_id: UUID | None
    metadata: dict[str, Any]


def _context(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    request.state.housing_mutation_context = context
    request.state.housing_mutation_db = db
    request.state.housing_mutation_action = _ACTION
    return context


@router.post("/stays/{stay_id}/void-history", response_model=StayHistoryVoidResult)
def void_history(
    stay_id: UUID,
    command: StayHistoryVoidRequest,
    request: Request,
    actor: Annotated[AuthContext, Depends(_context)],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> StayHistoryVoidResult:
    audit_args: _AuditArgs = {
        "action": _ACTION,
        "resource_type": "housing_stay",
        "resource_id": stay_id,
        "metadata": {},
    }
    try:
        result = HousingHistoryVoidService(
            db,
            cipher,
            lookup,
            occurred_at=datetime.now(UTC),
            request_id=str(request.state.request_id),
        ).void(actor, stay_id, command)
    except LockedHousingAuthorizationDenied as exc:
        _deny_mutation(db, request, actor, not_found=exc.not_found, **audit_args)
    except HousingResourceNotFound:
        _deny_mutation(db, request, actor, not_found=True, **audit_args)
    except HousingStayAlreadyVoided as exc:
        # An authorized no-op must not create another audit or operation credential.
        _mark_request_audited(request)
        _raise_housing_error(exc)
    except HousingServiceError as exc:
        _failed_mutation(db, request, actor, exc, **audit_args)
    except DBAPIError:
        _failed_mutation(
            db, request, actor, HousingPersistenceFailure("housing write failed"), **audit_args
        )
    except AuditMetadataRejected:
        _mark_request_audited(request)
        raise ProblemError(
            status=500, code="housing_audit_failed", detail="房态审计记录失败"
        ) from None
    _mark_request_audited(request)
    return result
