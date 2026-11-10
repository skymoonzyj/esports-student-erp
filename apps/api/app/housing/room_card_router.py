from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.housing.access import HousingAccessService
from app.housing.api_support import (
    LockedHousingAuthorizationDenied,
    PrivateHousingRoute,
    _audit,
    _mark_request_audited,
    _standalone_audit,
)
from app.housing.models import HousingRoom
from app.housing.occupancy_router import _mutation_context, _require_current_care_actor
from app.housing.room_card_schemas import RoomCardItem, RoomCardList, RoomCardSave
from app.housing.room_card_service import HousingRoomCardService

router = APIRouter(prefix="/housing", tags=["housing"], route_class=PrivateHousingRoute)


@router.get("/rooms/{room_id}/room-cards", response_model=RoomCardList)
def list_room_cards(
    room_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> RoomCardList:
    now = datetime.now(UTC)
    return HousingRoomCardService(db).list_room(
        room_id, HousingAccessService(db, context.account.id, now), now
    )


@router.put("/stays/{stay_id}/room-card", response_model=RoomCardItem)
def save_room_card(
    stay_id: UUID,
    input: RoomCardSave,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.room_card.save"))],
    db: Annotated[Session, Depends(get_db)],
) -> RoomCardItem:
    def authorize(room: HousingRoom) -> None:
        try:
            _require_current_care_actor(db, context)
        except LockedHousingAuthorizationDenied:
            raise ProblemError(
                status=403, code="permission_denied", detail="没有房卡编辑权限"
            ) from None
        access = HousingAccessService(db, context.account.id, datetime.now(UTC))
        if not access.can_manage_keycards(room):
            raise ProblemError(status=403, code="permission_denied", detail="没有房卡编辑权限")

    try:
        with atomic_write_transaction(db):
            result, _room_id, metadata = HousingRoomCardService(db).save(
                stay_id,
                input,
                actor_user_id=context.account.id,
                observed_at=datetime.now(UTC),
                authorize=authorize,
            )
            _audit(
                db,
                request,
                context,
                action="housing.room_card.save",
                resource_type="housing_room_card",
                resource_id=stay_id,
                outcome="allowed",
                metadata=metadata,
            )
            _mark_request_audited(request)
        return result
    except ProblemError as exc:
        _standalone_audit(
            db,
            request,
            context,
            action="housing.room_card.save",
            resource_type="housing_room_card",
            resource_id=stay_id,
            outcome="denied" if exc.status in {403, 404} else "failed",
            metadata={"error_code": exc.code},
        )
        _mark_request_audited(request)
        raise
