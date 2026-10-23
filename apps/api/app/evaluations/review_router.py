from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.evaluations.adapter_certification_service import (
    EvaluationAdapterCertificationService,
)
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.dependencies import (
    get_evaluation_adapter_certification_service,
    get_evaluation_context_service,
    get_evaluation_matching_service,
    get_evaluation_query_service,
    get_evaluation_result_service,
    get_evaluation_review_service,
)
from app.evaluations.errors import EvaluationOperationDenied
from app.evaluations.matching_service import EvaluationMatchingService
from app.evaluations.mutation_api_support import (
    idempotency_key,
    json_request_body_openapi,
    mutation_call,
    new_audit_request_id,
    parse_input,
    preauthorize_task,
)
from app.evaluations.portal_router import require_evaluation_center
from app.evaluations.query_api_support import (
    match_target_page_response,
    task_detail_response,
    task_filter_options_response,
    task_page_response,
    task_workspace_response,
)
from app.evaluations.query_service import EvaluationQueryFilters, EvaluationQueryService
from app.evaluations.result_service import EvaluationResultService
from app.evaluations.review_schemas import (
    EvaluationAdapterCertificationInput,
    EvaluationArchiveInput,
    EvaluationManualDraftInput,
    EvaluationReopenInput,
    EvaluationReviewInput,
    EvaluationTaskMatchInput,
)
from app.evaluations.review_service import EvaluationReviewService
from app.evaluations.schemas import (
    EvaluationProjectDirectoryItem,
    EvaluationProjectDirectoryPage,
)
from app.evaluations.task_schemas import (
    EvaluationAdapterCertificationItem,
    EvaluationAdapterCertificationListResponse,
    EvaluationAdapterCertificationResponse,
    EvaluationDraftSavedResponse,
    EvaluationMatchTargetPageResponse,
    EvaluationReviewedResponse,
    EvaluationTaskDetailResponse,
    EvaluationTaskFilterOptionsResponse,
    EvaluationTaskMatchedResponse,
    EvaluationTaskPageResponse,
    EvaluationTaskStateChangedResponse,
    EvaluationTaskStatus,
    EvaluationTaskWorkspaceResponse,
)

router = APIRouter(
    prefix="/evaluations/admin",
    tags=["system evaluation administration"],
)

EvaluationAdminQueue = Literal[
    "processing",
    "pending_match",
    "manual_input",
    "needs_review",
    "rejected",
    "published",
    "failed",
    "archived",
]


def _operation_denied(exc: EvaluationOperationDenied) -> NoReturn:
    raise ProblemError(
        status=403,
        code="evaluation_operation_denied",
        detail="没有执行此评价操作的权限",
    ) from exc


@router.get("/system-tasks", response_model=EvaluationTaskPageResponse)
def list_system_tasks(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
    queue: Annotated[EvaluationAdminQueue | None, Query()] = None,
    cohort_id: Annotated[UUID | None, Query()] = None,
    project_id: Annotated[UUID | None, Query()] = None,
    class_id: Annotated[UUID | None, Query()] = None,
    student_id: Annotated[UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationTaskPageResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    return task_page_response(
        lambda: service.list_admin_tasks(
            actor_user_id=context.account.id,
            filters=EvaluationQueryFilters(
                queue=queue,
                cohort_id=cohort_id,
                project_id=project_id,
                class_id=class_id,
                student_id=student_id,
            ),
            page=page,
            page_size=page_size,
            occurred_at=occurred_at,
        ),
        operational=True,
    )


@router.get("/system-tasks/{task_id}", response_model=EvaluationTaskDetailResponse)
def get_system_task(
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
        lambda: service.get_admin_task(
            task_id,
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
        ),
        operational=True,
    )


@router.get(
    "/system-tasks/{task_id}/workspace",
    response_model=EvaluationTaskWorkspaceResponse,
)
def get_system_task_workspace(
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
        lambda: service.get_admin_task_workspace(
            task_id,
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
        ),
        operational=True,
    )


@router.get(
    "/system-task-filter-options",
    response_model=EvaluationTaskFilterOptionsResponse,
)
def get_system_task_filter_options(
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
        lambda: service.admin_filter_options(
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
        ),
        operational=True,
    )


@router.get(
    "/system-tasks/{task_id}/match-targets",
    response_model=EvaluationMatchTargetPageResponse,
)
def list_system_task_match_targets(
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
    query: Annotated[str, Query(max_length=100)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationMatchTargetPageResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    return match_target_page_response(
        lambda: service.list_match_targets(
            task_id,
            actor_user_id=context.account.id,
            query=query,
            page=page,
            page_size=page_size,
            occurred_at=datetime.now(UTC),
        )
    )


@router.post(
    "/system-tasks/{task_id}/match",
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
    "/system-tasks/{task_id}/manual-draft",
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
    "/system-tasks/{task_id}/reviews",
    response_model=EvaluationReviewedResponse,
    openapi_extra=json_request_body_openapi(EvaluationReviewInput),
)
async def review_result(
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
        EvaluationReviewService,
        Depends(get_evaluation_review_service),
    ],
) -> EvaluationReviewedResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    denied = preauthorize_task(
        query_service,
        task_id,
        actor_user_id=context.account.id,
        permission="evaluation_jobs.review",
        occurred_at=occurred_at,
    )
    if denied is not None:
        return denied
    input = await parse_input(request, EvaluationReviewInput)
    result = mutation_call(
        lambda: service.review(
            task_id,
            input,
            reviewer_user_id=context.account.id,
            idempotency_key=idempotency_key(request),
            occurred_at=occurred_at,
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    return EvaluationReviewedResponse(
        task_id=result.task_id,
        result_id=result.result_id,
        result_version=result.result_version,
        task_status=cast(EvaluationTaskStatus, result.task_status),
        task_version=result.task_version,
        review_decision=cast(
            Literal["approved", "rejected"],
            result.review_decision,
        ),
        reviewed_at=result.reviewed_at,
        published_at=result.published_at,
    )


async def _change_task_state(
    *,
    task_id: UUID,
    request: Request,
    context: AuthContext,
    context_service: EvaluationContextService,
    query_service: EvaluationQueryService,
    service: EvaluationReviewService,
    reopen: bool,
) -> EvaluationTaskStateChangedResponse | JSONResponse:
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
    if reopen:
        reopen_input = await parse_input(request, EvaluationReopenInput)
        result = mutation_call(
            lambda: service.reopen(
                task_id,
                reopen_input,
                actor_user_id=context.account.id,
                idempotency_key=idempotency_key(request),
                occurred_at=occurred_at,
                request_id=new_audit_request_id(),
            )
        )
    else:
        archive_input = await parse_input(request, EvaluationArchiveInput)
        result = mutation_call(
            lambda: service.archive(
                task_id,
                archive_input,
                actor_user_id=context.account.id,
                idempotency_key=idempotency_key(request),
                occurred_at=occurred_at,
                request_id=new_audit_request_id(),
            )
        )
    if isinstance(result, JSONResponse):
        return result
    return EvaluationTaskStateChangedResponse(
        task_id=result.task_id,
        task_status=cast(EvaluationTaskStatus, result.task_status),
        task_version=result.task_version,
    )


@router.post(
    "/system-tasks/{task_id}/archive",
    response_model=EvaluationTaskStateChangedResponse,
    openapi_extra=json_request_body_openapi(EvaluationArchiveInput),
)
async def archive_task(
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
        EvaluationReviewService,
        Depends(get_evaluation_review_service),
    ],
) -> EvaluationTaskStateChangedResponse | JSONResponse:
    return await _change_task_state(
        task_id=task_id,
        request=request,
        context=context,
        context_service=context_service,
        query_service=query_service,
        service=service,
        reopen=False,
    )


@router.post(
    "/system-tasks/{task_id}/reopen",
    response_model=EvaluationTaskStateChangedResponse,
    openapi_extra=json_request_body_openapi(EvaluationReopenInput),
)
async def reopen_task(
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
        EvaluationReviewService,
        Depends(get_evaluation_review_service),
    ],
) -> EvaluationTaskStateChangedResponse | JSONResponse:
    return await _change_task_state(
        task_id=task_id,
        request=request,
        context=context,
        context_service=context_service,
        query_service=query_service,
        service=service,
        reopen=True,
    )


@router.get(
    "/adapter-certification-projects",
    response_model=EvaluationProjectDirectoryPage,
)
def list_adapter_certification_projects(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationAdapterCertificationService,
        Depends(get_evaluation_adapter_certification_service),
    ],
    query: Annotated[str, Query(max_length=80)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationProjectDirectoryPage:
    require_evaluation_center(context=context, service=context_service)
    try:
        result = service.list_projects(
            actor_user_id=context.account.id,
            query=query,
            page=page,
            page_size=page_size,
            occurred_at=datetime.now(UTC),
        )
    except EvaluationOperationDenied as exc:
        _operation_denied(exc)
    return EvaluationProjectDirectoryPage(
        items=[
            EvaluationProjectDirectoryItem(
                id=project.id,
                name=project.name,
                is_active=project.is_active,
            )
            for project in result.items
        ],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get(
    "/adapter-certifications",
    response_model=EvaluationAdapterCertificationListResponse,
)
def list_adapter_certifications(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationAdapterCertificationService,
        Depends(get_evaluation_adapter_certification_service),
    ],
) -> EvaluationAdapterCertificationListResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    try:
        result = service.list_certifications(
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
        )
    except EvaluationOperationDenied as exc:
        _operation_denied(exc)
    return EvaluationAdapterCertificationListResponse(
        items=[
            EvaluationAdapterCertificationItem.model_validate(
                item,
                from_attributes=True,
            )
            for item in result
        ]
    )


@router.post(
    "/adapter-certifications",
    response_model=EvaluationAdapterCertificationResponse,
    status_code=201,
    openapi_extra=json_request_body_openapi(EvaluationAdapterCertificationInput),
)
async def create_adapter_certification(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationAdapterCertificationService,
        Depends(get_evaluation_adapter_certification_service),
    ],
) -> EvaluationAdapterCertificationResponse | JSONResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    try:
        service.require_capability(
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
        )
    except EvaluationOperationDenied as exc:
        _operation_denied(exc)
    input = await parse_input(request, EvaluationAdapterCertificationInput)
    result = mutation_call(
        lambda: service.create(
            input,
            actor_user_id=context.account.id,
            idempotency_key=idempotency_key(request),
            occurred_at=occurred_at,
            request_id=new_audit_request_id(),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    return EvaluationAdapterCertificationResponse(
        certification=EvaluationAdapterCertificationItem.model_validate(
            result,
            from_attributes=True,
        )
    )
