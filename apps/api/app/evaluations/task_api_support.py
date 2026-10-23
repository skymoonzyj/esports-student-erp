from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse

from app.core.errors import ProblemError
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    EvaluationUploadLimitReached,
    MediaValidationFailed,
)
from app.evaluations.schemas import EvaluationToolSessionContext
from app.evaluations.task_schemas import (
    EvaluationTaskCreate,
    EvaluationTaskCreatedResponse,
    EvaluationTaskStatus,
    EvaluationUploadContextResponse,
    EvaluationUploadSessionCreate,
    EvaluationUploadSessionResponse,
    EvaluationUploadSlotDeletedResponse,
    EvaluationUploadSlotResponse,
    EvaluationWebSessionContextResponse,
)
from app.evaluations.task_service import (
    EvaluationTaskRecord,
    EvaluationTaskService,
    EvaluationTaskTarget,
)
from app.evaluations.upload_service import (
    EvaluationUploadService,
    UploadSessionRecord,
    UploadSlotRecord,
)

_ServiceRecord = UploadSessionRecord | UploadSlotRecord | EvaluationTaskRecord


def _private_resource_not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "type": "about:blank",
            "title": "Not Found",
            "status": 404,
            "detail": "评价资源不存在",
            "code": "evaluation_resource_not_found",
        },
    )


def _service_call(
    call: Callable[[], _ServiceRecord | None],
) -> _ServiceRecord | None | JSONResponse:
    try:
        return call()
    except (EvaluationResourceNotFound, EvaluationOperationDenied):
        return _private_resource_not_found()
    except EvaluationIdempotencyConflict as exc:
        raise ProblemError(
            status=409,
            code="evaluation_idempotency_conflict",
            detail="幂等请求与原请求不一致",
        ) from exc
    except EvaluationUploadLimitReached as exc:
        raise ProblemError(
            status=429,
            code=exc.code,
            detail="暂存上传任务已达到上限，请先完成已有上传或稍后再试",
        ) from exc
    except MediaValidationFailed as exc:
        raise ProblemError(
            status=422,
            code="media_validation_failed",
            detail="评价媒体无效",
        ) from exc
    except ValueError as exc:
        raise ProblemError(
            status=422,
            code="evaluation_request_invalid",
            detail="评价请求无效",
        ) from exc


def require_upload_operation(
    *,
    context_service: EvaluationContextService,
    actor_user_id: UUID,
    occurred_at: datetime,
) -> None:
    if not context_service.upload_operation_allowed(
        account_id=actor_user_id,
        occurred_at=occurred_at,
    ):
        raise ProblemError(
            status=403,
            code="evaluation_operation_denied",
            detail="没有执行此评价操作的权限",
        )


def upload_context_response(
    *,
    context_service: EvaluationContextService,
    actor_user_id: UUID,
    occurred_at: datetime,
) -> EvaluationUploadContextResponse:
    targets = context_service.upload_targets(
        account_id=actor_user_id,
        occurred_at=occurred_at,
    )
    return EvaluationUploadContextResponse(
        upload_available=bool(targets),
        targets=targets,
    )


def web_session_context_response(
    *,
    delegated_context: EvaluationToolSessionContext,
    context_service: EvaluationContextService,
    occurred_at: datetime,
) -> EvaluationWebSessionContextResponse:
    targets = context_service.upload_targets(
        account_id=delegated_context.account_id,
        occurred_at=occurred_at,
    )
    display_name = delegated_context.display_name
    identity_summary = (
        "*"
        if len(display_name) <= 1
        else display_name[0] + "*" * (len(display_name) - 1)
    )
    return EvaluationWebSessionContextResponse(
        identity_summary=identity_summary,
        upload_available=bool(targets),
    )


def create_upload_session_response(
    input: EvaluationUploadSessionCreate,
    *,
    actor_user_id: UUID,
    occurred_at: datetime,
    context_service: EvaluationContextService,
    service: EvaluationUploadService,
) -> EvaluationUploadSessionResponse | JSONResponse:
    require_upload_operation(
        context_service=context_service,
        actor_user_id=actor_user_id,
        occurred_at=occurred_at,
    )
    result = _service_call(
        lambda: service.create_session(
            project_id=input.project_id,
            created_by_user_id=actor_user_id,
            occurred_at=occurred_at,
            student_person_id=input.student_person_id,
            enrollment_id=input.enrollment_id,
            delegated_upload_reason=input.delegated_upload_reason,
        )
    )
    if isinstance(result, JSONResponse):
        return result
    assert isinstance(result, UploadSessionRecord)
    return EvaluationUploadSessionResponse(
        id=result.id,
        status=cast(
            Literal["open", "committed", "expired", "cancelled"],
            result.status,
        ),
        expires_at=result.expires_at,
    )


def put_upload_slot_response(
    session_id: UUID,
    slot_key: str,
    file: UploadFile,
    *,
    actor_user_id: UUID,
    occurred_at: datetime,
    context_service: EvaluationContextService,
    service: EvaluationUploadService,
) -> EvaluationUploadSlotResponse | JSONResponse:
    require_upload_operation(
        context_service=context_service,
        actor_user_id=actor_user_id,
        occurred_at=occurred_at,
    )
    result = _service_call(
        lambda: service.put_slot(
            session_id,
            slot_key=slot_key,
            stream=file.file,
            declared_filename=file.filename or "",
            declared_mime_type=file.content_type or "",
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
    )
    if isinstance(result, JSONResponse):
        return result
    assert isinstance(result, UploadSlotRecord)
    return EvaluationUploadSlotResponse(
        id=result.id,
        session_id=result.session_id,
        slot_key=result.slot_key,
        position=result.position,
        mime_type=cast(
            Literal["image/jpeg", "image/png", "image/webp"],
            result.mime_type,
        ),
        size_bytes=result.size_bytes,
        width_pixels=result.width_pixels,
        height_pixels=result.height_pixels,
    )


def delete_upload_slot_response(
    session_id: UUID,
    slot_key: str,
    *,
    actor_user_id: UUID,
    occurred_at: datetime,
    context_service: EvaluationContextService,
    service: EvaluationUploadService,
) -> EvaluationUploadSlotDeletedResponse | JSONResponse:
    require_upload_operation(
        context_service=context_service,
        actor_user_id=actor_user_id,
        occurred_at=occurred_at,
    )
    result = _service_call(
        lambda: service.delete_slot(
            session_id,
            slot_key=slot_key,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
    )
    if isinstance(result, JSONResponse):
        return result
    return EvaluationUploadSlotDeletedResponse(
        session_id=session_id,
        slot_key=slot_key,
    )


def _idempotency_key(request: Request) -> str:
    values = request.headers.getlist("Idempotency-Key")
    if len(values) != 1:
        raise ProblemError(
            status=422,
            code="evaluation_idempotency_key_invalid",
            detail="幂等键无效",
        )
    value = values[0]
    if (
        not 1 <= len(value) <= 200
        or value != value.strip()
        or "," in value
        or any(character.isspace() for character in value)
    ):
        raise ProblemError(
            status=422,
            code="evaluation_idempotency_key_invalid",
            detail="幂等键无效",
        )
    return value


def _audit_request_id(request: Request) -> str:
    request_id = str(request.state.request_id)
    sensitive_values = request.headers.getlist(
        "X-Evaluation-Delegated-Session"
    )
    for authorization in request.headers.getlist("Authorization"):
        sensitive_values.append(authorization)
        if authorization.startswith("Bearer "):
            sensitive_values.append(authorization.removeprefix("Bearer "))
    if any(
        sensitive_value and sensitive_value in request_id
        for sensitive_value in sensitive_values
    ):
        return str(uuid4())
    return request_id


def create_task_response(
    session_id: UUID,
    input: EvaluationTaskCreate,
    request: Request,
    *,
    actor_user_id: UUID,
    occurred_at: datetime,
    context_service: EvaluationContextService,
    service: EvaluationTaskService,
) -> EvaluationTaskCreatedResponse | JSONResponse:
    require_upload_operation(
        context_service=context_service,
        actor_user_id=actor_user_id,
        occurred_at=occurred_at,
    )
    result = _service_call(
        lambda: service.create_task(
            session_id,
            actor_user_id=actor_user_id,
            target=EvaluationTaskTarget(
                student_person_id=input.student_person_id,
                enrollment_id=input.enrollment_id,
                cohort_id=input.cohort_id,
                class_id=input.class_id,
                upload_window_start=occurred_at,
                upload_window_end=occurred_at,
            ),
            occurred_at=occurred_at,
            request_id=_audit_request_id(request),
            idempotency_key=_idempotency_key(request),
        )
    )
    if isinstance(result, JSONResponse):
        return result
    assert isinstance(result, EvaluationTaskRecord)
    return EvaluationTaskCreatedResponse(
        id=result.id,
        public_number=result.public_number,
        status=cast(EvaluationTaskStatus, result.status),
        lock_version=result.lock_version,
    )
