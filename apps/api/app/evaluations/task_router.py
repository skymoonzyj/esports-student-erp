from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.dependencies import (
    get_evaluation_context_service,
    get_evaluation_matching_service,
    get_evaluation_media_service,
    get_evaluation_query_service,
    get_evaluation_result_service,
    get_evaluation_task_service,
    get_evaluation_upload_service,
)
from app.evaluations.errors import (
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    MediaAssetNotFound,
)
from app.evaluations.matching_service import EvaluationMatchingService
from app.evaluations.media_service import EvaluationMediaService, ProtectedMediaFile
from app.evaluations.mutation_api_support import (
    idempotency_key,
    json_request_body_openapi,
    mutation_call,
    new_audit_request_id,
    parse_input,
    preauthorize_task,
    private_not_found,
)
from app.evaluations.portal_router import require_evaluation_center
from app.evaluations.query_api_support import (
    task_detail_response,
    task_filter_options_response,
    task_page_response,
    task_workspace_response,
)
from app.evaluations.query_service import EvaluationQueryFilters, EvaluationQueryService
from app.evaluations.result_service import EvaluationResultService
from app.evaluations.review_schemas import (
    EvaluationManualDraftInput,
    EvaluationManualSubmitInput,
    EvaluationResultRevisionInput,
    EvaluationTaskMatchInput,
)
from app.evaluations.task_api_support import (
    create_task_response,
    create_upload_session_response,
    delete_upload_slot_response,
    put_upload_slot_response,
    upload_context_response,
)
from app.evaluations.task_schemas import (
    EvaluationDashboardResponse,
    EvaluationDraftSavedResponse,
    EvaluationResultSubmittedResponse,
    EvaluationRevisionStartedResponse,
    EvaluationTaskCreate,
    EvaluationTaskCreatedResponse,
    EvaluationTaskDetailResponse,
    EvaluationTaskFilterOptionsResponse,
    EvaluationTaskMatchedResponse,
    EvaluationTaskPageResponse,
    EvaluationTaskStatus,
    EvaluationTaskWorkspaceResponse,
    EvaluationUploadContextResponse,
    EvaluationUploadSessionCreate,
    EvaluationUploadSessionResponse,
    EvaluationUploadSlotDeletedResponse,
    EvaluationUploadSlotInput,
    EvaluationUploadSlotResponse,
)
from app.evaluations.task_service import EvaluationTaskService
from app.evaluations.upload_service import EvaluationUploadService

router = APIRouter(prefix="/evaluations/system", tags=["system-evaluations"])

EvaluationAccountQueue = Literal[
    "processing",
    "pending_match",
    "manual_input",
    "needs_review",
    "rejected",
    "published",
    "failed",
    "archived",
]


def _task_media_response(protected: ProtectedMediaFile) -> FileResponse:
    try:
        return FileResponse(
            protected.path,
            media_type=protected.mime_type,
            headers=protected.headers,
            background=BackgroundTask(protected.close),
        )
    except Exception:
        protected.close()
        raise


@router.get("/dashboard", response_model=EvaluationDashboardResponse)
def get_dashboard(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
) -> EvaluationDashboardResponse:
    require_evaluation_center(context=context, service=context_service)
    counts = service.dashboard_counts(
        actor_user_id=context.account.id,
        occurred_at=datetime.now(UTC),
    )
    return EvaluationDashboardResponse.model_validate(
        counts,
        from_attributes=True,
    )


@router.get(
    "/task-filter-options",
    response_model=EvaluationTaskFilterOptionsResponse,
)
def get_task_filter_options(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
) -> EvaluationTaskFilterOptionsResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    return task_filter_options_response(
        lambda: service.account_filter_options(
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
        ),
        operational=False,
    )


@router.get("/tasks", response_model=EvaluationTaskPageResponse)
def list_tasks(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
    status: Annotated[EvaluationTaskStatus | None, Query()] = None,
    queue: Annotated[EvaluationAccountQueue | None, Query()] = None,
    cohort_id: Annotated[UUID | None, Query()] = None,
    project_id: Annotated[UUID | None, Query()] = None,
    class_id: Annotated[UUID | None, Query()] = None,
    student_id: Annotated[UUID | None, Query()] = None,
    published_only: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationTaskPageResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    return task_page_response(
        lambda: service.list_account_tasks(
            actor_user_id=context.account.id,
            filters=EvaluationQueryFilters(
                status=status,
                queue=queue,
                cohort_id=cohort_id,
                project_id=project_id,
                class_id=class_id,
                student_id=student_id,
                published_only=published_only,
            ),
            page=page,
            page_size=page_size,
            occurred_at=occurred_at,
        ),
        operational=False,
    )


@router.get("/tasks/{task_id}", response_model=EvaluationTaskDetailResponse)
def get_task(
    task_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
) -> EvaluationTaskDetailResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    return task_detail_response(
        lambda: service.get_account_task(
            task_id,
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
        ),
        operational=False,
    )


@router.get(
    "/tasks/{task_id}/workspace",
    response_model=EvaluationTaskWorkspaceResponse,
)
def get_task_workspace(
    task_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
) -> EvaluationTaskWorkspaceResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    return task_workspace_response(
        lambda: service.get_account_task_workspace(
            task_id,
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
        ),
        operational=False,
    )


@router.get(
    "/tasks/{task_id}/media/{media_id}/preview",
    response_class=FileResponse,
    response_model=None,
)
def read_task_media_preview(
    task_id: UUID,
    media_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    query_service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
    media_service: Annotated[
        EvaluationMediaService,
        Depends(get_evaluation_media_service),
    ],
) -> FileResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    try:
        query_service.require_task_media(
            task_id,
            media_id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
        )
    except EvaluationOperationDenied as exc:
        raise ProblemError(
            status=403,
            code="evaluation_operation_denied",
            detail="没有执行此评价操作的权限",
        ) from exc
    except EvaluationResourceNotFound:
        return private_not_found()
    try:
        protected = media_service.read_preview(
            media_id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
        )
    except MediaAssetNotFound:
        return private_not_found()
    return _task_media_response(protected)


@router.post(
    "/tasks/{task_id}/match",
    response_model=EvaluationTaskMatchedResponse,
    openapi_extra=json_request_body_openapi(EvaluationTaskMatchInput),
)
async def match_task(
    task_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    query_service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
    service: Annotated[
        EvaluationMatchingService,
        Depends(get_evaluation_matching_service),
    ],
) -> EvaluationTaskMatchedResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    denied = preauthorize_task(
        query_service,
        task_id,
        actor_user_id=context.account.id,
        permission="evaluation_jobs.match",
        occurred_at=occurred_at,
    )
    if denied is not None:
        return denied
    input = await parse_input(request, EvaluationTaskMatchInput)
    result = mutation_call(
        lambda: service.confirm_task_match(
            task_id,
            input,
            actor_user_id=context.account.id,
            idempotency_key=idempotency_key(request),
            occurred_at=occurred_at,
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    return EvaluationTaskMatchedResponse(
        task_id=result.task_id,
        student_person_id=result.student_person_id,
        enrollment_id=result.enrollment_id,
        cohort_id=result.cohort_id,
        project_id=result.project_id,
        class_id=result.class_id,
        task_status=cast(EvaluationTaskStatus, result.task_status),
        task_version=result.task_version,
    )


@router.put(
    "/tasks/{task_id}/manual-draft",
    response_model=EvaluationDraftSavedResponse,
    openapi_extra=json_request_body_openapi(EvaluationManualDraftInput),
)
async def save_manual_draft(
    task_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    query_service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
    service: Annotated[
        EvaluationResultService,
        Depends(get_evaluation_result_service),
    ],
) -> EvaluationDraftSavedResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    denied = preauthorize_task(
        query_service,
        task_id,
        actor_user_id=context.account.id,
        permission="evaluation_jobs.manual_input",
        occurred_at=occurred_at,
    )
    if denied is not None:
        return denied
    input = await parse_input(request, EvaluationManualDraftInput)
    result = mutation_call(
        lambda: service.save_manual_draft(
            task_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    return EvaluationDraftSavedResponse(
        task_id=result.task_id,
        task_status=cast(EvaluationTaskStatus, result.task_status),
        task_version=result.task_version,
        draft_revision=result.draft_revision,
        is_complete=result.is_complete,
        missing_required_fields=list(result.missing_required_fields),
        updated_at=result.updated_at,
    )


@router.post(
    "/tasks/{task_id}/submit-review",
    response_model=EvaluationResultSubmittedResponse,
    status_code=201,
    openapi_extra=json_request_body_openapi(EvaluationManualSubmitInput),
)
async def submit_manual_result(
    task_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    query_service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
    service: Annotated[
        EvaluationResultService,
        Depends(get_evaluation_result_service),
    ],
) -> EvaluationResultSubmittedResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    denied = preauthorize_task(
        query_service,
        task_id,
        actor_user_id=context.account.id,
        permission="evaluation_jobs.manual_input",
        occurred_at=occurred_at,
    )
    if denied is not None:
        return denied
    input = await parse_input(request, EvaluationManualSubmitInput)
    result = mutation_call(
        lambda: service.submit_manual_result(
            task_id,
            input,
            actor_user_id=context.account.id,
            idempotency_key=idempotency_key(request),
            occurred_at=occurred_at,
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    return EvaluationResultSubmittedResponse(
        task_id=result.task_id,
        result_id=result.result_id,
        result_version=result.result_version,
        task_status=cast(EvaluationTaskStatus, result.task_status),
        task_version=result.task_version,
    )


async def _start_result_revision(
    *,
    task_id: UUID,
    request: Request,
    context: AuthContext,
    context_service: EvaluationContextService,
    query_service: EvaluationQueryService,
    service: EvaluationResultService,
    correction: bool,
) -> EvaluationRevisionStartedResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    denied = preauthorize_task(
        query_service,
        task_id,
        actor_user_id=context.account.id,
        permission="evaluation_jobs.manual_input",
        occurred_at=occurred_at,
    )
    if denied is not None:
        return denied
    input = await parse_input(request, EvaluationResultRevisionInput)
    operation = service.start_correction if correction else service.start_revision
    result = mutation_call(
        lambda: operation(
            task_id,
            input,
            actor_user_id=context.account.id,
            idempotency_key=idempotency_key(request),
            occurred_at=occurred_at,
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    return EvaluationRevisionStartedResponse(
        task_id=result.task_id,
        task_status=cast(EvaluationTaskStatus, result.task_status),
        task_version=result.task_version,
        draft_revision=result.draft_revision,
    )


@router.post(
    "/tasks/{task_id}/corrections",
    response_model=EvaluationRevisionStartedResponse,
    status_code=201,
    openapi_extra=json_request_body_openapi(EvaluationResultRevisionInput),
)
async def start_correction(
    task_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    query_service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
    service: Annotated[
        EvaluationResultService,
        Depends(get_evaluation_result_service),
    ],
) -> EvaluationRevisionStartedResponse | JSONResponse:
    return await _start_result_revision(
        task_id=task_id,
        request=request,
        context=context,
        context_service=context_service,
        query_service=query_service,
        service=service,
        correction=True,
    )


@router.post(
    "/tasks/{task_id}/revisions",
    response_model=EvaluationRevisionStartedResponse,
    status_code=201,
    openapi_extra=json_request_body_openapi(EvaluationResultRevisionInput),
)
async def start_revision(
    task_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    query_service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
    service: Annotated[
        EvaluationResultService,
        Depends(get_evaluation_result_service),
    ],
) -> EvaluationRevisionStartedResponse | JSONResponse:
    return await _start_result_revision(
        task_id=task_id,
        request=request,
        context=context,
        context_service=context_service,
        query_service=query_service,
        service=service,
        correction=False,
    )


@router.get("/context", response_model=EvaluationUploadContextResponse)
def system_evaluation_context(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
) -> EvaluationUploadContextResponse:
    require_evaluation_center(context=context, service=context_service)
    return upload_context_response(
        context_service=context_service,
        actor_user_id=context.account.id,
        occurred_at=datetime.now(UTC),
    )


@router.post(
    "/upload-sessions",
    response_model=EvaluationUploadSessionResponse,
    status_code=201,
)
def create_upload_session(
    input: EvaluationUploadSessionCreate,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationUploadService,
        Depends(get_evaluation_upload_service),
    ],
) -> EvaluationUploadSessionResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    return create_upload_session_response(
        input,
        actor_user_id=context.account.id,
        occurred_at=datetime.now(UTC),
        context_service=context_service,
        service=service,
    )


@router.put(
    "/upload-sessions/{session_id}/slots/{slot_key}",
    response_model=EvaluationUploadSlotResponse,
)
def put_upload_slot(
    session_id: UUID,
    slot_key: str,
    input: Annotated[EvaluationUploadSlotInput, Form()],
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationUploadService,
        Depends(get_evaluation_upload_service),
    ],
) -> EvaluationUploadSlotResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    return put_upload_slot_response(
        session_id,
        slot_key,
        input.file,
        actor_user_id=context.account.id,
        occurred_at=datetime.now(UTC),
        context_service=context_service,
        service=service,
    )


@router.delete(
    "/upload-sessions/{session_id}/slots/{slot_key}",
    response_model=EvaluationUploadSlotDeletedResponse,
)
def delete_upload_slot(
    session_id: UUID,
    slot_key: str,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationUploadService,
        Depends(get_evaluation_upload_service),
    ],
) -> EvaluationUploadSlotDeletedResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    return delete_upload_slot_response(
        session_id,
        slot_key,
        actor_user_id=context.account.id,
        occurred_at=datetime.now(UTC),
        context_service=context_service,
        service=service,
    )


@router.post(
    "/upload-sessions/{session_id}/tasks",
    response_model=EvaluationTaskCreatedResponse,
    status_code=201,
)
def create_task(
    session_id: UUID,
    input: EvaluationTaskCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationTaskService,
        Depends(get_evaluation_task_service),
    ],
) -> EvaluationTaskCreatedResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    return create_task_response(
        session_id,
        input,
        request,
        actor_user_id=context.account.id,
        occurred_at=datetime.now(UTC),
        context_service=context_service,
        service=service,
    )
