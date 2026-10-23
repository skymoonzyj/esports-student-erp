from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BeforeValidator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.dependencies import (
    get_evaluation_context_service,
    get_teaching_report_query_service,
    get_teaching_report_service,
    get_teaching_report_workspace_query_service,
)
from app.evaluations.errors import (
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
)
from app.evaluations.mutation_api_support import (
    idempotency_key,
    idempotent_json_request_openapi,
    mutation_call,
    new_audit_request_id,
    parse_input,
    private_not_found,
)
from app.evaluations.portal_router import require_evaluation_center
from app.evaluations.report_api_schemas import (
    TeachingReportAction,
    TeachingReportContextResponse,
    TeachingReportDraftResponse,
    TeachingReportDraftStartResponse,
    TeachingReportPageResponse,
    TeachingReportProblemResponse,
    TeachingReportPublishedItemResponse,
    TeachingReportPublishedPageResponse,
    TeachingReportReopenResponse,
    TeachingReportReviewQueuePageResponse,
    TeachingReportReviewResponse,
    TeachingReportSlotListResponse,
    TeachingReportSlotResponse,
    TeachingReportStatus,
    TeachingReportSubmitResponse,
    TeachingReportTargetPageResponse,
    TeachingReportVersionPageResponse,
    TeachingReportVersionSource,
)
from app.evaluations.report_models import (
    StudentReport,
    StudentReportDraft,
    StudentReportWindowGrant,
)
from app.evaluations.report_query_service import (
    TeachingReportAuthorWorkspaceItem,
    TeachingReportQueryService,
)
from app.evaluations.report_schemas import (
    TeachingReportDraftSave,
    TeachingReportDraftStartInput,
    TeachingReportReopenInput,
    TeachingReportReviewInput,
    TeachingReportSubmitInput,
)
from app.evaluations.report_service import TeachingReportService
from app.evaluations.report_workspace_query_service import (
    TeachingReportTargetFilters,
    TeachingReportWorkspaceQueryService,
)

router = APIRouter(prefix="/evaluations", tags=["teaching reports"])


def _mutation_responses(
    replay_model: type[BaseModel] | None = None,
) -> dict[int | str, dict[str, Any]]:
    responses: dict[int | str, dict[str, Any]] = {
        status: {"model": TeachingReportProblemResponse}
        for status in (403, 404, 409, 422)
    }
    if replay_model is not None:
        responses[200] = {"model": replay_model}
    return responses


def _read_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    return {
        status: {"model": TeachingReportProblemResponse}
        for status in statuses
    }


def _now() -> datetime:
    return datetime.now(UTC)


def _trim_optional_query(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    return trimmed or None


def _author_operation_denied() -> ProblemError:
    return ProblemError(
        status=403,
        code="evaluation_operation_denied",
        detail="没有填写教学评语的权限",
    )


def _require_author_capability(
    service: TeachingReportQueryService,
    *,
    account_id: UUID,
    occurred_at: datetime,
) -> None:
    if not service.author_capable(
        account_id=account_id,
        occurred_at=occurred_at,
    ):
        raise _author_operation_denied()


def _require_family_capability(
    service: TeachingReportQueryService,
    *,
    account_id: UUID,
    occurred_at: datetime,
) -> None:
    if not service.family_capable(
        account_id=account_id,
        occurred_at=occurred_at,
    ):
        raise ProblemError(
            status=403,
            code="evaluation_operation_denied",
            detail="没有查看家庭教学评语的权限",
        )


def _preauthorize_author_report(
    service: TeachingReportQueryService,
    report_id: UUID,
    *,
    account_id: UUID,
    occurred_at: datetime,
) -> JSONResponse | None:
    _require_author_capability(
        service,
        account_id=account_id,
        occurred_at=occurred_at,
    )
    try:
        service.require_author_report(
            report_id,
            account_id=account_id,
            occurred_at=occurred_at,
        )
    except EvaluationOperationDenied as exc:
        raise _author_operation_denied() from exc
    except EvaluationResourceNotFound:
        return private_not_found()
    return None


def _allowed_actions(
    db: Session,
    item: TeachingReportAuthorWorkspaceItem,
    *,
    occurred_at: datetime,
) -> list[TeachingReportAction]:
    slot = item.slot
    if slot.report_id is None:
        return ["save_draft"] if slot.draft_allowed else []
    report = db.get(StudentReport, slot.report_id)
    if report is None:
        return []
    draft = db.get(StudentReportDraft, report.id)
    if draft is not None:
        if report.status == "correction_draft":
            editable = bool(
                report.correction_due_at is not None
                and occurred_at <= report.correction_due_at
            )
        elif report.status == "published" and draft.draft_kind == "revision":
            grant = (
                db.get(StudentReportWindowGrant, report.current_window_grant_id)
                if report.current_window_grant_id is not None
                else None
            )
            editable = occurred_at <= report.initial_submission_deadline or bool(
                grant is not None
                and grant.grant_kind == "revision"
                and grant.opens_at <= occurred_at <= grant.expires_at
            )
        else:
            editable = slot.draft_allowed
        if not editable:
            return []
        actions: list[TeachingReportAction] = ["save_draft"]
        if (
            report.status == "correction_draft"
            or slot.submit_allowed
            or (report.status == "published" and draft.draft_kind == "revision")
        ):
            actions.append("submit")
        return actions
    if (
        report.status == "rejected"
        and report.correction_due_at is not None
        and occurred_at <= report.correction_due_at
    ):
        return ["start_correction"]
    if report.status == "published":
        grant = (
            db.get(StudentReportWindowGrant, report.current_window_grant_id)
            if report.current_window_grant_id is not None
            else None
        )
        if occurred_at <= report.initial_submission_deadline or (
            grant is not None
            and grant.grant_kind == "revision"
            and grant.opens_at <= occurred_at <= grant.expires_at
        ):
            return ["start_revision"]
    return []


def _slot_response(
    db: Session,
    item: TeachingReportAuthorWorkspaceItem,
    *,
    occurred_at: datetime,
) -> TeachingReportSlotResponse:
    slot = item.slot
    report = db.get(StudentReport, slot.report_id) if slot.report_id is not None else None
    return TeachingReportSlotResponse(
        enrollment_id=slot.target.enrollment_id,
        student_person_id=slot.target.student_person_id,
        cohort_id=slot.target.cohort_id,
        cohort_name=slot.target.cohort_name,
        project_id=slot.target.project_id,
        project_name=slot.target.project_name,
        class_id=slot.target.class_id,
        class_name=slot.target.class_name,
        report_kind=slot.period.kind,
        period_sequence=slot.period.sequence,
        period_start_date=slot.period.starts_on,
        period_end_date=slot.period.ends_on,
        submission_opens_at=slot.submission_opens_at,
        initial_submission_deadline=slot.initial_submission_deadline,
        report_id=slot.report_id,
        status=cast(TeachingReportStatus | None, slot.report_status),
        lock_version=report.lock_version if report is not None else None,
        draft_body=item.draft_body,
        draft_dimension_values=item.draft_dimension_values,
        draft_revision=item.draft_revision,
        allowed_actions=_allowed_actions(db, item, occurred_at=occurred_at),
    )


@router.get(
    "/reports/context",
    response_model=TeachingReportContextResponse,
)
def get_report_context(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
) -> TeachingReportContextResponse:
    require_evaluation_center(context=context, service=context_service)
    capabilities = service.capabilities(
        account_id=context.account.id,
        occurred_at=_now(),
    )
    return TeachingReportContextResponse.model_validate(
        capabilities,
        from_attributes=True,
    )


@router.get(
    "/reports/targets",
    response_model=TeachingReportTargetPageResponse,
    responses=_read_responses(403, 422),
)
def get_report_targets(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportWorkspaceQueryService,
        Depends(get_teaching_report_workspace_query_service),
    ],
    cohort_id: Annotated[UUID | None, Query()] = None,
    project_id: Annotated[UUID | None, Query()] = None,
    class_id: Annotated[UUID | None, Query()] = None,
    student_id: Annotated[UUID | None, Query()] = None,
    query: Annotated[
        str | None,
        Query(max_length=80),
        BeforeValidator(_trim_optional_query),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TeachingReportTargetPageResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    try:
        result = service.author_targets(
            account_id=context.account.id,
            occurred_at=occurred_at,
            filters=TeachingReportTargetFilters(
                cohort_id=cohort_id,
                project_id=project_id,
                class_id=class_id,
                student_id=student_id,
                query=query,
                page=page,
                page_size=page_size,
            ),
        )
    except EvaluationOperationDenied as exc:
        raise _author_operation_denied() from exc
    return TeachingReportTargetPageResponse.model_validate(
        result,
        from_attributes=True,
    )


@router.get(
    "/reports/slots",
    response_model=TeachingReportSlotListResponse,
)
def get_report_slots(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
    db: Annotated[Session, Depends(get_db)],
    enrollment_id: Annotated[UUID, Query()],
) -> TeachingReportSlotListResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    _require_author_capability(
        service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    try:
        items = service.author_workspace(
            account_id=context.account.id,
            enrollment_id=enrollment_id,
            occurred_at=occurred_at,
        )
    except EvaluationResourceNotFound:
        return private_not_found()
    return TeachingReportSlotListResponse(
        items=[_slot_response(db, item, occurred_at=occurred_at) for item in items]
    )


@router.get("/reports", response_model=TeachingReportPageResponse)
def list_reports(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
    db: Annotated[Session, Depends(get_db)],
    enrollment_id: Annotated[UUID, Query()],
    kind: Annotated[Literal["daily", "weekly"] | None, Query()] = None,
    status: Annotated[TeachingReportStatus | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TeachingReportPageResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    _require_author_capability(
        service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    try:
        workspace = service.author_workspace(
            account_id=context.account.id,
            enrollment_id=enrollment_id,
            occurred_at=occurred_at,
        )
    except EvaluationResourceNotFound:
        return private_not_found()
    items = [
        item
        for item in workspace
        if item.slot.report_id is not None
        and (kind is None or item.slot.period.kind == kind)
        and (status is None or item.slot.report_status == status)
    ]
    offset = (page - 1) * page_size
    return TeachingReportPageResponse(
        items=[
            _slot_response(db, item, occurred_at=occurred_at)
            for item in items[offset : offset + page_size]
        ],
        total=len(items),
        page=page,
        page_size=page_size,
    )


@router.get(
    "/reports/review-queue",
    response_model=TeachingReportReviewQueuePageResponse,
)
def get_report_review_queue(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
    cohort_id: Annotated[UUID | None, Query()] = None,
    project_id: Annotated[UUID | None, Query()] = None,
    class_id: Annotated[UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TeachingReportReviewQueuePageResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    if not service.review_capable(
        account_id=context.account.id,
        occurred_at=occurred_at,
    ):
        raise ProblemError(
            status=403,
            code="evaluation_operation_denied",
            detail="没有审核教学评语的权限",
        )
    result = service.review_queue(
        account_id=context.account.id,
        occurred_at=occurred_at,
        cohort_id=cohort_id,
        project_id=project_id,
        class_id=class_id,
        page=page,
        page_size=page_size,
    )
    return TeachingReportReviewQueuePageResponse.model_validate(
        result,
        from_attributes=True,
    )


@router.get(
    "/reports/{report_id}/versions",
    response_model=TeachingReportVersionPageResponse,
    responses=_read_responses(403, 404, 422),
)
def get_report_versions(
    report_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportWorkspaceQueryService,
        Depends(get_teaching_report_workspace_query_service),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TeachingReportVersionPageResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    try:
        result = service.report_versions(
            report_id,
            account_id=context.account.id,
            occurred_at=occurred_at,
            page=page,
            page_size=page_size,
        )
    except EvaluationOperationDenied as exc:
        raise ProblemError(
            status=403,
            code="evaluation_operation_denied",
            detail="没有查看教学评语版本的权限",
        ) from exc
    except EvaluationResourceNotFound:
        return private_not_found()
    return TeachingReportVersionPageResponse.model_validate(
        result,
        from_attributes=True,
    )


@router.get(
    "/reports/{report_id}",
    response_model=TeachingReportSlotResponse,
)
def get_report(
    report_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> TeachingReportSlotResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    _require_author_capability(
        service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    identity = db.execute(
        select(StudentReport.enrollment_id).where(
            StudentReport.id == report_id,
            StudentReport.author_user_id == context.account.id,
        )
    ).scalar_one_or_none()
    if identity is None:
        return private_not_found()
    try:
        workspace = service.author_workspace(
            account_id=context.account.id,
            enrollment_id=identity,
            occurred_at=occurred_at,
        )
    except EvaluationResourceNotFound:
        return private_not_found()
    item = next((value for value in workspace if value.slot.report_id == report_id), None)
    return (
        _slot_response(db, item, occurred_at=occurred_at)
        if item is not None
        else private_not_found()
    )


@router.put(
    "/reports/{report_id}/draft",
    response_model=TeachingReportDraftResponse,
    responses=_mutation_responses(),
    openapi_extra=idempotent_json_request_openapi(TeachingReportDraftSave),
)
async def save_report_draft(
    report_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportService,
        Depends(get_teaching_report_service),
    ],
    query_service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
) -> TeachingReportDraftResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    _require_author_capability(
        query_service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    # Client-generated IDs are valid for new drafts.  If the ID already
    # identifies a report, keep that private selector ahead of body parsing.
    if query_service.db.get(StudentReport, report_id) is not None:
        denied = _preauthorize_author_report(
            query_service,
            report_id,
            account_id=context.account.id,
            occurred_at=occurred_at,
        )
        if denied is not None:
            return denied
    input = await parse_input(request, TeachingReportDraftSave)
    result = mutation_call(
        lambda: service.save_draft(
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key(request),
            request_id=new_audit_request_id(),
            report_id=report_id,
        )
    )
    if isinstance(result, JSONResponse):
        return result
    return TeachingReportDraftResponse(
        report_id=result.report.id,
        status=cast(TeachingReportStatus, result.report.status),
        lock_version=result.report.lock_version,
        draft_revision=result.draft.draft_revision,
        body=result.draft.body,
        dimension_values=result.draft.dimension_values,
        idempotent=result.idempotent,
        allowed_actions=_allowed_actions(
            service.db,
            TeachingReportAuthorWorkspaceItem(
                slot=replace(
                    result.slot,
                    report_id=result.report.id,
                    report_status=result.report.status,
                ),
                draft_body=result.draft.body,
                draft_dimension_values=result.draft.dimension_values,
                draft_revision=result.draft.draft_revision,
            ),
            occurred_at=occurred_at,
        ),
    )


@router.post(
    "/reports/{report_id}/submit",
    status_code=201,
    response_model=TeachingReportSubmitResponse,
    responses=_mutation_responses(TeachingReportSubmitResponse),
    openapi_extra=idempotent_json_request_openapi(TeachingReportSubmitInput),
)
async def submit_report(
    report_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportService,
        Depends(get_teaching_report_service),
    ],
    query_service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
) -> TeachingReportSubmitResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    denied = _preauthorize_author_report(
        query_service,
        report_id,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    if denied is not None:
        return denied
    input = await parse_input(request, TeachingReportSubmitInput)
    result = mutation_call(
        lambda: service.submit(
            report_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key(request),
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    payload = TeachingReportSubmitResponse(
        report_id=result.report.id,
        status=cast(TeachingReportStatus, result.report.status),
        lock_version=result.report.lock_version,
        version_id=result.version.id,
        version=result.version.version,
        source=cast(TeachingReportVersionSource, result.version.source),
        published_version_id=result.report.published_version_id,
        idempotent=result.idempotent,
    )
    return (
        JSONResponse(status_code=200, content=payload.model_dump(mode="json"))
        if result.idempotent
        else payload
    )


@router.post(
    "/reports/{report_id}/correction-drafts",
    status_code=201,
    response_model=TeachingReportDraftStartResponse,
    responses=_mutation_responses(TeachingReportDraftStartResponse),
    openapi_extra=idempotent_json_request_openapi(TeachingReportDraftStartInput),
)
async def start_report_correction(
    report_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportService,
        Depends(get_teaching_report_service),
    ],
    query_service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
) -> TeachingReportDraftStartResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    denied = _preauthorize_author_report(
        query_service,
        report_id,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    if denied is not None:
        return denied
    input = await parse_input(request, TeachingReportDraftStartInput)
    result = mutation_call(
        lambda: service.start_correction(
            report_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key(request),
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    payload = TeachingReportDraftStartResponse(
        report_id=result.report.id,
        status=cast(TeachingReportStatus, result.report.status),
        lock_version=result.report.lock_version,
        draft_revision=result.draft.draft_revision,
        body=result.draft.body,
        dimension_values=result.draft.dimension_values,
        idempotent=result.idempotent,
    )
    return (
        JSONResponse(status_code=200, content=payload.model_dump(mode="json"))
        if result.idempotent
        else payload
    )


@router.post(
    "/reports/{report_id}/revisions",
    status_code=201,
    response_model=TeachingReportDraftStartResponse,
    responses=_mutation_responses(TeachingReportDraftStartResponse),
    openapi_extra=idempotent_json_request_openapi(TeachingReportDraftStartInput),
)
async def start_report_revision(
    report_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportService,
        Depends(get_teaching_report_service),
    ],
    query_service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
) -> TeachingReportDraftStartResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    denied = _preauthorize_author_report(
        query_service,
        report_id,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    if denied is not None:
        return denied
    input = await parse_input(request, TeachingReportDraftStartInput)
    result = mutation_call(
        lambda: service.start_revision(
            report_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key(request),
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    payload = TeachingReportDraftStartResponse(
        report_id=result.report.id,
        status=cast(TeachingReportStatus, result.report.status),
        lock_version=result.report.lock_version,
        draft_revision=result.draft.draft_revision,
        body=result.draft.body,
        dimension_values=result.draft.dimension_values,
        idempotent=result.idempotent,
    )
    return (
        JSONResponse(status_code=200, content=payload.model_dump(mode="json"))
        if result.idempotent
        else payload
    )


@router.post(
    "/reports/{report_id}/reviews",
    response_model=TeachingReportReviewResponse,
    responses=_mutation_responses(),
    openapi_extra=idempotent_json_request_openapi(TeachingReportReviewInput),
)
async def review_report(
    report_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportService,
        Depends(get_teaching_report_service),
    ],
    query_service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
) -> TeachingReportReviewResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    try:
        query_service.require_review_report(
            report_id,
            account_id=context.account.id,
            occurred_at=occurred_at,
        )
    except EvaluationOperationDenied as exc:
        raise ProblemError(
            status=403,
            code="evaluation_operation_denied",
            detail="没有审核教学评语的权限",
        ) from exc
    except EvaluationResourceNotFound:
        return private_not_found()
    input = await parse_input(request, TeachingReportReviewInput)
    result = mutation_call(
        lambda: service.review(
            report_id,
            input,
            reviewer_user_id=context.account.id,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key(request),
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    return TeachingReportReviewResponse(
        report_id=result.report.id,
        status=cast(TeachingReportStatus, result.report.status),
        lock_version=result.report.lock_version,
        published_version_id=result.report.published_version_id,
        review_id=result.review.id,
        decision=cast(Literal["approved", "rejected"], result.review.decision),
        idempotent=result.idempotent,
    )


@router.post(
    "/reports/{report_id}/reopen",
    status_code=201,
    response_model=TeachingReportReopenResponse,
    responses=_mutation_responses(TeachingReportReopenResponse),
    openapi_extra=idempotent_json_request_openapi(TeachingReportReopenInput),
)
async def reopen_report(
    report_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportService,
        Depends(get_teaching_report_service),
    ],
    query_service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
) -> TeachingReportReopenResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    try:
        query_service.require_review_report(
            report_id,
            account_id=context.account.id,
            occurred_at=occurred_at,
        )
    except EvaluationOperationDenied as exc:
        raise ProblemError(
            status=403,
            code="evaluation_operation_denied",
            detail="没有审核教学评语的权限",
        ) from exc
    except EvaluationResourceNotFound:
        return private_not_found()
    input = await parse_input(request, TeachingReportReopenInput)
    result = mutation_call(
        lambda: service.reopen(
            report_id,
            input,
            reviewer_user_id=context.account.id,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key(request),
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    payload = TeachingReportReopenResponse(
        report_id=result.report.id,
        status=cast(TeachingReportStatus, result.report.status),
        lock_version=result.report.lock_version,
        grant_id=result.grant.id,
        grant_kind=cast(
            Literal["correction", "revision"],
            result.grant.grant_kind,
        ),
        opens_at=result.grant.opens_at,
        expires_at=result.grant.expires_at,
        idempotent=result.idempotent,
    )
    return (
        JSONResponse(status_code=200, content=payload.model_dump(mode="json"))
        if result.idempotent
        else payload
    )


@router.get(
    "/students/{student_id}/published-reports",
    response_model=TeachingReportPublishedPageResponse,
)
def get_published_reports(
    student_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        TeachingReportQueryService,
        Depends(get_teaching_report_query_service),
    ],
    enrollment_id: Annotated[UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TeachingReportPublishedPageResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = _now()
    _require_family_capability(
        service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    try:
        result = service.published_for_student(
            account_id=context.account.id,
            student_person_id=student_id,
            occurred_at=occurred_at,
            page=page,
            page_size=page_size,
            enrollment_id=enrollment_id,
        )
    except EvaluationOperationDenied as exc:
        raise ProblemError(
            status=403,
            code="evaluation_operation_denied",
            detail="没有查看家庭教学评语的权限",
        ) from exc
    except EvaluationResourceNotFound:
        return private_not_found()
    return TeachingReportPublishedPageResponse(
        items=[
            TeachingReportPublishedItemResponse.model_validate(asdict(item))
            for item in result.items
        ],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )
