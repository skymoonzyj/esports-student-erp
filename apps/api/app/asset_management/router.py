from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, selectinload, undefer

from app.asset_management.export_service import render_asset_export
from app.asset_management.models import Asset, AssetAssignment, AssetPhoto
from app.asset_management.photos import AssetPhotoValidationError, validate_asset_photo
from app.asset_management.schemas import (
    AssetAssignmentResponse,
    AssetAssignRequest,
    AssetCreate,
    AssetExportRequest,
    AssetPage,
    AssetPhotoMime,
    AssetPhotoResponse,
    AssetResponse,
    AssetReturnRequest,
    AssetScanResponse,
    AssetSelfResponse,
    AssetStatus,
    AssetUpdate,
)
from app.asset_management.service import (
    AssetAlreadyAssigned,
    AssetAssignmentNotFound,
    AssetNotAssignable,
    AssetNotFound,
    AssetPersonNotFound,
    AssetService,
    AssetServiceError,
    AssetStatusConflict,
    AssetWarrantyRangeInvalid,
)
from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.rbac.service import RbacService

router = APIRouter(prefix="/assets", tags=["assets"])

Context = Annotated[AuthContext, Depends(require_standard_account)]
Db = Annotated[Session, Depends(get_db)]


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _decision(context: AuthContext, db: Session, permission: str, tokens: set[str]) -> bool:
    return (
        RbacService(db)
        .decide(
            user_id=context.account.id,
            permission=permission,
            resource_tokens=tokens,
        )
        .allowed
    )


def _deny() -> NoReturn:
    raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")


def _require_management(
    context: AuthContext,
    db: Session,
    permission: str = "assets.manage",
) -> None:
    if not _decision(context, db, permission, {"global"}):
        _deny()


def _self_tokens(context: AuthContext, db: Session, person_id: UUID) -> set[str]:
    return RbacService(db).person_resource_tokens(
        actor_person_id=context.person.id,
        target_person_id=person_id,
    )


def _require_self_or_manager(
    context: AuthContext,
    db: Session,
    person_id: UUID,
) -> None:
    if _decision(context, db, "assets.read", {"global"}):
        return
    if not _decision(context, db, "assets.read_self", _self_tokens(context, db, person_id)):
        _deny()


def _assignment_response(assignment: AssetAssignment, person_name: str) -> AssetAssignmentResponse:
    return AssetAssignmentResponse(
        id=assignment.id,
        asset_id=assignment.asset_id,
        person_id=assignment.person_id,
        person_name=person_name,
        assigned_at=assignment.assigned_at,
        expected_return_at=assignment.expected_return_at,
        returned_at=assignment.returned_at,
        condition_on_assign=assignment.condition_on_assign,
        condition_on_return=assignment.condition_on_return,
        note=assignment.note,
    )


def _asset_response(
    asset: Asset,
    assignment: AssetAssignment | None = None,
    person_name: str | None = None,
) -> AssetResponse:
    current = (
        _assignment_response(assignment, person_name or "已删除人员")
        if assignment is not None
        else None
    )
    return AssetResponse(
        id=asset.id,
        asset_code=asset.asset_code,
        name=asset.name,
        category=asset.category,
        brand=asset.brand,
        model=asset.model,
        serial_number=asset.serial_number,
        status=cast(AssetStatus, asset.status),
        location=asset.location,
        purchase_date=asset.purchase_date,
        warranty_expires_on=asset.warranty_expires_on,
        source_purchase_id=asset.source_purchase_id,
        warranty_channel=cast(Literal["online", "offline"] | None, asset.warranty_channel),
        warranty_service_location=asset.warranty_service_location,
        warranty_contact_name=asset.warranty_contact_name,
        warranty_contact_phone=asset.warranty_contact_phone,
        warranty_product_url=asset.warranty_product_url,
        warranty_order_number=asset.warranty_order_number,
        note=asset.note,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        current_assignment=current,
        photo=_photo_response(asset.id, asset.photo),
    )


def _photo_response(asset_id: UUID, photo: AssetPhoto | None) -> AssetPhotoResponse | None:
    if photo is None:
        return None
    return AssetPhotoResponse(
        id=photo.id,
        mime_type=cast(AssetPhotoMime, photo.mime_type),
        extension=cast(Literal["jpg", "png", "webp"], photo.extension),
        size_bytes=photo.byte_size,
        updated_at=photo.updated_at,
        url=f"/api/v1/assets/{asset_id}/photo",
    )


def _audit(
    db: Session,
    request: Request,
    context: AuthContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    metadata: dict[str, object] | None = None,
    purpose: str | None = None,
) -> None:
    AuditService(db).append(
        actor_user_id=context.account.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        purpose=purpose,
        outcome="allowed",
        request_id=_request_id(request),
        metadata=metadata or {},
    )


def _raise_asset_error(exc: AssetServiceError) -> NoReturn:
    if isinstance(exc, (AssetNotFound, AssetAssignmentNotFound, AssetPersonNotFound)):
        status_code = 404
    elif isinstance(exc, (AssetAlreadyAssigned, AssetStatusConflict)):
        status_code = 409
    elif isinstance(exc, (AssetNotAssignable, AssetWarrantyRangeInvalid)):
        status_code = 422
    else:
        status_code = 422
    details = {
        "asset_not_found": "资产不存在",
        "asset_assignment_not_found": "资产当前没有有效领用记录",
        "asset_person_not_found": "领用人员不存在或档案已删除",
        "asset_already_assigned": "资产当前已经分配给其他人员",
        "asset_not_assignable": "该资产当前不可分配",
        "asset_status_conflict": "资产状态与当前领用记录冲突",
        "asset_warranty_range_invalid": "保修到期日不能早于采购日期",
    }
    raise ProblemError(
        status=status_code,
        code=exc.code,
        detail=details.get(exc.code, "资产操作失败"),
    ) from exc


def _raise_photo_error(exc: AssetPhotoValidationError) -> NoReturn:
    details = {
        "empty": "照片文件为空",
        "size": "照片不能超过 10 MB",
        "format": "仅支持 JPG、PNG 或 WebP 图片",
        "pixels": "照片分辨率过大，无法上传",
        "mime": "照片类型与文件内容不一致",
        "decode": "照片无法读取，请重新选择图片",
    }
    raise ProblemError(
        status=422,
        code="asset_photo_invalid",
        detail=details.get(exc.code, "照片无法上传"),
    ) from exc


@router.get("/scan/{asset_code}", response_model=AssetScanResponse)
def scan_asset(asset_code: str, response: Response, db: Db) -> AssetScanResponse:
    """Return the deliberately minimal public payload used by a QR scan."""
    asset = db.query(Asset).filter_by(asset_code=asset_code.strip().upper()).one_or_none()
    if asset is None:
        raise ProblemError(status=404, code="asset_not_found", detail="资产不存在")
    response.headers["Cache-Control"] = "no-store"
    return AssetScanResponse(
        asset_code=asset.asset_code,
        name=asset.name,
        category=asset.category,
        brand=asset.brand,
        model=asset.model,
        status=cast(AssetStatus, asset.status),
        location=asset.location,
        purchase_date=asset.purchase_date,
        warranty_expires_on=asset.warranty_expires_on,
        scanned_at=datetime.now(UTC),
    )


@router.get("/me", response_model=list[AssetSelfResponse])
def list_my_assets(context: Context, db: Db, response: Response) -> list[AssetSelfResponse]:
    tokens = _self_tokens(context, db, context.person.id)
    if not _decision(context, db, "assets.read_self", tokens):
        _deny()
    response.headers["Cache-Control"] = "no-store, private"
    return [
        AssetSelfResponse(
            id=item.asset.id,
            asset_code=item.asset.asset_code,
            name=item.asset.name,
            category=item.asset.category,
            brand=item.asset.brand,
            model=item.asset.model,
            status=cast(AssetStatus, item.asset.status),
            location=item.asset.location,
            purchase_date=item.asset.purchase_date,
            warranty_expires_on=item.asset.warranty_expires_on,
            assigned_at=item.assignment.assigned_at,
            warranty_channel=cast(Literal["online", "offline"] | None, item.asset.warranty_channel),
            warranty_service_location=item.asset.warranty_service_location,
            warranty_contact_name=item.asset.warranty_contact_name,
            warranty_contact_phone=item.asset.warranty_contact_phone,
            warranty_product_url=item.asset.warranty_product_url,
            warranty_order_number=item.asset.warranty_order_number,
            expected_return_at=item.assignment.expected_return_at,
            note=item.assignment.note,
            photo=_photo_response(item.asset.id, item.asset.photo),
        )
        for item in AssetService(db).list_for_person(context.person.id)
    ]


@router.get("/person/{person_id}", response_model=list[AssetResponse])
def list_person_assets(
    person_id: UUID, context: Context, db: Db, response: Response
) -> list[AssetResponse]:
    _require_self_or_manager(context, db, person_id)
    response.headers["Cache-Control"] = "no-store, private"
    service = AssetService(db)
    person = db.get(Person, person_id)
    person_name = person.full_name if person is not None else "已删除人员"
    return [
        _asset_response(item.asset, item.assignment, person_name)
        for item in service.list_for_person(person_id)
    ]


@router.get("", response_model=AssetPage)
def list_assets(
    context: Context,
    db: Db,
    response: Response,
    query: str | None = Query(default=None, max_length=120),
    status_filter: Annotated[AssetStatus | None, Query(alias="status")] = None,
    category: str | None = Query(default=None, max_length=80),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AssetPage:
    _require_management(context, db, "assets.read")
    result = AssetService(db).list(
        query=query,
        status=status_filter,
        category=category,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return AssetPage(
        items=[
            _asset_response(asset, assignment, name) for asset, assignment, name in result.items
        ],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("/exports")
def export_assets(
    input: AssetExportRequest,
    request: Request,
    context: Context,
    db: Db,
) -> Response:
    _require_management(context, db, "assets.export")
    conditions = []
    if input.status is not None:
        conditions.append(Asset.status == input.status)
    if input.category:
        conditions.append(Asset.category == input.category)
    if input.query:
        pattern = f"%{input.query.strip()}%"
        conditions.append(
            Asset.asset_code.ilike(pattern)
            | Asset.name.ilike(pattern)
            | Asset.category.ilike(pattern)
            | Asset.serial_number.ilike(pattern)
        )
    rows = list(
        db.execute(
            select(Asset, AssetAssignment, Person.full_name)
            .options(selectinload(Asset.photo))
            .outerjoin(
                AssetAssignment,
                and_(
                    AssetAssignment.asset_id == Asset.id,
                    AssetAssignment.returned_at.is_(None),
                ),
            )
            .outerjoin(Person, Person.id == AssetAssignment.person_id)
            .where(*conditions)
            .order_by(Asset.updated_at.desc(), Asset.id)
        ).all()
    )
    occurred_at = datetime.now(UTC)
    try:
        export_rows = cast(list[tuple[Asset, AssetAssignment | None, str | None]], rows)
        content, media_type, filename, export_format = render_asset_export(
            export_rows,
            format=input.format,
            occurred_at=occurred_at,
        )
        _audit(
            db,
            request,
            context,
            action="asset.export",
            resource_type="asset_export",
            resource_id=None,
            purpose=input.purpose,
            metadata={
                "format": export_format,
                "item_count": len(export_rows),
                "file_sha256": sha256(content).hexdigest(),
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, ProblemError):
            raise
        raise ProblemError(
            status=422,
            code="asset_export_failed",
            detail="资产导出失败，请稍后重试",
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


@router.put("/{asset_id}/photo", response_model=AssetResponse)
async def upload_asset_photo(
    asset_id: UUID,
    request: Request,
    context: Context,
    db: Db,
    file: UploadFile = File(...),  # noqa: B008
) -> AssetResponse:
    _require_management(context, db)
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(status=404, code="asset_not_found", detail="资产不存在")
    content = await file.read(10 * 1024 * 1024 + 1)
    try:
        validated = validate_asset_photo(content, file.content_type)
    except AssetPhotoValidationError as exc:
        _raise_photo_error(exc)
    photo = db.scalar(select(AssetPhoto).where(AssetPhoto.asset_id == asset_id).with_for_update())
    if photo is None:
        photo = AssetPhoto(asset_id=asset_id, uploaded_by_user_id=context.account.id)
        db.add(photo)
    photo.content = validated.content
    photo.mime_type = validated.mime_type
    photo.extension = validated.extension
    photo.byte_size = validated.byte_size
    photo.sha256 = validated.sha256
    photo.uploaded_by_user_id = context.account.id
    asset.updated_by_user_id = context.account.id
    _audit(
        db,
        request,
        context,
        action="asset.photo.upload",
        resource_type="asset",
        resource_id=asset.id,
        metadata={"file_sha256": validated.sha256},
    )
    db.commit()
    return _asset_response(asset)


@router.delete("/{asset_id}/photo", response_model=AssetResponse)
def delete_asset_photo(
    asset_id: UUID,
    request: Request,
    context: Context,
    db: Db,
) -> AssetResponse:
    _require_management(context, db)
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(status=404, code="asset_not_found", detail="资产不存在")
    photo = db.scalar(select(AssetPhoto).where(AssetPhoto.asset_id == asset_id).with_for_update())
    if photo is not None:
        db.delete(photo)
        asset.updated_by_user_id = context.account.id
        _audit(
            db,
            request,
            context,
            action="asset.photo.remove",
            resource_type="asset",
            resource_id=asset.id,
        )
        db.commit()
    return _asset_response(asset)


@router.get("/{asset_id}/photo")
def read_asset_photo(
    asset_id: UUID,
    context: Context,
    db: Db,
) -> Response:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(status=404, code="asset_not_found", detail="资产不存在")
    current = AssetService(db).current_assignment(asset_id)
    if current is not None:
        _require_self_or_manager(context, db, current[0].person_id)
    else:
        _require_management(context, db, "assets.read")
    photo = db.scalar(
        select(AssetPhoto)
        .where(AssetPhoto.asset_id == asset_id)
        .options(undefer(AssetPhoto.content))
    )
    if photo is None:
        raise ProblemError(status=404, code="asset_photo_not_found", detail="该资产尚未上传照片")
    return Response(
        content=photo.content,
        media_type=photo.mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{asset.asset_code}.{photo.extension}"',
        },
    )


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: UUID, context: Context, db: Db, response: Response) -> AssetResponse:
    _require_management(context, db, "assets.read")
    service = AssetService(db)
    try:
        asset = service.get(asset_id)
        current = service.current_assignment(asset_id)
        response.headers["Cache-Control"] = "no-store, private"
        return _asset_response(asset, current[0], current[1]) if current else _asset_response(asset)
    except AssetServiceError as exc:
        _raise_asset_error(exc)


@router.post("", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
def create_asset(input: AssetCreate, request: Request, context: Context, db: Db) -> AssetResponse:
    _require_management(context, db)
    try:
        with atomic_write_transaction(db):
            asset = AssetService(db).create(input, actor_user_id=context.account.id)
            _audit(
                db,
                request,
                context,
                action="asset.create",
                resource_type="asset",
                resource_id=asset.id,
            )
        return _asset_response(asset)
    except AssetServiceError as exc:
        _raise_asset_error(exc)


@router.patch("/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: UUID,
    input: AssetUpdate,
    request: Request,
    context: Context,
    db: Db,
) -> AssetResponse:
    _require_management(context, db)
    try:
        with atomic_write_transaction(db):
            asset = AssetService(db).update(asset_id, input, actor_user_id=context.account.id)
            _audit(
                db,
                request,
                context,
                action="asset.update",
                resource_type="asset",
                resource_id=asset.id,
            )
        current = AssetService(db).current_assignment(asset.id)
        return _asset_response(asset, current[0], current[1]) if current else _asset_response(asset)
    except AssetServiceError as exc:
        _raise_asset_error(exc)


@router.post("/{asset_id}/assign", response_model=AssetResponse)
def assign_asset(
    asset_id: UUID,
    input: AssetAssignRequest,
    request: Request,
    context: Context,
    db: Db,
) -> AssetResponse:
    _require_management(context, db, "assets.assign")
    service = AssetService(db)
    try:
        with atomic_write_transaction(db):
            assignment = service.assign(asset_id, input, actor_user_id=context.account.id)
            _audit(
                db,
                request,
                context,
                action="asset.assign",
                resource_type="asset_assignment",
                resource_id=assignment.id,
                metadata={"person_id": str(assignment.person_id)},
            )
        asset = service.get(asset_id)
        person = db.get(Person, assignment.person_id)
        return _asset_response(asset, assignment, person.full_name if person else "已删除人员")
    except AssetServiceError as exc:
        _raise_asset_error(exc)


@router.post("/{asset_id}/return", response_model=AssetResponse)
def return_asset(
    asset_id: UUID,
    input: AssetReturnRequest,
    request: Request,
    context: Context,
    db: Db,
) -> AssetResponse:
    _require_management(context, db, "assets.assign")
    service = AssetService(db)
    try:
        with atomic_write_transaction(db):
            assignment = service.return_asset(asset_id, input, actor_user_id=context.account.id)
            _audit(
                db,
                request,
                context,
                action="asset.return",
                resource_type="asset_assignment",
                resource_id=assignment.id,
                metadata={"person_id": str(assignment.person_id)},
            )
        return _asset_response(service.get(asset_id))
    except AssetServiceError as exc:
        _raise_asset_error(exc)


@router.get("/{asset_id}/history", response_model=list[AssetAssignmentResponse])
def asset_history(asset_id: UUID, context: Context, db: Db) -> list[AssetAssignmentResponse]:
    _require_management(context, db)
    try:
        return [
            _assignment_response(assignment, person_name)
            for assignment, person_name in AssetService(db).history(asset_id)
        ]
    except AssetServiceError as exc:
        _raise_asset_error(exc)
