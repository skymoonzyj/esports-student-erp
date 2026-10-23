from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.housing.api_support import (
    PrivateHousingRoute,
    _deny_mutation,
    _not_found,
    _set_sensitive_read_result_count,
    _set_sensitive_read_room,
)
from app.housing.dependencies import get_housing_cipher
from app.housing.models import HousingRoom, HousingRoomPhoto
from app.housing.occupancy_router import (
    _access,
    _authorize_locked_room_care,
    _execute_mutation,
    _mutation_context,
    _sensitive_read_context,
    room_detail,
)
from app.housing.room_photo_images import (
    MAX_PHOTO_BYTES,
    RoomPhotoValidationError,
    prepare_room_photo,
)
from app.housing.room_photo_service import HousingRoomPhotoService
from app.housing.schemas import HousingRoomDetail
from app.security.encryption import EnvelopeCipher

router = APIRouter(prefix="/housing", tags=["housing"], route_class=PrivateHousingRoute)


class RoomPhotoDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)


def _precheck(
    db: Session,
    request: Request,
    context: AuthContext,
    room_id: UUID,
    *,
    action: str,
    photo_id: UUID | None,
    metadata: dict[str, object],
) -> None:
    access = _access(db, context, datetime.now(UTC))
    room = db.get(HousingRoom, room_id)
    if room is None or not access.can_read_room(room):
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type="housing_room_photo",
            resource_id=photo_id,
            metadata=metadata,
            not_found=True,
        )
    if not access.can_manage_room_care(room):
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type="housing_room_photo",
            resource_id=photo_id,
            metadata=metadata,
        )


@router.put("/rooms/{room_id}/photos/{slot}", response_model=HousingRoomDetail)
def put_room_photo(
    room_id: UUID,
    slot: Annotated[int, Path(ge=1, le=3)],
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.room_photo.put"))],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    file: Annotated[UploadFile, File()],
    version: Annotated[int, Form(ge=1)],
) -> HousingRoomDetail:
    action = "housing.room_photo.put"
    metadata: dict[str, object] = {"room_id": str(room_id), "slot": slot, "version": version}
    _precheck(db, request, context, room_id, action=action, photo_id=None, metadata=metadata)
    # CPU decoding/encoding and bounded stream read precede the write transaction.
    content = file.file.read(MAX_PHOTO_BYTES + 1)
    try:
        prepared = prepare_room_photo(content, file.content_type)
    except RoomPhotoValidationError as exc:
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="照片无效；仅支持 10 MiB 以内的 JPEG、PNG、WebP 单帧图片",
        ) from exc
    occurred_at = datetime.now(UTC)
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_room_photo",
        resource_id=None,
        metadata=metadata,
        operation=lambda: HousingRoomPhotoService(db).put(
            room_id,
            slot,
            prepared,
            version=version,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room_care(db, context, occurred_at, room),
        ),
        serialize=lambda _photo: room_detail(room_id, request, context, db, cipher),
        success_audit=lambda photo: (
            "housing_room_photo",
            photo.id,
            {**metadata, "photo_id": str(photo.id)},
        ),
    )


@router.delete("/rooms/{room_id}/photos/{photo_id}", response_model=HousingRoomDetail)
def delete_room_photo(
    room_id: UUID,
    photo_id: UUID,
    input: RoomPhotoDeleteRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(_mutation_context("housing.room_photo.delete"))],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
) -> HousingRoomDetail:
    action = "housing.room_photo.delete"
    metadata: dict[str, object] = {
        "room_id": str(room_id),
        "photo_id": str(photo_id),
        "version": input.version,
    }
    _precheck(db, request, context, room_id, action=action, photo_id=photo_id, metadata=metadata)
    occurred_at = datetime.now(UTC)
    return _execute_mutation(
        db,
        request,
        context,
        action=action,
        resource_type="housing_room_photo",
        resource_id=photo_id,
        metadata=metadata,
        operation=lambda: HousingRoomPhotoService(db).delete(
            room_id,
            photo_id,
            version=input.version,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            authorize_room=lambda room: _authorize_locked_room_care(db, context, occurred_at, room),
        ),
        serialize=lambda _result: room_detail(room_id, request, context, db, cipher),
    )


@router.get("/rooms/{room_id}/photos/{photo_id}")
def read_room_photo(
    room_id: UUID,
    photo_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(_sensitive_read_context("housing.room_photo.read"))],
    db: Annotated[Session, Depends(get_db)],
    variant: Literal["original", "thumbnail"] = "original",
) -> Response:
    _set_sensitive_read_room(request, room_id)
    access = _access(db, context, datetime.now(UTC))
    room = db.get(HousingRoom, room_id)
    if room is None or not access.can_read_room(room):
        _not_found()
    # Full room permissions only: no owner/person/invitation-summary fallback.
    columns = (
        (HousingRoomPhoto.content, HousingRoomPhoto.mime_type)
        if variant == "original"
        else (HousingRoomPhoto.thumbnail_content, HousingRoomPhoto.thumbnail_mime_type)
    )
    photo = db.execute(
        select(*columns).where(
            HousingRoomPhoto.id == photo_id,
            HousingRoomPhoto.room_id == room_id,
        )
    ).one_or_none()
    if photo is None:
        _not_found()
    _set_sensitive_read_result_count(request, 1)
    return Response(
        content=photo[0],
        media_type=photo[1],
        headers={"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"},
    )
