from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.core.database import get_db
from app.housing.api_support import PrivateHousingRoute, _deny_mutation
from app.housing.cleaning_service import HousingCleaningService
from app.housing.inventory_service import HousingInventoryService
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom
from app.housing.occupancy_router import (
    _access,
    _authorize_locked_room_care,
    _execute_mutation,
    _mutation_context,
)
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.schemas import HousingRoomCard, RoomCleaningComplete

router = APIRouter(prefix="/housing", tags=["housing"], route_class=PrivateHousingRoute)


@router.post("/rooms/{room_id}/cleaning/complete", response_model=HousingRoomCard)
def complete_cleaning(
    room_id: UUID,
    input: RoomCleaningComplete,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.room.cleaning.complete"))],
    db: Annotated[Session, Depends(get_db)],
) -> HousingRoomCard:
    action = "housing.room.cleaning.complete"
    occurred_at = datetime.now(UTC)
    access = _access(db, context, occurred_at)
    metadata = {"room_id": str(room_id), "version": input.version}
    room = db.get(HousingRoom, room_id)
    if room is None or not access.can_read_room(room):
        _deny_mutation(db, request, context, action=action, resource_type="housing_room",
                       resource_id=room_id, metadata=metadata, not_found=True)
    if not access.can_manage_room_care(room):
        _deny_mutation(db, request, context, action=action, resource_type="housing_room",
                       resource_id=room_id, metadata=metadata)

    def serialize(result: HousingRoom) -> HousingRoomCard:
        building = db.get(HousingBuilding, result.building_id)
        floor = db.get(HousingFloor, result.floor_id)
        assert building is not None and floor is not None
        projection = HousingOccupancyService(db).room_projections([room_id], occurred_at)[room_id]
        return HousingInventoryService._room_card(
            result, building, floor, _access(db, context, datetime.now(UTC)), projection
        )

    return _execute_mutation(
        db, request, context, action=action, resource_type="housing_room", resource_id=room_id,
        metadata=metadata,
        operation=lambda: HousingCleaningService(db).complete(
            room_id, version=input.version, actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda locked: _authorize_locked_room_care(
                db, context, occurred_at, locked
            ),
        ),
        serialize=serialize,
        audit_when=lambda result: result.version != input.version,
    )
