from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.housing.access import REPORT_PERMISSION, HousingAccessService
from app.housing.api_support import (
    PrivateHousingRoute,
    _deny_mutation,
    _mark_request_audited,
    _standalone_audit,
)
from app.housing.dependencies import (
    get_housing_cipher,
    get_housing_lookup,
    get_housing_reporting_db,
)
from app.housing.errors import (
    HousingReportingError,
    HousingReportScopeDenied,
    HousingResourceNotFound,
    HousingServiceError,
)
from app.housing.models import HousingFloor
from app.housing.reporting_schemas import (
    GenerateHousingReportRequest,
    HousingReportBatchDetailResponse,
    HousingReportBatchPageResponse,
    HousingReportBatchSummaryResponse,
    MarkHousingReportSubmittedRequest,
    ReportBatchStatus,
    VoidHousingReportRequest,
)
from app.housing.reporting_service import (
    GenerateReportCommand,
    HousingReportingService,
    MarkSubmittedCommand,
    ReportBatchQuery,
    ReportFormat,
    VoidReportCommand,
)
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.security.encryption import EnvelopeCipher, LookupDigest

router = APIRouter(
    prefix="/housing/report-batches",
    tags=["housing-reporting"],
    route_class=PrivateHousingRoute,
)


@dataclass(frozen=True, slots=True)
class ReportingPrincipal:
    auth: AuthContext
    actor_user_id: UUID


@dataclass(frozen=True, slots=True)
class ReportingRequestContext:
    db: Session
    auth: AuthContext
    actor_user_id: UUID
    occurred_at: datetime
    action: str


def _reporting_principal(
    auth: Annotated[AuthContext, Depends(require_standard_account)],
) -> ReportingPrincipal:
    return ReportingPrincipal(auth=auth, actor_user_id=auth.account.id)


def _authorized_reporting_context(
    request: Request,
    db: Session,
    principal: ReportingPrincipal,
    *,
    action: str,
) -> ReportingRequestContext:
    occurred_at = datetime.now(UTC)
    request.state.housing_mutation_db = db
    request.state.housing_mutation_context = principal.auth
    request.state.housing_mutation_action = action
    access = HousingAccessService(db, principal.actor_user_id, occurred_at)
    has_global_capability = access.can(REPORT_PERMISSION, {"global"})
    authorized_floor_id = db.scalar(
        select(HousingFloor.id)
        .where(access.report_floor_criteria())
        .limit(1)
    )
    if not has_global_capability and authorized_floor_id is None:
        _deny_mutation(
            db,
            request,
            principal.auth,
            action=action,
            resource_type="housing_report_batch",
            resource_id=None,
            metadata={},
        )
    db.rollback()
    return ReportingRequestContext(
        db=db,
        auth=principal.auth,
        actor_user_id=principal.actor_user_id,
        occurred_at=occurred_at,
        action=action,
    )


def _reporting_context(action: str) -> Callable[..., ReportingRequestContext]:
    def dependency(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        principal: Annotated[ReportingPrincipal, Depends(_reporting_principal)],
    ) -> ReportingRequestContext:
        return _authorized_reporting_context(request, db, principal, action=action)

    return dependency


async def _generation_reporting_context(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[ReportingPrincipal, Depends(_reporting_principal)],
) -> ReportingRequestContext:
    action = "housing.report_batch.generate"
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("corrects_batch_id") is not None:
        action = "housing.report_batch.correct"
    return _authorized_reporting_context(request, db, principal, action=action)


def get_housing_reporting_service(
    request: Request,
    db: Annotated[Session, Depends(get_housing_reporting_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
    principal: Annotated[ReportingPrincipal, Depends(_reporting_principal)],
) -> HousingReportingService:
    return HousingReportingService(
        db,
        cipher,
        principal.actor_user_id,
        datetime.now(UTC),
        str(request.state.request_id),
        lookup=lookup,
    )


def _invoke_reporting_service[ResultT](
    request: Request,
    context: ReportingRequestContext,
    operation: Callable[[], ResultT],
    *,
    resource_id: UUID | None = None,
) -> ResultT:
    audit_action = getattr(
        request.state,
        "housing_mutation_action",
        context.action,
    )
    try:
        result = operation()
    except ApprovalEvidenceError as exc:
        _standalone_audit(
            context.db,
            request,
            context.auth,
            action=audit_action,
            resource_type="housing_report_batch",
            resource_id=resource_id,
            outcome="failed",
            metadata={"error_code": exc.code},
        )
        raise ProblemError(status=503, code=exc.code, detail="审核资料校验暂不可用") from exc
    except HousingReportScopeDenied:
        _standalone_audit(
            context.db,
            request,
            context.auth,
            action=audit_action,
            resource_type="housing_report_batch",
            resource_id=resource_id,
            outcome="denied",
            metadata={"error_code": "housing_report_scope_denied"},
        )
        raise
    except (HousingReportingError, HousingResourceNotFound):
        _mark_request_audited(request)
        raise
    except HousingServiceError:
        raise
    except ValueError as exc:
        _standalone_audit(
            context.db,
            request,
            context.auth,
            action=audit_action,
            resource_type="housing_report_batch",
            resource_id=None,
            outcome="failed",
            metadata={"error_code": "validation_error"},
        )
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="Request validation failed",
        ) from exc
    _mark_request_audited(request)
    return result


@router.post("", response_model=HousingReportBatchSummaryResponse)
def generate_report(
    payload: GenerateHousingReportRequest,
    request: Request,
    context: Annotated[
        ReportingRequestContext,
        Depends(_generation_reporting_context),
    ],
    service: Annotated[
        HousingReportingService,
        Depends(get_housing_reporting_service),
    ],
) -> HousingReportBatchSummaryResponse:
    result = _invoke_reporting_service(
        request,
        context,
        lambda: service.generate(
            GenerateReportCommand(
                range_start=payload.range_start,
                range_end=payload.range_end,
                scope_mode=payload.scope_mode,
                building_ids=tuple(payload.building_ids),
                floor_ids=tuple(payload.floor_ids),
                cohort_ids=tuple(payload.cohort_ids),
                person_types=tuple(payload.person_types),
                purpose=payload.purpose,
                corrects_batch_id=payload.corrects_batch_id,
            )
        ),
        resource_id=payload.corrects_batch_id,
    )
    return HousingReportBatchSummaryResponse.model_validate(result)


@router.get("", response_model=HousingReportBatchPageResponse)
def list_reports(
    request: Request,
    context: Annotated[
        ReportingRequestContext,
        Depends(_reporting_context("housing.report_batch.list")),
    ],
    service: Annotated[
        HousingReportingService,
        Depends(get_housing_reporting_service),
    ],
    status: Annotated[list[ReportBatchStatus] | None, Query()] = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    building_id: Annotated[list[UUID] | None, Query()] = None,
    floor_id: Annotated[list[UUID] | None, Query()] = None,
    cohort_id: Annotated[list[UUID] | None, Query()] = None,
    created_by_user_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> HousingReportBatchPageResponse:
    result = _invoke_reporting_service(
        request,
        context,
        lambda: service.list(
            ReportBatchQuery(
                statuses=tuple(status or ()),
                range_start=range_start,
                range_end=range_end,
                building_ids=tuple(building_id or ()),
                floor_ids=tuple(floor_id or ()),
                cohort_ids=tuple(cohort_id or ()),
                created_by_user_id=created_by_user_id,
                page=page,
                page_size=page_size,
            )
        ),
    )
    return HousingReportBatchPageResponse.model_validate(result)


@router.get("/{batch_id}", response_model=HousingReportBatchDetailResponse)
def get_report(
    batch_id: UUID,
    request: Request,
    context: Annotated[
        ReportingRequestContext,
        Depends(_reporting_context("housing.report_batch.read")),
    ],
    service: Annotated[
        HousingReportingService,
        Depends(get_housing_reporting_service),
    ],
) -> HousingReportBatchDetailResponse:
    result = _invoke_reporting_service(
        request,
        context,
        lambda: service.get(batch_id),
    )
    return HousingReportBatchDetailResponse.model_validate(result)


@router.get("/{batch_id}/download")
def download_report(
    batch_id: UUID,
    format: ReportFormat,
    request: Request,
    context: Annotated[
        ReportingRequestContext,
        Depends(_reporting_context("housing.report_batch.download")),
    ],
    service: Annotated[
        HousingReportingService,
        Depends(get_housing_reporting_service),
    ],
) -> Response:
    request.state.housing_mutation_action = f"housing.report_batch.download.{format}"
    result = _invoke_reporting_service(
        request,
        context,
        lambda: service.download(batch_id, format),
    )
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{batch_id}/mark-submitted",
    response_model=HousingReportBatchSummaryResponse,
)
def mark_report_submitted(
    batch_id: UUID,
    payload: MarkHousingReportSubmittedRequest,
    request: Request,
    context: Annotated[
        ReportingRequestContext,
        Depends(_reporting_context("housing.report_batch.mark_submitted")),
    ],
    service: Annotated[
        HousingReportingService,
        Depends(get_housing_reporting_service),
    ],
) -> HousingReportBatchSummaryResponse:
    result = _invoke_reporting_service(
        request,
        context,
        lambda: service.mark_submitted(
            batch_id,
            MarkSubmittedCommand(
                submitted_at=payload.submitted_at,
                note=payload.note,
                expected_version=payload.version,
            ),
        ),
    )
    return HousingReportBatchSummaryResponse.model_validate(result)


@router.post("/{batch_id}/void", response_model=HousingReportBatchSummaryResponse)
def void_report(
    batch_id: UUID,
    payload: VoidHousingReportRequest,
    request: Request,
    context: Annotated[
        ReportingRequestContext,
        Depends(_reporting_context("housing.report_batch.void")),
    ],
    service: Annotated[
        HousingReportingService,
        Depends(get_housing_reporting_service),
    ],
) -> HousingReportBatchSummaryResponse:
    result = _invoke_reporting_service(
        request,
        context,
        lambda: service.void(
            batch_id,
            VoidReportCommand(
                reason=payload.reason,
                expected_version=payload.version,
            ),
        ),
    )
    return HousingReportBatchSummaryResponse.model_validate(result)
