from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.housing.errors import HousingResourceNotFound, HousingStaleVersion
from app.housing.models import HousingRoom


def mark_room_pending_cleaning(room: HousingRoom, *, occurred_at: datetime) -> None:
    """Caller owns the room lock and transaction; every departure invalidates old work."""
    room.cleaning_status = "pending"
    room.cleaning_pending_at = occurred_at
    room.version += 1


class HousingCleaningService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def complete(
        self,
        room_id: UUID,
        *,
        version: int,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: Callable[[HousingRoom], None],
    ) -> HousingRoom:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("cleaning timestamp must be timezone aware")
        with write_transaction(self.db):
            room = self.db.scalar(
                select(HousingRoom)
                .where(HousingRoom.id == room_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if room is None:
                raise HousingResourceNotFound("housing room not found")
            authorize_room(room)
            if room.version != version:
                raise HousingStaleVersion("housing room version is stale")
            if room.cleaning_status == "pending":
                room.cleaning_status = "ready"
                room.cleaning_completed_at = occurred_at
                room.cleaning_completed_by_user_id = actor_user_id
                room.updated_by_user_id = actor_user_id
                room.version += 1
                self.db.flush()
        return room
