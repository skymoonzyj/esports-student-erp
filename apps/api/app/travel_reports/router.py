from __future__ import annotations

from datetime import date
from typing import Annotated, NoReturn
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import require_current_standard_account, require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.rbac.service import RbacService
from app.travel_reports.converter import (
    MAX_UPLOAD_BYTES,
    XLSX_MEDIA_TYPE,
    TravelReportWorkbookError,
    convert_travel_report,
)
from app.travel_reports.models import TravelReportConversion
from app.travel_reports.schemas import (
    TravelReportBillingPeriodOption,
    TravelReportFilterOptions,
    TravelReportPage,
    TravelReportResponse,
)
from app.travel_reports.service import (
    TravelReportDuplicate,
    TravelReportNotFound,
    TravelReportService,
    TravelReportServiceError,
)

router = APIRouter(prefix="/travel-reports", tags=["travel-reports"])
Context = Annotated[AuthContext, Depends(require_standard_account)]
Db = Annotated[Session, Depends(get_db)]


def _require_permission(context: AuthContext, db: Session, permission: str) -> None:
    decision = RbacService(db).decide(
        user_id=context.account.id,
        permission=permission,
        resource_tokens={"global"},
    )
    if not decision.allowed:
        raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")


def _audit(
    db: Session,
    request: Request,
    context: AuthContext,
    *,
    action: str,
    report: TravelReportConversion,
    file_sha256: str,
) -> None:
    AuditService(db).append(
        actor_user_id=context.account.id,
        action=action,
        resource_type="travel_report_conversion",
        resource_id=report.id,
        purpose=None,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata={
            "item_count": (
                report.domestic_air_count + report.international_air_count + report.train_count
            ),
            "file_sha256": file_sha256,
        },
    )


def _response(report: TravelReportConversion, imported_by_name: str) -> TravelReportResponse:
    return TravelReportResponse.model_validate(
        {
            **{
                field: getattr(report, field)
                for field in TravelReportResponse.model_fields
                if field != "imported_by_name"
            },
            "imported_by_name": imported_by_name,
        }
    )


def _raise_workbook_error(exc: TravelReportWorkbookError) -> NoReturn:
    raise ProblemError(
        status=422,
        code="travel_report_file_invalid",
        detail=str(exc),
        errors=[{"field": "file", "message": str(exc)}],
    ) from exc


def _raise_service_error(exc: TravelReportServiceError) -> NoReturn:
    if isinstance(exc, TravelReportDuplicate):
        raise ProblemError(
            status=409,
            code=exc.code,
            detail="相同账单已经导入，请在历史记录中下载转换结果",
        ) from exc
    if isinstance(exc, TravelReportNotFound):
        raise ProblemError(status=404, code=exc.code, detail="订票报表记录不存在") from exc
    raise ProblemError(status=422, code=exc.code, detail="订票报表操作失败") from exc


def _content_disposition(filename: str) -> str:
    return f"attachment; filename=travel-report.xlsx; filename*=UTF-8''{quote(filename)}"


@router.get("", response_model=TravelReportPage)
def list_travel_reports(
    context: Context,
    db: Db,
    response: Response,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 30,
    billing_period_start: date | None = None,
    billing_period_end: date | None = None,
    cost_center: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
) -> TravelReportPage:
    _require_permission(context, db, "travel_reports.read")
    normalized_cost_center = cost_center.strip() if cost_center is not None else None
    result = TravelReportService(db).list(
        page=page,
        page_size=page_size,
        billing_period_start=billing_period_start,
        billing_period_end=billing_period_end,
        cost_center=normalized_cost_center or None,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return TravelReportPage(
        items=[_response(item.report, item.imported_by_name) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        filter_options=TravelReportFilterOptions(
            billing_periods=[
                TravelReportBillingPeriodOption(
                    billing_period_start=period.billing_period_start,
                    billing_period_end=period.billing_period_end,
                )
                for period in result.billing_periods
            ],
            cost_centers=result.cost_centers,
        ),
    )


@router.post(
    "/imports",
    response_model=TravelReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_travel_report(
    request: Request,
    context: Context,
    db: Db,
    file: Annotated[UploadFile, File()],
) -> TravelReportResponse:
    _require_permission(context, db, "travel_reports.import")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        converted = convert_travel_report(content, file.filename)
        report = TravelReportService(db).create(
            converted,
            source_content=content,
            actor_user_id=context.account.id,
        )
        _audit(
            db,
            request,
            context,
            action="travel_report.import",
            report=report,
            file_sha256=report.source_sha256,
        )
        db.commit()
        return _response(report, context.person.full_name)
    except TravelReportWorkbookError as exc:
        db.rollback()
        _raise_workbook_error(exc)
    except TravelReportServiceError as exc:
        db.rollback()
        _raise_service_error(exc)


def _download(
    report_id: UUID,
    *,
    content_kind: str,
    request: Request,
    context: AuthContext,
    db: Session,
) -> Response:
    _require_permission(context, db, "travel_reports.export")
    try:
        report = TravelReportService(db).get(report_id, content_kind=content_kind)
    except TravelReportServiceError as exc:
        _raise_service_error(exc)
    if content_kind == "source":
        content = report.source_content
        filename = report.original_filename
        digest = report.source_sha256
        action = "travel_report.source.download"
    else:
        content = report.converted_content
        filename = report.converted_filename
        digest = report.converted_sha256
        action = "travel_report.converted.download"
    # Retrieval may wait for a row lock or regenerate the converted workbook.
    # Do not release bytes or record success using the pre-retrieval authority.
    context = require_current_standard_account(db, context)
    _require_permission(context, db, "travel_reports.export")
    _audit(db, request, context, action=action, report=report, file_sha256=digest)
    db.commit()
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": _content_disposition(filename),
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{report_id}/converted")
def download_converted_travel_report(
    report_id: UUID,
    request: Request,
    context: Context,
    db: Db,
) -> Response:
    return _download(
        report_id,
        content_kind="converted",
        request=request,
        context=context,
        db=db,
    )


@router.get("/{report_id}/source")
def download_source_travel_report(
    report_id: UUID,
    request: Request,
    context: Context,
    db: Db,
) -> Response:
    return _download(
        report_id,
        content_kind="source",
        request=request,
        context=context,
        db=db,
    )
