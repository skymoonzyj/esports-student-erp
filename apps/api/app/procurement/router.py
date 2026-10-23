from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.procurement.export_service import render_procurement_export
from app.procurement.invoice_files import (
    INVOICE_FILE_MAX_BYTES,
    InvoiceFileValidationError,
    validate_invoice_file,
)
from app.procurement.models import (
    DailyPurchaseRecord,
    ProcurementInvoice,
    ProcurementSettlementCenter,
)
from app.procurement.schemas import (
    InvoiceMatchCandidate,
    InvoiceMatchSuggestionResponse,
    InvoiceResponse,
    InvoiceSummary,
    ProcurementInvoiceStatus,
    ProcurementReimbursementStatus,
    ProcurementSettlementStatus,
    PurchaseCreate,
    PurchaseExportRequest,
    PurchasePage,
    PurchaseReimbursementUpdate,
    PurchaseResponse,
    PurchaseSettlementUpdate,
    PurchaseUpdate,
    ReserveFundEntryCreate,
    SettlementCenterCreate,
    SettlementCenterResponse,
    SettlementCenterUpdate,
)
from app.procurement.service import (
    InvoiceNotFound,
    InvoicePurchaseConflict,
    ProcurementService,
    ProcurementServiceError,
    PurchaseConflict,
    PurchaseFilters,
    PurchaseLocked,
    PurchaseNotFound,
    ReserveFundBalance,
    ReserveFundInsufficient,
    SettlementCenterDuplicate,
    SettlementCenterNotFound,
)
from app.rbac.service import RbacService

router = APIRouter(prefix="/procurement", tags=["procurement"])

Context = Annotated[AuthContext, Depends(require_standard_account)]
Db = Annotated[Session, Depends(get_db)]


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _require_permission(context: AuthContext, db: Session, permission: str) -> None:
    decision = RbacService(db).decide(
        user_id=context.account.id,
        permission=permission,
        resource_tokens={"global"},
    )
    if not decision.allowed:
        raise ProblemError(
            status=403,
            code="permission_denied",
            detail="没有执行此操作的权限",
        )


def _audit(
    db: Session,
    request: Request,
    context: AuthContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    purpose: str | None = None,
    metadata: dict[str, object] | None = None,
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


def _raise_service_error(exc: ProcurementServiceError) -> NoReturn:
    if isinstance(exc, (SettlementCenterNotFound, PurchaseNotFound, InvoiceNotFound)):
        status_code = 404
    elif isinstance(
        exc,
        (
            SettlementCenterDuplicate,
            PurchaseLocked,
            PurchaseConflict,
            ReserveFundInsufficient,
            InvoicePurchaseConflict,
        ),
    ):
        status_code = 409
    else:
        status_code = 422
    details = {
        "procurement_settlement_center_not_found": "结算中心不存在",
        "procurement_settlement_center_inactive": "该结算中心已停用，请选择其他结算中心",
        "procurement_settlement_center_duplicate": "已存在同名结算中心",
        "procurement_purchase_not_found": "采购记录不存在",
        "procurement_purchase_locked": "已结算或已开票记录不能修改，请先撤销结算或移除发票",
        "procurement_purchase_reimbursed": "已报销记录不能修改，请先撤销报销，再修改采购信息",
        "procurement_purchase_conflict": "采购记录状态已变化，请刷新后重试",
        "procurement_operation_conflict": "该操作标识已用于不同内容，请先核对已登记记录，再新建操作",
        "procurement_reserve_fund_insufficient": "该结算中心备用金余额不足，请先增加备用金或改用其他资金来源",
        "procurement_invoice_not_found": "发票记录不存在",
        "procurement_invoice_selection_required": "请至少选择一条采购记录",
        "procurement_invoice_amount_mismatch": "所选采购总额与发票金额不一致",
        "procurement_invoice_purchase_conflict": "所选采购中有记录已绑定其他发票，请刷新后重试",
    }
    raise ProblemError(
        status=status_code,
        code=exc.code,
        detail=details.get(exc.code, "采购操作失败"),
    ) from exc


def _raise_invoice_file_error(exc: InvoiceFileValidationError) -> NoReturn:
    details = {
        "empty": "发票文件为空",
        "size": "发票文件不能超过 20 MB",
        "format": "仅支持 PDF、JPG、PNG 或 WebP 文件",
        "pixels": "发票图片分辨率过大，无法上传",
        "mime": "发票文件类型与文件内容不一致",
        "decode": "发票文件无法读取，请重新选择",
        "filename": "发票文件名无效",
    }
    raise ProblemError(
        status=422,
        code="procurement_invoice_file_invalid",
        detail=details.get(exc.code, "发票文件无法上传"),
    ) from exc


def _invoice_summary(invoice: ProcurementInvoice) -> InvoiceSummary:
    return InvoiceSummary(
        id=invoice.id,
        amount_cents=invoice.amount_cents,
        invoice_date=invoice.invoice_date,
        invoice_number=invoice.invoice_number,
        original_filename=invoice.original_filename,
        mime_type=invoice.mime_type,
        byte_size=invoice.byte_size,
        created_at=invoice.created_at,
    )


def _invoice_response(invoice: ProcurementInvoice) -> InvoiceResponse:
    return InvoiceResponse(
        **_invoice_summary(invoice).model_dump(),
        note=invoice.note,
        purchase_ids=sorted((link.purchase_id for link in invoice.purchase_links), key=str),
        updated_at=invoice.updated_at,
    )


def _purchase_response(record: DailyPurchaseRecord) -> PurchaseResponse:
    invoice = record.invoice_link.invoice if record.invoice_link is not None else None
    return PurchaseResponse(
        id=record.id,
        purchase_date=record.purchase_date,
        item_name=record.item_name,
        purchase_channel=record.purchase_channel,
        product_url=record.product_url,
        quantity=record.quantity,
        unit_price_cents=record.unit_price_cents,
        total_price_cents=record.total_price_cents,
        settlement_center_id=record.settlement_center_id,
        settlement_center_name=record.settlement_center.name,
        uses_reserve_fund=record.uses_reserve_fund,
        is_reimbursed=record.is_reimbursed,
        is_settled=record.is_settled,
        settled_on=record.settled_on,
        has_invoice=invoice is not None,
        invoice=_invoice_summary(invoice) if invoice is not None else None,
        note=record.note,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _settlement_center_response(
    center: ProcurementSettlementCenter,
    balance: ReserveFundBalance,
) -> SettlementCenterResponse:
    return SettlementCenterResponse.model_validate(
        {
            "id": center.id,
            "name": center.name,
            "is_active": center.is_active,
            "reserve_fund_total_cents": balance.total_cents,
            "reserve_fund_used_cents": balance.used_cents,
            "reserve_fund_remaining_cents": balance.remaining_cents,
            "created_at": center.created_at,
            "updated_at": center.updated_at,
        }
    )


def _filters(
    *,
    query: str | None,
    settlement_center_id: UUID | None,
    reimbursement_status: str | None,
    settlement_status: str | None,
    invoice_status: str | None,
    start_date: date | None,
    end_date: date | None,
) -> PurchaseFilters:
    if start_date and end_date and end_date < start_date:
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="结束日期不能早于开始日期",
        )
    return PurchaseFilters(
        query=query,
        settlement_center_id=settlement_center_id,
        reimbursement_status=reimbursement_status,
        settlement_status=settlement_status,
        invoice_status=invoice_status,
        start_date=start_date,
        end_date=end_date,
    )


def _export_filters(input: PurchaseExportRequest) -> PurchaseFilters:
    if input.scope == "all":
        return PurchaseFilters(settlement_center_id=input.settlement_center_id)
    reimbursement_status = input.reimbursement_status
    settlement_status = input.settlement_status
    if input.scope in {"reimbursed", "unreimbursed"}:
        reimbursement_status = input.scope
        settlement_status = None
    elif input.scope in {"settled", "unsettled"}:
        settlement_status = input.scope
        reimbursement_status = None
    return _filters(
        query=input.query,
        settlement_center_id=input.settlement_center_id,
        reimbursement_status=reimbursement_status,
        settlement_status=settlement_status,
        invoice_status=input.invoice_status,
        start_date=input.start_date,
        end_date=input.end_date,
    )


@router.get("/settlement-centers", response_model=list[SettlementCenterResponse])
def list_settlement_centers(
    context: Context,
    db: Db,
    response: Response,
    include_inactive: bool = Query(default=False),
) -> list[SettlementCenterResponse]:
    _require_permission(context, db, "procurement.read")
    response.headers["Cache-Control"] = "no-store, private"
    service = ProcurementService(db)
    return [
        _settlement_center_response(center, service.reserve_fund_balance(center.id))
        for center in service.list_centers(include_inactive=include_inactive)
    ]


@router.post(
    "/settlement-centers",
    response_model=SettlementCenterResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_settlement_center(
    input: SettlementCenterCreate,
    request: Request,
    context: Context,
    db: Db,
) -> SettlementCenterResponse:
    _require_permission(context, db, "procurement.centers.manage")
    try:
        with atomic_write_transaction(db):
            center = ProcurementService(db).create_center(
                input,
                actor_user_id=context.account.id,
            )
            _audit(
                db,
                request,
                context,
                action="procurement.center.create",
                resource_type="procurement_settlement_center",
                resource_id=center.id,
            )
            return _settlement_center_response(
                center,
                ProcurementService(db).reserve_fund_balance(center.id),
            )
    except ProcurementServiceError as exc:
        _raise_service_error(exc)


@router.patch(
    "/settlement-centers/{center_id}",
    response_model=SettlementCenterResponse,
)
def update_settlement_center(
    center_id: UUID,
    input: SettlementCenterUpdate,
    request: Request,
    context: Context,
    db: Db,
) -> SettlementCenterResponse:
    _require_permission(context, db, "procurement.centers.manage")
    try:
        with atomic_write_transaction(db):
            center = ProcurementService(db).update_center(
                center_id,
                input,
                actor_user_id=context.account.id,
            )
            _audit(
                db,
                request,
                context,
                action="procurement.center.update",
                resource_type="procurement_settlement_center",
                resource_id=center.id,
            )
            return _settlement_center_response(
                center,
                ProcurementService(db).reserve_fund_balance(center.id),
            )
    except ProcurementServiceError as exc:
        _raise_service_error(exc)


@router.post(
    "/settlement-centers/{center_id}/reserve-fund-entries",
    response_model=SettlementCenterResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_settlement_center_reserve_fund(
    center_id: UUID,
    input: ReserveFundEntryCreate,
    request: Request,
    context: Context,
    db: Db,
) -> SettlementCenterResponse:
    _require_permission(context, db, "procurement.manage")
    if input.operation_key is None:
        raise ProblemError(
            status=422,
            code="procurement_operation_key_required",
            detail="操作标识缺失，请刷新页面；如曾提交，请先核对是否已登记",
        )
    try:
        with atomic_write_transaction(db):
            service = ProcurementService(db)
            _entry, center, balance = service.add_reserve_fund(
                center_id,
                input,
                actor_user_id=context.account.id,
            )
            if not service.operation_replayed:
                _audit(
                    db,
                    request,
                    context,
                    action="procurement.center.reserve_fund.add",
                    resource_type="procurement_settlement_center",
                    resource_id=center.id,
                )
            return _settlement_center_response(center, balance)
    except ProcurementServiceError as exc:
        _raise_service_error(exc)


@router.get("", response_model=PurchasePage)
def list_purchases(
    context: Context,
    db: Db,
    response: Response,
    query: Annotated[str | None, Query(max_length=120)] = None,
    settlement_center_id: Annotated[UUID | None, Query()] = None,
    reimbursement_status: Annotated[ProcurementReimbursementStatus | None, Query()] = None,
    settlement_status: Annotated[ProcurementSettlementStatus | None, Query()] = None,
    invoice_status: Annotated[ProcurementInvoiceStatus | None, Query()] = None,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PurchasePage:
    _require_permission(context, db, "procurement.read")
    filters = _filters(
        query=query,
        settlement_center_id=settlement_center_id,
        reimbursement_status=reimbursement_status,
        settlement_status=settlement_status,
        invoice_status=invoice_status,
        start_date=start_date,
        end_date=end_date,
    )
    result = ProcurementService(db).list_purchases(filters, page=page, page_size=page_size)
    response.headers["Cache-Control"] = "no-store, private"
    return PurchasePage(
        items=[_purchase_response(record) for record in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        total_amount_cents=result.total_amount_cents,
        reimbursed_amount_cents=result.reimbursed_amount_cents,
        unreimbursed_amount_cents=result.unreimbursed_amount_cents,
        settled_amount_cents=result.settled_amount_cents,
        unsettled_amount_cents=result.unsettled_amount_cents,
    )


@router.get("/invoices/match-suggestions", response_model=InvoiceMatchSuggestionResponse)
def suggest_invoice_matches(
    context: Context,
    db: Db,
    response: Response,
    amount_cents: Annotated[int, Query(gt=0, le=9_000_000_000_000)],
    settlement_center_id: Annotated[UUID | None, Query()] = None,
) -> InvoiceMatchSuggestionResponse:
    _require_permission(context, db, "procurement.invoices.manage")
    result = ProcurementService(db).suggest_invoice_matches(
        amount_cents,
        settlement_center_id=settlement_center_id,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return InvoiceMatchSuggestionResponse(
        amount_cents=amount_cents,
        matched_total_cents=result.matched_total_cents,
        suggested_purchase_ids=list(result.suggested_purchase_ids),
        candidates=[
            InvoiceMatchCandidate(
                id=record.id,
                purchase_date=record.purchase_date,
                item_name=record.item_name,
                total_price_cents=record.total_price_cents,
                settlement_center_id=record.settlement_center_id,
                settlement_center_name=record.settlement_center.name,
            )
            for record in result.candidates
        ],
    )


@router.post(
    "/invoices",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_invoice(
    request: Request,
    context: Context,
    db: Db,
    file: Annotated[UploadFile, File()],
    amount_cents: Annotated[int, Form(gt=0, le=9_000_000_000_000)],
    invoice_date: Annotated[date, Form()],
    purchase_ids: Annotated[list[UUID], Form(min_length=1, max_length=100)],
    invoice_number: Annotated[str | None, Form(max_length=120)] = None,
    note: Annotated[str | None, Form(max_length=2000)] = None,
) -> InvoiceResponse:
    _require_permission(context, db, "procurement.invoices.manage")
    content = await file.read(INVOICE_FILE_MAX_BYTES + 1)
    try:
        validated_file = validate_invoice_file(content, file.content_type, file.filename)
    except InvoiceFileValidationError as exc:
        _raise_invoice_file_error(exc)
    try:
        with atomic_write_transaction(db):
            invoice = ProcurementService(db).create_invoice(
                amount_cents=amount_cents,
                invoice_date=invoice_date,
                invoice_number=(invoice_number or "").strip() or None,
                note=(note or "").strip() or None,
                purchase_ids=purchase_ids,
                file=validated_file,
                actor_user_id=context.account.id,
            )
            _audit(
                db,
                request,
                context,
                action="procurement.invoice.upload",
                resource_type="procurement_invoice",
                resource_id=invoice.id,
                metadata={
                    "item_count": len(invoice.purchase_links),
                    "file_sha256": invoice.sha256,
                },
            )
            return _invoice_response(invoice)
    except ProcurementServiceError as exc:
        _raise_service_error(exc)


@router.get("/invoices/{invoice_id}/file")
def download_invoice_file(
    invoice_id: UUID,
    context: Context,
    db: Db,
) -> Response:
    _require_permission(context, db, "procurement.read")
    try:
        invoice = ProcurementService(db).get_invoice(invoice_id, include_content=True)
    except ProcurementServiceError as exc:
        _raise_service_error(exc)
    return Response(
        content=invoice.content,
        media_type=invoice.mime_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="procurement-invoice-{invoice.id}.{invoice.extension}"'
            ),
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(
    invoice_id: UUID,
    request: Request,
    context: Context,
    db: Db,
) -> Response:
    _require_permission(context, db, "procurement.invoices.manage")
    try:
        with atomic_write_transaction(db):
            purchase_ids = ProcurementService(db).delete_invoice(invoice_id)
            _audit(
                db,
                request,
                context,
                action="procurement.invoice.remove",
                resource_type="procurement_invoice",
                resource_id=invoice_id,
                metadata={"item_count": len(purchase_ids)},
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ProcurementServiceError as exc:
        _raise_service_error(exc)


@router.post("", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED)
def create_purchase(
    input: PurchaseCreate,
    request: Request,
    context: Context,
    db: Db,
) -> PurchaseResponse:
    _require_permission(context, db, "procurement.manage")
    if input.add_to_assets:
        _require_permission(context, db, "assets.manage")
    if input.is_settled:
        _require_permission(context, db, "procurement.settle")
    if input.operation_key is None:
        raise ProblemError(
            status=422,
            code="procurement_operation_key_required",
            detail="操作标识缺失，请刷新页面；如曾提交，请先核对是否已登记",
        )
    try:
        with atomic_write_transaction(db):
            service = ProcurementService(db)
            record = service.create_purchase(
                input,
                actor_user_id=context.account.id,
            )
            if not service.operation_replayed:
                _audit(
                    db,
                    request,
                    context,
                    action="procurement.purchase.create",
                    resource_type="daily_purchase_record",
                    resource_id=record.id,
                    metadata={
                        "uses_reserve_fund": record.uses_reserve_fund,
                        "item_count": int(input.quantity) if input.add_to_assets else 0,
                    },
                )
            return _purchase_response(record)
    except ProcurementServiceError as exc:
        _raise_service_error(exc)


@router.patch("/{purchase_id}", response_model=PurchaseResponse)
def update_purchase(
    purchase_id: UUID,
    input: PurchaseUpdate,
    request: Request,
    context: Context,
    db: Db,
) -> PurchaseResponse:
    _require_permission(context, db, "procurement.manage")
    try:
        with atomic_write_transaction(db):
            record = ProcurementService(db).update_purchase(
                purchase_id,
                input,
                actor_user_id=context.account.id,
            )
            _audit(
                db,
                request,
                context,
                action="procurement.purchase.update",
                resource_type="daily_purchase_record",
                resource_id=record.id,
                metadata={"uses_reserve_fund": record.uses_reserve_fund},
            )
            return _purchase_response(record)
    except ProcurementServiceError as exc:
        _raise_service_error(exc)


@router.patch("/{purchase_id}/settlement", response_model=PurchaseResponse)
def update_purchase_settlement(
    purchase_id: UUID,
    input: PurchaseSettlementUpdate,
    request: Request,
    context: Context,
    db: Db,
) -> PurchaseResponse:
    _require_permission(context, db, "procurement.settle")
    try:
        with atomic_write_transaction(db):
            record = ProcurementService(db).update_settlement(
                purchase_id,
                input,
                actor_user_id=context.account.id,
            )
            _audit(
                db,
                request,
                context,
                action="procurement.purchase.settlement",
                resource_type="daily_purchase_record",
                resource_id=record.id,
            )
            return _purchase_response(record)
    except ProcurementServiceError as exc:
        _raise_service_error(exc)


@router.patch("/{purchase_id}/reimbursement", response_model=PurchaseResponse)
def update_purchase_reimbursement(
    purchase_id: UUID,
    input: PurchaseReimbursementUpdate,
    request: Request,
    context: Context,
    db: Db,
) -> PurchaseResponse:
    _require_permission(context, db, "procurement.manage")
    try:
        with atomic_write_transaction(db):
            record = ProcurementService(db).update_reimbursement(
                purchase_id,
                input,
                actor_user_id=context.account.id,
            )
            _audit(
                db,
                request,
                context,
                action="procurement.purchase.reimbursement",
                resource_type="daily_purchase_record",
                resource_id=record.id,
                metadata={"is_reimbursed": input.is_reimbursed},
            )
            return _purchase_response(record)
    except ProcurementServiceError as exc:
        _raise_service_error(exc)


@router.post("/exports")
def export_purchases(
    input: PurchaseExportRequest,
    request: Request,
    context: Context,
    db: Db,
) -> Response:
    _require_permission(context, db, "procurement.export")
    filters = _export_filters(input)
    records = ProcurementService(db).export_purchases(filters)
    occurred_at = datetime.now(UTC)
    try:
        content, media_type, filename, export_format = render_procurement_export(
            records,
            format=input.format,
            occurred_at=occurred_at,
            scope=input.scope,
            applicant_name=context.person.full_name,
        )
        _audit(
            db,
            request,
            context,
            action="procurement.purchase.export",
            resource_type="daily_purchase_export",
            resource_id=None,
            purpose=input.purpose,
            metadata={
                "format": export_format,
                "export_scope": input.scope,
                "item_count": len(records),
                "file_sha256": sha256(content).hexdigest(),
                **(
                    {"settlement_center_id": str(input.settlement_center_id)}
                    if input.settlement_center_id is not None
                    else {}
                ),
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, ProblemError):
            raise
        raise ProblemError(
            status=422,
            code="procurement_export_failed",
            detail="采购台账导出失败，请稍后重试",
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
