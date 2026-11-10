from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.housing.api_support import PrivateHousingRoute, _audit, _failed_mutation, _standalone_audit
from app.housing.errors import HousingServiceError
from app.housing.occupancy_router import _mutation_context
from app.housing.property_schemas import (
    PropertyChangeCreate,
    PropertyChangeItem,
    PropertyChangeList,
    PropertyReview,
)
from app.housing.property_service import HousingPropertyService
from app.housing.room_status_export_router import _current_access

router = APIRouter(prefix="/housing", tags=["housing"], route_class=PrivateHousingRoute)


@router.get("/property/capabilities")
def capabilities(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    room_id: UUID | None = None,
) -> dict[str, bool]:
    return HousingPropertyService(db).capabilities(_current_access(db, context), room_id=room_id)


@router.get("/property/changes", response_model=PropertyChangeList)
def changes(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    status: Literal["pending", "approved", "rejected", "withdrawn"] = "pending",
    view: Literal["all", "submitted", "review"] = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    room_id: UUID | None = None,
) -> PropertyChangeList:
    return HousingPropertyService(db).list(
        _current_access(db, context),
        context.account.id,
        status=status,
        room_id=room_id,
        page=page,
        page_size=page_size,
        view=view,
    )


def _mutate(
    operation: str,
    resource_id: UUID,
    input: PropertyChangeCreate | PropertyReview | None,
    request: Request,
    context: AuthContext,
    db: Session,
) -> PropertyChangeItem:
    service, now = HousingPropertyService(db), datetime.now(UTC)
    try:
        with atomic_write_transaction(db):
            _current_access(db, context)
            if operation == "submit":
                assert isinstance(input, PropertyChangeCreate)
                row = service.submit(
                    resource_id, input, actor_id=context.account.id, occurred_at=now
                )
            elif operation == "review":
                assert isinstance(input, PropertyReview)
                row = service.review(
                    resource_id, input, actor_id=context.account.id, occurred_at=now
                )
            else:
                row = service.withdraw(resource_id, actor_id=context.account.id, occurred_at=now)
            _audit(
                db,
                request,
                context,
                action="housing.property_change." + operation,
                resource_type="housing_property_change",
                resource_id=row.id,
                outcome="allowed",
                metadata={
                    "room_id": str(row.room_id),
                    **({"old_version": row.base_version} if operation == "submit" else {}),
                    **(
                        {"housing_change_decision": input.decision}
                        if isinstance(input, PropertyReview)
                        else {}
                    ),
                },
            )
            return service.item(row, _current_access(db, context), context.account.id)
    except HousingServiceError as exc:
        _failed_mutation(
            db,
            request,
            context,
            exc,
            action="housing.property_change." + operation,
            resource_type="housing_property_change",
            resource_id=resource_id,
            metadata={},
        )
        raise
    except ProblemError as exc:
        _standalone_audit(
            db,
            request,
            context,
            action="housing.property_change." + operation,
            resource_type="housing_property_change",
            resource_id=resource_id,
            outcome="denied" if exc.status in {401, 403, 404} else "failed",
            metadata={"error_code": exc.code},
        )
        raise


@router.post("/rooms/{room_id}/change-requests", response_model=PropertyChangeItem, status_code=201)
def submit(
    room_id: UUID,
    input: PropertyChangeCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.property_change.submit"))],
    db: Annotated[Session, Depends(get_db)],
) -> PropertyChangeItem:
    return _mutate("submit", room_id, input, request, context, db)


@router.post("/change-requests/{request_id}/review", response_model=PropertyChangeItem)
def review(
    request_id: UUID,
    input: PropertyReview,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.property_change.review"))],
    db: Annotated[Session, Depends(get_db)],
) -> PropertyChangeItem:
    return _mutate("review", request_id, input, request, context, db)


@router.post("/change-requests/{request_id}/withdraw", response_model=PropertyChangeItem)
def withdraw(
    request_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.property_change.withdraw"))],
    db: Annotated[Session, Depends(get_db)],
) -> PropertyChangeItem:
    return _mutate("withdraw", request_id, None, request, context, db)
