from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.housing.access import REPORT_PERMISSION, HousingAccessService
from app.housing.api_support import (
    LockedHousingAuthorizationDenied as _LockedAuthorizationDenied,
)
from app.housing.api_support import (
    PrivateHousingRoute,
    _audit,
    _deny_mutation,
    _failed_mutation,
    _not_found,
    _permission_denied,
)
from app.housing.dependencies import get_housing_cipher, get_housing_lookup
from app.housing.errors import (
    HousingInvalidRoomConfiguration,
    HousingResourceNotFound,
    HousingServiceError,
)
from app.housing.inventory_service import HousingInventoryService, HousingOverviewFilters
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom
from app.housing.occupancy_export_service import (
    HousingOccupancyExportService,
    render_occupancy_export,
)
from app.housing.occupancy_router import _authorize_locked_room_care, _require_current_care_actor
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.room_status_export_router import _current_access
from app.housing.schemas import (
    BuildingCreate,
    BuildingResponse,
    BuildingUpdate,
    CleaningFrequency,
    CleaningStatus,
    FloorCreate,
    FloorResponse,
    FloorUpdate,
    HousingCatalogResponse,
    HousingOccupancyExportRequest,
    HousingOverviewResponse,
    HousingQuickFilter,
    HousingRoomCard,
    RoomBatchServiceStatusRequest,
    RoomBatchVerifyRequest,
    RoomCreate,
    RoomResponse,
    RoomType,
    RoomUpdate,
    RoomVerifyRequest,
    ServiceStatus,
    VerificationStatus,
)
from app.security.encryption import EnvelopeCipher, LookupDigest

CurrentGender = Literal["male", "female", "unspecified"]
Occupancy = Literal["occupied", "unoccupied"]
Assignability = Literal["assignable", "unassignable", "full"]


router = APIRouter(
    prefix="/housing",
    tags=["housing"],
    route_class=PrivateHousingRoute,
)


def _access(db: Session, context: AuthContext, occurred_at: datetime) -> HousingAccessService:
    return HousingAccessService(db, context.account.id, occurred_at)


def _visible_room(
    db: Session,
    access: HousingAccessService,
    room_id: UUID,
) -> HousingRoom:
    room = db.get(HousingRoom, room_id)
    if room is None or not access.can_read_room(room):
        _not_found()
    return room


def _visible_floor(
    db: Session,
    access: HousingAccessService,
    floor_id: UUID,
) -> HousingFloor:
    floor = db.get(HousingFloor, floor_id)
    if floor is None or not access.can_read_floor(floor):
        _not_found()
    return floor


def _visible_building(
    db: Session,
    access: HousingAccessService,
    building_id: UUID,
) -> HousingBuilding:
    building = db.get(HousingBuilding, building_id)
    if building is None or not access.can_read_building(building):
        _not_found()
    return building


def _catalog_or_deny(
    service: HousingInventoryService,
    access: HousingAccessService,
) -> HousingCatalogResponse:
    catalog = service.visible_catalog(access)
    if not access.can("housing.read", {"global"}) and not catalog.buildings:
        _permission_denied()
    return catalog


def _batch_scope(rooms: Sequence[HousingRoom]) -> dict[str, Any]:
    building_ids = {room.building_id for room in rooms}
    floor_ids = {room.floor_id for room in rooms}
    if len(floor_ids) == 1:
        return {"scope_kind": "floor", "scope_id": str(next(iter(floor_ids)))}
    if len(building_ids) == 1:
        return {"scope_kind": "building", "scope_id": str(next(iter(building_ids)))}
    return {"scope_kind": "global"}


def _authorize_locked_room(
    access: HousingAccessService,
    room: HousingRoom,
) -> None:
    if not access.can_read_room(room):
        raise _LockedAuthorizationDenied(not_found=True)
    if not access.can_manage_room(room):
        raise _LockedAuthorizationDenied(not_found=False)


def _authorize_locked_floor(
    access: HousingAccessService,
    floor: HousingFloor,
) -> None:
    if not access.can_read_floor(floor):
        raise _LockedAuthorizationDenied(not_found=True)
    if not access.can_manage_floor(floor):
        raise _LockedAuthorizationDenied(not_found=False)


def _authorize_locked_rooms(
    access: HousingAccessService,
    rooms: Sequence[HousingRoom],
) -> None:
    if any(not access.can_read_room(room) for room in rooms):
        raise _LockedAuthorizationDenied(not_found=True)
    if any(not access.can_manage_room(room) for room in rooms):
        raise _LockedAuthorizationDenied(not_found=False)


@router.get("/catalog", response_model=HousingCatalogResponse)
def catalog(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> HousingCatalogResponse:
    occurred_at = datetime.now(UTC)
    access = _access(db, context, occurred_at)
    service = HousingInventoryService(db)
    return _catalog_or_deny(service, access)


@router.get("/overview", response_model=HousingOverviewResponse)
def overview(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    query: Annotated[str | None, Query(max_length=100)] = None,
    room_type: Annotated[list[RoomType] | None, Query()] = None,
    verification_status: Annotated[list[VerificationStatus] | None, Query()] = None,
    service_status: Annotated[list[ServiceStatus] | None, Query()] = None,
    cleaning_statuses: Annotated[list[CleaningStatus] | None, Query()] = None,
    cleaning_frequency: Annotated[list[CleaningFrequency] | None, Query()] = None,
    daily_cleaning_enabled: bool | None = None,
    current_gender: CurrentGender | None = None,
    occupancy: Occupancy | None = None,
    assignability: Assignability | None = None,
    quick_filter: HousingQuickFilter | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> HousingOverviewResponse:
    as_of = datetime.now(UTC)
    access = _access(db, context, as_of)
    service = HousingInventoryService(db)
    if not access.can("housing.read", {"global"}):
        _catalog_or_deny(service, access)
    return service.overview(
        access,
        HousingOverviewFilters(
            building_id=building_id,
            floor_id=floor_id,
            query=query,
            room_types=frozenset(room_type or ()),
            verification_statuses=frozenset(verification_status or ()),
            service_statuses=frozenset(service_status or ()),
            cleaning_statuses=frozenset(cleaning_statuses or ()),
            daily_cleaning_enabled=daily_cleaning_enabled,
            cleaning_frequencies=frozenset(cleaning_frequency or ()),
            current_gender=current_gender,
            occupancy=occupancy,
            assignability=assignability,
            quick_filter=quick_filter,
        ),
        page,
        page_size,
        as_of,
    )


@router.post("/exports")
def export_occupancy(
    input: HousingOccupancyExportRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> Response:
    """Export actual room stays with the sensitive fields authorized by RBAC."""

    occurred_at = datetime.now(UTC)
    access = _current_access(db, context)
    action = "housing.occupancy.export"
    if db.scalar(
        select(HousingFloor.id)
        .where(access.floor_permission_criteria(REPORT_PERMISSION))
        .limit(1)
    ) is None:
        _deny_mutation(
            db,
            request,
            context,
            action=action,
            resource_type="housing_occupancy_export",
            resource_id=None,
            metadata={},
        )

    try:
        rows = HousingOccupancyExportService(
            db,
            cipher=cipher,
            lookup=lookup,
            access=access,
        ).rows(input, as_of=occurred_at)
        content, media_type, filename = render_occupancy_export(
            rows,
            format=input.format,
            occurred_at=occurred_at,
        )
        current_access = _current_access(db, context)
        if access.access_revision != current_access.access_revision:
            raise ProblemError(
                status=409,
                code="housing_access_changed",
                detail="房态权限已发生变化，请刷新后重新导出",
            )
        _audit(
            db,
            request,
            context,
            action=action,
            resource_type="housing_occupancy_export",
            resource_id=None,
            outcome="allowed",
            purpose=input.purpose,
            metadata={
                "format": input.format,
                "item_count": len(rows),
                "file_sha256": sha256(content).hexdigest(),
            },
        )
        db.commit()
    except AuditMetadataRejected as exc:
        db.rollback()
        raise ProblemError(
            status=500,
            code="housing_audit_failed",
            detail="房态审计记录失败",
        ) from exc
    except (DBAPIError, ValueError) as exc:
        db.rollback()
        raise ProblemError(
            status=422,
            code="housing_export_failed",
            detail="房态导出失败，请检查入住实名资料后重试",
        ) from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/rooms/batch-verify", response_model=list[RoomResponse])
def verify_rooms(
    input: RoomBatchVerifyRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> list[RoomResponse]:
    occurred_at = datetime.now(UTC)
    access = _access(db, context, occurred_at)
    requested_ids = [item.room_id for item in input.rooms]
    loaded_rooms = list(
        db.scalars(select(HousingRoom).where(HousingRoom.id.in_(requested_ids)))
    )
    rooms_by_id = {room.id: room for room in loaded_rooms}
    if set(rooms_by_id) != set(requested_ids):
        metadata = {"scope_kind": "global", "result_count": len(input.rooms)}
        _deny_mutation(
            db,
            request,
            context,
            action="housing.room.verify_batch",
            resource_type="housing_room",
            resource_id=None,
            metadata=metadata,
            not_found=True,
        )
    rooms = [rooms_by_id[item.room_id] for item in input.rooms]
    if any(not access.can_read_room(room) for room in rooms):
        metadata = {"scope_kind": "global", "result_count": len(input.rooms)}
        _deny_mutation(
            db,
            request,
            context,
            action="housing.room.verify_batch",
            resource_type="housing_room",
            resource_id=None,
            metadata=metadata,
            not_found=True,
        )
    metadata = {**_batch_scope(rooms), "result_count": len(rooms)}
    if any(not access.can_manage_room(room) for room in rooms):
        _deny_mutation(
            db,
            request,
            context,
            action="housing.room.verify_batch",
            resource_type="housing_room",
            resource_id=None,
            metadata=metadata,
        )
    if any(room.verification_status != "pending_verification" for room in rooms):
        _failed_mutation(
            db,
            request,
            context,
            HousingInvalidRoomConfiguration(
                "housing room verification requires pending rooms"
            ),
            action="housing.room.verify_batch",
            resource_type="housing_room",
            resource_id=None,
            metadata=metadata,
        )

    def authorize_rooms(locked_rooms: Sequence[HousingRoom]) -> None:
        _require_current_care_actor(db, context)
        _authorize_locked_rooms(_access(db, context, datetime.now(UTC)), locked_rooms)

    try:
        db.commit()
        with db.begin():
            verified_rows = HousingInventoryService(db).verify_rooms(
                input.rooms,
                actor_user_id=context.account.id,
                authorize_rooms=authorize_rooms,
            )
            refreshed_by_id = {
                room.id: room
                for room in db.scalars(
                    select(HousingRoom)
                    .where(HousingRoom.id.in_(requested_ids))
                    .execution_options(populate_existing=True)
                )
            }
            verified = [
                RoomResponse.model_validate(refreshed_by_id[room.id])
                for room in verified_rows
            ]
            _audit(
                db,
                request,
                context,
                action="housing.room.verify_batch",
                resource_type="housing_room",
                resource_id=None,
                outcome="allowed",
                metadata=metadata,
            )
    except _LockedAuthorizationDenied as exc:
        _deny_mutation(
            db,
            request,
            context,
            action="housing.room.verify_batch",
            resource_type="housing_room",
            resource_id=None,
            metadata=metadata,
            not_found=exc.not_found,
        )
    except HousingResourceNotFound:
        _deny_mutation(
            db,
            request,
            context,
            action="housing.room.verify_batch",
            resource_type="housing_room",
            resource_id=None,
            metadata=metadata,
            not_found=True,
        )
    except HousingServiceError as exc:
        _failed_mutation(
            db,
            request,
            context,
            exc,
            action="housing.room.verify_batch",
            resource_type="housing_room",
            resource_id=None,
            metadata=metadata,
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="housing_audit_failed",
            detail="房态审计记录失败",
        ) from exc
    return verified


@router.post("/rooms/batch-service-status", response_model=list[RoomResponse])
def update_rooms_service_status(
    input: RoomBatchServiceStatusRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> list[RoomResponse]:
    action = f"housing.room.service_status_batch.{input.service_status}"
    access = _access(db, context, datetime.now(UTC))
    requested_ids = [item.room_id for item in input.rooms]
    rooms_by_id = {
        room.id: room
        for room in db.scalars(select(HousingRoom).where(HousingRoom.id.in_(requested_ids)))
    }
    metadata: dict[str, Any] = {"scope_kind": "global", "result_count": len(input.rooms)}
    if set(rooms_by_id) != set(requested_ids) or any(
        not access.can_read_room(room) for room in rooms_by_id.values()
    ):
        _deny_mutation(
            db, request, context, action=action, resource_type="housing_room",
            resource_id=None, metadata=metadata, not_found=True,
        )
    rooms = [rooms_by_id[item.room_id] for item in input.rooms]
    metadata = {**_batch_scope(rooms), "result_count": len(rooms)}
    if any(not access.can_manage_room(room) for room in rooms):
        _deny_mutation(
            db, request, context, action=action, resource_type="housing_room",
            resource_id=None, metadata=metadata,
        )

    def authorize_rooms(locked_rooms: Sequence[HousingRoom]) -> None:
        _require_current_care_actor(db, context)
        _authorize_locked_rooms(_access(db, context, datetime.now(UTC)), locked_rooms)

    try:
        db.commit()
        with db.begin():
            updated_rows = HousingInventoryService(db).update_rooms_service_status(
                input.rooms,
                service_status=input.service_status,
                actor_user_id=context.account.id,
                authorize_rooms=authorize_rooms,
            )
            refreshed_by_id = {
                room.id: room
                for room in db.scalars(
                    select(HousingRoom)
                    .where(HousingRoom.id.in_(requested_ids))
                    .execution_options(populate_existing=True)
                )
            }
            updated = [RoomResponse.model_validate(refreshed_by_id[room.id]) for room in updated_rows]
            _audit(
                db, request, context, action=action, resource_type="housing_room",
                resource_id=None, outcome="allowed", metadata=metadata,
            )
    except _LockedAuthorizationDenied as exc:
        _deny_mutation(
            db, request, context, action=action, resource_type="housing_room",
            resource_id=None, metadata=metadata, not_found=exc.not_found,
        )
    except HousingResourceNotFound:
        _deny_mutation(
            db, request, context, action=action, resource_type="housing_room",
            resource_id=None, metadata=metadata, not_found=True,
        )
    except HousingServiceError as exc:
        _failed_mutation(
            db, request, context, exc, action=action, resource_type="housing_room",
            resource_id=None, metadata=metadata,
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500, code="housing_audit_failed", detail="房态审计记录失败",
        ) from exc
    return updated


@router.get("/rooms/{room_id}", response_model=HousingRoomCard)
def get_room(
    room_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> HousingRoomCard:
    occurred_at = datetime.now(UTC)
    access = _access(db, context, occurred_at)
    room = _visible_room(db, access, room_id)
    building = db.get(HousingBuilding, room.building_id)
    floor = db.get(HousingFloor, room.floor_id)
    assert building is not None and floor is not None
    projection = HousingOccupancyService(db).room_projections([room.id], occurred_at)[room.id]
    return HousingInventoryService._room_card(room, building, floor, access, projection)


@router.post("/buildings", response_model=BuildingResponse, status_code=201)
def create_building(
    input: BuildingCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> BuildingResponse:
    access = _access(db, context, datetime.now(UTC))
    metadata: dict[str, Any] = {}
    if not access.can_create_building:
        _deny_mutation(
            db,
            request,
            context,
            action="housing.building.create",
            resource_type="housing_building",
            resource_id=None,
            metadata=metadata,
        )
    try:
        db.commit()
        with db.begin():
            building = HousingInventoryService(db).create_building(
                input,
                actor_user_id=context.account.id,
            )
            metadata = {"building_id": str(building.id)}
            _audit(
                db,
                request,
                context,
                action="housing.building.create",
                resource_type="housing_building",
                resource_id=building.id,
                outcome="allowed",
                metadata=metadata,
            )
    except HousingServiceError as exc:
        _failed_mutation(
            db,
            request,
            context,
            exc,
            action="housing.building.create",
            resource_type="housing_building",
            resource_id=None,
            metadata={},
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return BuildingResponse.model_validate(building)


def _authorize_building_mutation(
    building_id: UUID,
    request: Request,
    context: AuthContext,
    db: Session,
    access: HousingAccessService,
    action: str,
) -> HousingBuilding:
    building = db.get(HousingBuilding, building_id)
    metadata = {"building_id": str(building_id)}
    if building is None or not access.can_read_building(building):
        _deny_mutation(db, request, context, action=action, resource_type="housing_building", resource_id=building_id, metadata=metadata, not_found=True)
    if not access.can_manage_building(building):
        _deny_mutation(db, request, context, action=action, resource_type="housing_building", resource_id=building_id, metadata=metadata)
    return building


@router.patch("/buildings/{building_id}", response_model=BuildingResponse)
def update_building(
    building_id: UUID,
    input: BuildingUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> BuildingResponse:
    action = "housing.building.update"
    access = _access(db, context, datetime.now(UTC))
    _authorize_building_mutation(building_id, request, context, db, access, action)
    metadata = {"building_id": str(building_id)}
    try:
        db.commit()
        with db.begin():
            building = HousingInventoryService(db).update_building(building_id, input, actor_user_id=context.account.id)
            _audit(db, request, context, action=action, resource_type="housing_building", resource_id=building_id, outcome="allowed", metadata=metadata)
    except HousingServiceError as exc:
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_building", resource_id=building_id, metadata=metadata)
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return BuildingResponse.model_validate(building)


@router.delete("/buildings/{building_id}", status_code=204)
def delete_building(
    building_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    action = "housing.building.delete"
    access = _access(db, context, datetime.now(UTC))
    _authorize_building_mutation(building_id, request, context, db, access, action)
    metadata = {"building_id": str(building_id)}
    try:
        db.commit()
        with db.begin():
            HousingInventoryService(db).delete_building(building_id)
            _audit(db, request, context, action=action, resource_type="housing_building", resource_id=building_id, outcome="allowed", metadata=metadata)
    except HousingServiceError as exc:
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_building", resource_id=building_id, metadata=metadata)
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return Response(status_code=204)


@router.post("/floors", response_model=FloorResponse, status_code=201)
def create_floor(
    input: FloorCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> FloorResponse:
    action = "housing.floor.create"
    access = _access(db, context, datetime.now(UTC))
    building = db.get(HousingBuilding, input.building_id)
    metadata = {"building_id": str(input.building_id)}
    if building is None or not access.can_read_building(building):
        _deny_mutation(db, request, context, action=action, resource_type="housing_floor", resource_id=None, metadata=metadata, not_found=True)
    if not access.can_manage_building(building):
        _deny_mutation(db, request, context, action=action, resource_type="housing_floor", resource_id=None, metadata=metadata)
    try:
        db.commit()
        with db.begin():
            floor = HousingInventoryService(db).create_floor(input, actor_user_id=context.account.id)
            result_metadata = {"building_id": str(input.building_id), "floor_id": str(floor.id)}
            _audit(db, request, context, action=action, resource_type="housing_floor", resource_id=floor.id, outcome="allowed", metadata=result_metadata)
    except HousingServiceError as exc:
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_floor", resource_id=None, metadata=metadata)
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return FloorResponse.model_validate(floor)


def _authorize_floor_mutation(
    floor_id: UUID,
    request: Request,
    context: AuthContext,
    db: Session,
    access: HousingAccessService,
    action: str,
) -> HousingFloor:
    floor = db.get(HousingFloor, floor_id)
    metadata = {"floor_id": str(floor_id)}
    if floor is None or not access.can_read_floor(floor):
        _deny_mutation(db, request, context, action=action, resource_type="housing_floor", resource_id=floor_id, metadata=metadata, not_found=True)
    if not access.can_manage_floor(floor):
        _deny_mutation(db, request, context, action=action, resource_type="housing_floor", resource_id=floor_id, metadata=metadata)
    return floor


@router.patch("/floors/{floor_id}", response_model=FloorResponse)
def update_floor(
    floor_id: UUID,
    input: FloorUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> FloorResponse:
    action = "housing.floor.update"
    access = _access(db, context, datetime.now(UTC))
    _authorize_floor_mutation(floor_id, request, context, db, access, action)
    metadata = {"floor_id": str(floor_id)}
    try:
        db.commit()
        with db.begin():
            floor = HousingInventoryService(db).update_floor(floor_id, input, actor_user_id=context.account.id)
            _audit(db, request, context, action=action, resource_type="housing_floor", resource_id=floor_id, outcome="allowed", metadata=metadata)
    except HousingServiceError as exc:
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_floor", resource_id=floor_id, metadata=metadata)
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return FloorResponse.model_validate(floor)


@router.delete("/floors/{floor_id}", status_code=204)
def delete_floor(
    floor_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    action = "housing.floor.delete"
    access = _access(db, context, datetime.now(UTC))
    _authorize_floor_mutation(floor_id, request, context, db, access, action)
    metadata = {"floor_id": str(floor_id)}
    try:
        db.commit()
        with db.begin():
            HousingInventoryService(db).delete_floor(floor_id)
            _audit(db, request, context, action=action, resource_type="housing_floor", resource_id=floor_id, outcome="allowed", metadata=metadata)
    except HousingServiceError as exc:
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_floor", resource_id=floor_id, metadata=metadata)
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return Response(status_code=204)


@router.post("/rooms", response_model=RoomResponse, status_code=201)
def create_room(
    input: RoomCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> RoomResponse:
    action = "housing.room.create"
    access = _access(db, context, datetime.now(UTC))
    floor = db.get(HousingFloor, input.floor_id)
    metadata = {"floor_id": str(input.floor_id)}
    if floor is None or not access.can_read_floor(floor):
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=None, metadata=metadata, not_found=True)
    if not access.can_manage_floor(floor):
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=None, metadata=metadata)
    protected = bool({"daily_cleaning_enabled", "cleaning_frequency"} & input.model_fields_set)
    if protected and not access.has_room_care_role:
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=None, metadata=metadata)

    def authorize_floor(locked_floor: HousingFloor) -> None:
        _require_current_care_actor(db, context)
        fresh = _access(db, context, datetime.now(UTC))
        _authorize_locked_floor(fresh, locked_floor)
        if protected and not fresh.has_room_care_role:
            raise _LockedAuthorizationDenied(not_found=False)

    try:
        db.commit()
        with db.begin():
            room = HousingInventoryService(db).create_room(
                input, actor_user_id=context.account.id, authorize_floor=authorize_floor
            )
            result_metadata = {"floor_id": str(input.floor_id), "room_id": str(room.id)}
            _audit(db, request, context, action=action, resource_type="housing_room", resource_id=room.id, outcome="allowed", metadata=result_metadata)
    except _LockedAuthorizationDenied as exc:
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=None, metadata=metadata, not_found=exc.not_found)
    except HousingServiceError as exc:
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_room", resource_id=None, metadata=metadata)
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return RoomResponse.model_validate(room)


def _authorize_room_mutation(
    room_id: UUID,
    request: Request,
    context: AuthContext,
    db: Session,
    access: HousingAccessService,
    action: str,
) -> HousingRoom:
    room = db.get(HousingRoom, room_id)
    metadata = {"room_id": str(room_id)}
    if room is None or not access.can_read_room(room):
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata, not_found=True)
    if not access.can_manage_room(room):
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata)
    return room


@router.patch("/rooms/{room_id}", response_model=RoomResponse)
def update_room(
    room_id: UUID,
    input: RoomUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> RoomResponse:
    action = "housing.room.update"
    access = _access(db, context, datetime.now(UTC))
    existing = _authorize_room_mutation(room_id, request, context, db, access, action)
    protected = bool({"daily_cleaning_enabled", "cleaning_frequency"} & input.model_fields_set)
    if protected and not access.can_manage_room_care(existing):
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata={"room_id": str(room_id)})
    if input.floor_id is not None:
        target = db.get(HousingFloor, input.floor_id)
        if target is None or not access.can_read_floor(target):
            _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata={"room_id": str(room_id)}, not_found=True)
        if not access.can_manage_floor(target):
            _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata={"room_id": str(room_id)})
    metadata = {"room_id": str(room_id)}

    previous_daily_cleaning_enabled: bool | None = None
    previous_cleaning_frequency: str | None = None

    def authorize_room(locked_room: HousingRoom) -> None:
        nonlocal previous_daily_cleaning_enabled, previous_cleaning_frequency
        if protected:
            _authorize_locked_room_care(db, context, datetime.now(UTC), locked_room)
            previous_daily_cleaning_enabled = locked_room.daily_cleaning_enabled
            previous_cleaning_frequency = locked_room.cleaning_frequency
        else:
            _require_current_care_actor(db, context)
            _authorize_locked_room(_access(db, context, datetime.now(UTC)), locked_room)

    def authorize_floor(locked_floor: HousingFloor) -> None:
        fresh = _access(db, context, datetime.now(UTC))
        _authorize_locked_floor(fresh, locked_floor)
        if protected and not fresh.has_room_care_role:
            raise _LockedAuthorizationDenied(not_found=False)

    try:
        db.commit()
        with db.begin():
            room = HousingInventoryService(db).update_room(
                room_id,
                input,
                actor_user_id=context.account.id,
                authorize_room=authorize_room,
                authorize_floor=authorize_floor,
            )
            result_metadata: dict[str, object] = dict(metadata)
            if (
                "cleaning_frequency" in input.model_fields_set
                and previous_cleaning_frequency != room.cleaning_frequency
            ):
                result_metadata.update(
                    previous_cleaning_frequency=previous_cleaning_frequency,
                    cleaning_frequency=room.cleaning_frequency,
                )
            if protected and previous_daily_cleaning_enabled != room.daily_cleaning_enabled:
                result_metadata.update(
                    previous_daily_cleaning_enabled=previous_daily_cleaning_enabled,
                    daily_cleaning_enabled=room.daily_cleaning_enabled,
                )
            _audit(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, outcome="allowed", metadata=result_metadata)
    except _LockedAuthorizationDenied as exc:
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata, not_found=exc.not_found)
    except HousingResourceNotFound:
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata, not_found=True)
    except HousingServiceError as exc:
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata)
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return RoomResponse.model_validate(room)


@router.delete("/rooms/{room_id}", status_code=204)
def delete_room(
    room_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    action = "housing.room.delete"
    access = _access(db, context, datetime.now(UTC))
    _authorize_room_mutation(room_id, request, context, db, access, action)
    metadata = {"room_id": str(room_id)}

    def authorize_room(locked_room: HousingRoom) -> None:
        _require_current_care_actor(db, context)
        _authorize_locked_room(_access(db, context, datetime.now(UTC)), locked_room)

    try:
        db.commit()
        with db.begin():
            HousingInventoryService(db).delete_room(
                room_id,
                authorize_room=authorize_room,
            )
            _audit(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, outcome="allowed", metadata=metadata)
    except _LockedAuthorizationDenied as exc:
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata, not_found=exc.not_found)
    except HousingResourceNotFound:
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata, not_found=True)
    except HousingServiceError as exc:
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata)
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return Response(status_code=204)


@router.post("/rooms/{room_id}/verify", response_model=RoomResponse)
def verify_room(
    room_id: UUID,
    input: RoomVerifyRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> RoomResponse:
    action = "housing.room.verify"
    access = _access(db, context, datetime.now(UTC))
    room = _authorize_room_mutation(room_id, request, context, db, access, action)
    metadata = {"room_id": str(room_id)}
    if room.verification_status != "pending_verification":
        exc = HousingInvalidRoomConfiguration("housing room is already verified")
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata)

    def authorize_room(locked_room: HousingRoom) -> None:
        _require_current_care_actor(db, context)
        _authorize_locked_room(_access(db, context, datetime.now(UTC)), locked_room)

    try:
        db.commit()
        with db.begin():
            room = HousingInventoryService(db).verify_room(
                room_id,
                expected_version=input.version,
                actor_user_id=context.account.id,
                authorize_room=authorize_room,
            )
            _audit(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, outcome="allowed", metadata=metadata)
    except _LockedAuthorizationDenied as exc:
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata, not_found=exc.not_found)
    except HousingResourceNotFound:
        _deny_mutation(db, request, context, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata, not_found=True)
    except HousingServiceError as exc:
        _failed_mutation(db, request, context, exc, action=action, resource_type="housing_room", resource_id=room_id, metadata=metadata)
    except AuditMetadataRejected as exc:
        raise ProblemError(status=500, code="housing_audit_failed", detail="房态审计记录失败") from exc
    return RoomResponse.model_validate(room)
