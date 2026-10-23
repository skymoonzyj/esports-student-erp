from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.housing.errors import HousingResourceNotFound, HousingServiceError, HousingStaleVersion
from app.housing.models import HousingRoom, HousingRoomPhoto
from app.housing.room_photo_images import PreparedRoomPhoto


class HousingRoomPhotoService:
    """Room-serialized writes; the route owns the enclosing atomic audit transaction."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _locked_room(self, room_id: UUID) -> HousingRoom:
        room = self.db.scalar(
            select(HousingRoom)
            .where(HousingRoom.id == room_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if room is None:
            raise HousingResourceNotFound("housing room not found")
        return room

    @staticmethod
    def _validate(version: int, occurred_at: datetime) -> None:
        if type(version) is not int or version < 1:
            raise HousingServiceError("positive room version required")
        if occurred_at.utcoffset() is None:
            raise HousingServiceError("housing timestamps must be timezone-aware")

    def put(
        self,
        room_id: UUID,
        slot: int,
        prepared: PreparedRoomPhoto,
        *,
        version: int,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: Callable[[HousingRoom], None],
    ) -> HousingRoomPhoto:
        self._validate(version, occurred_at)
        if type(slot) is not int or not 1 <= slot <= 3:
            raise HousingServiceError("photo slot must be between one and three")
        with write_transaction(self.db):
            room = self._locked_room(room_id)
            authorize_room(room)
            if room.version != version:
                raise HousingStaleVersion("housing room version is stale")
            photo = self.db.scalar(
                select(HousingRoomPhoto).where(
                    HousingRoomPhoto.room_id == room.id,
                    HousingRoomPhoto.slot == slot,
                )
            )
            if photo is None:
                photo = HousingRoomPhoto(room_id=room.id, slot=slot)
                self.db.add(photo)
            photo.content = prepared.content
            photo.mime_type = prepared.mime_type
            photo.byte_size = prepared.byte_size
            photo.sha256 = prepared.sha256
            photo.thumbnail_content = prepared.thumbnail_content
            photo.thumbnail_mime_type = prepared.thumbnail_mime_type
            photo.thumbnail_byte_size = prepared.thumbnail_byte_size
            photo.uploaded_by_user_id = actor_user_id
            photo.updated_at = occurred_at
            room.version += 1
            room.updated_by_user_id = actor_user_id
            room.updated_at = occurred_at
            self.db.flush()
        return photo

    def delete(
        self,
        room_id: UUID,
        photo_id: UUID,
        *,
        version: int,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: Callable[[HousingRoom], None],
    ) -> None:
        self._validate(version, occurred_at)
        with write_transaction(self.db):
            room = self._locked_room(room_id)
            authorize_room(room)
            # Check the room epoch before target lookup, including delete/recreate ABA.
            if room.version != version:
                raise HousingStaleVersion("housing room version is stale")
            photo = self.db.scalar(
                select(HousingRoomPhoto).where(
                    HousingRoomPhoto.room_id == room.id,
                    HousingRoomPhoto.id == photo_id,
                )
            )
            if photo is None:
                raise HousingResourceNotFound("housing room photo not found")
            self.db.delete(photo)
            room.version += 1
            room.updated_by_user_id = actor_user_id
            room.updated_at = occurred_at
            self.db.flush()
