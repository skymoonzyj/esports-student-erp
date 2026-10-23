from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from app.evaluations.context_service import EvaluationContextService
from app.evaluations.dependencies import (
    get_evaluation_context_service,
    get_evaluation_query_service,
    get_evaluation_task_service,
    get_evaluation_upload_service,
    require_evaluation_delegated_session,
    require_evaluation_tool_web_service,
)
from app.evaluations.query_api_support import task_detail_response, task_page_response
from app.evaluations.query_service import EvaluationQueryFilters, EvaluationQueryService
from app.evaluations.schemas import EvaluationToolSessionContext
from app.evaluations.task_api_support import (
    create_task_response,
    create_upload_session_response,
    delete_upload_slot_response,
    put_upload_slot_response,
    upload_context_response,
    web_session_context_response,
)
from app.evaluations.task_schemas import (
    EvaluationTaskCreate,
    EvaluationTaskCreatedResponse,
    EvaluationTaskDetailResponse,
    EvaluationTaskPageResponse,
    EvaluationTaskStatus,
    EvaluationUploadContextResponse,
    EvaluationUploadSessionCreate,
    EvaluationUploadSessionResponse,
    EvaluationUploadSlotDeletedResponse,
    EvaluationUploadSlotInput,
    EvaluationUploadSlotResponse,
    EvaluationWebSessionContextResponse,
)
from app.evaluations.task_service import EvaluationTaskService
from app.evaluations.upload_service import EvaluationUploadService

router = APIRouter(
    prefix="/integrations/evaluations/v2/web",
    tags=["evaluation-integration-v2-web"],
    dependencies=[Depends(require_evaluation_tool_web_service)],
)


@router.get("/tasks", response_model=EvaluationTaskPageResponse)
def list_tasks(
    delegated_context: Annotated[
        EvaluationToolSessionContext,
        Depends(require_evaluation_delegated_session),
    ],
    service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
    status: EvaluationTaskStatus | None = None,
    cohort_id: UUID | None = None,
    project_id: UUID | None = None,
    class_id: UUID | None = None,
    student_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> EvaluationTaskPageResponse | JSONResponse:
    occurred_at = datetime.now(UTC)
    return task_page_response(
        lambda: service.list_account_tasks(
            actor_user_id=delegated_context.account_id,
            filters=EvaluationQueryFilters(
                status=status,
                cohort_id=cohort_id,
                project_id=project_id,
                class_id=class_id,
                student_id=student_id,
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
    delegated_context: Annotated[
        EvaluationToolSessionContext,
        Depends(require_evaluation_delegated_session),
    ],
    service: Annotated[
        EvaluationQueryService,
        Depends(get_evaluation_query_service),
    ],
) -> EvaluationTaskDetailResponse | JSONResponse:
    return task_detail_response(
        lambda: service.get_account_task(
            task_id,
            actor_user_id=delegated_context.account_id,
            occurred_at=datetime.now(UTC),
        ),
        operational=False,
    )


@router.post(
    "/session-context",
    response_model=EvaluationWebSessionContextResponse,
)
def delegated_session_context(
    delegated_context: Annotated[
        EvaluationToolSessionContext,
        Depends(require_evaluation_delegated_session),
    ],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
) -> EvaluationWebSessionContextResponse:
    return web_session_context_response(
        delegated_context=delegated_context,
        context_service=context_service,
        occurred_at=datetime.now(UTC),
    )


@router.get(
    "/upload-context",
    response_model=EvaluationUploadContextResponse,
)
def delegated_upload_context(
    delegated_context: Annotated[
        EvaluationToolSessionContext,
        Depends(require_evaluation_delegated_session),
    ],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
) -> EvaluationUploadContextResponse:
    return upload_context_response(
        context_service=context_service,
        actor_user_id=delegated_context.account_id,
        occurred_at=datetime.now(UTC),
    )


@router.post(
    "/upload-sessions",
    response_model=EvaluationUploadSessionResponse,
    status_code=201,
)
def create_upload_session(
    delegated_context: Annotated[
        EvaluationToolSessionContext,
        Depends(require_evaluation_delegated_session),
    ],
    input: EvaluationUploadSessionCreate,
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationUploadService,
        Depends(get_evaluation_upload_service),
    ],
) -> EvaluationUploadSessionResponse | JSONResponse:
    return create_upload_session_response(
        input,
        actor_user_id=delegated_context.account_id,
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
    delegated_context: Annotated[
        EvaluationToolSessionContext,
        Depends(require_evaluation_delegated_session),
    ],
    input: Annotated[EvaluationUploadSlotInput, Form()],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationUploadService,
        Depends(get_evaluation_upload_service),
    ],
) -> EvaluationUploadSlotResponse | JSONResponse:
    return put_upload_slot_response(
        session_id,
        slot_key,
        input.file,
        actor_user_id=delegated_context.account_id,
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
    delegated_context: Annotated[
        EvaluationToolSessionContext,
        Depends(require_evaluation_delegated_session),
    ],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationUploadService,
        Depends(get_evaluation_upload_service),
    ],
) -> EvaluationUploadSlotDeletedResponse | JSONResponse:
    return delete_upload_slot_response(
        session_id,
        slot_key,
        actor_user_id=delegated_context.account_id,
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
    delegated_context: Annotated[
        EvaluationToolSessionContext,
        Depends(require_evaluation_delegated_session),
    ],
    input: EvaluationTaskCreate,
    request: Request,
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationTaskService,
        Depends(get_evaluation_task_service),
    ],
) -> EvaluationTaskCreatedResponse | JSONResponse:
    return create_task_response(
        session_id,
        input,
        request,
        actor_user_id=delegated_context.account_id,
        occurred_at=datetime.now(UTC),
        context_service=context_service,
        service=service,
    )
