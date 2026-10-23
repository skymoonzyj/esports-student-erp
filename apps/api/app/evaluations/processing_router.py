from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from app.core.errors import ProblemError
from app.evaluations.dependencies import (
    get_evaluation_processing_service,
    get_worker_media_ticket_service,
    require_evaluation_tool_worker_service,
)
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationLeaseInvalid,
    EvaluationResourceNotFound,
    EvaluationResultInvalid,
)
from app.evaluations.processing_schemas import (
    EvaluationWorkerClaimEnvelope,
    EvaluationWorkerClaimInput,
    EvaluationWorkerClaimMediaResponse,
    EvaluationWorkerClaimResponse,
    EvaluationWorkerFailureResponse,
    EvaluationWorkerLeaseRenewalResponse,
    EvaluationWorkerResultResponse,
    SystemEvaluationFailureInput,
    SystemEvaluationResultInput,
)
from app.evaluations.processing_service import (
    EvaluationProcessingService,
)
from app.evaluations.worker_ticket import WorkerMediaTicketService

_WORKER_IDENTITY = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}\Z")
_LEASE_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,200}\Z")
_MEDIA_TICKET = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")


@dataclass(frozen=True)
class EvaluationWorkerLeaseHeaders:
    worker_identity: str
    task_version: int
    lease_token: str


@dataclass(frozen=True)
class EvaluationWorkerMediaHeaders:
    worker_identity: str
    task_version: int


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


def _single_header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    return values[0] if len(values) == 1 else None


def _worker_identity_and_version(request: Request) -> tuple[str, int] | None:
    worker_identity = _single_header(request, "X-Evaluation-Worker-Identity")
    raw_task_version = _single_header(request, "X-Evaluation-Task-Version")
    try:
        task_version = int(raw_task_version or "")
    except ValueError:
        task_version = 0
    if (
        worker_identity is None
        or _WORKER_IDENTITY.fullmatch(worker_identity) is None
        or raw_task_version != str(task_version)
        or task_version < 1
    ):
        return None
    return worker_identity, task_version


def require_worker_lease_headers(request: Request) -> EvaluationWorkerLeaseHeaders:
    identity_and_version = _worker_identity_and_version(request)
    lease_token = _single_header(request, "X-Evaluation-Lease-Token")
    if (
        identity_and_version is None
        or lease_token is None
        or _LEASE_TOKEN.fullmatch(lease_token) is None
    ):
        raise ProblemError(
            status=404,
            code="evaluation_resource_not_found",
            detail="评价资源不存在",
        )
    worker_identity, task_version = identity_and_version
    return EvaluationWorkerLeaseHeaders(
        worker_identity=worker_identity,
        task_version=task_version,
        lease_token=lease_token,
    )


def require_worker_media_headers(request: Request) -> EvaluationWorkerMediaHeaders:
    identity_and_version = _worker_identity_and_version(request)
    if identity_and_version is None:
        raise ProblemError(
            status=404,
            code="evaluation_resource_not_found",
            detail="评价资源不存在",
        )
    worker_identity, task_version = identity_and_version
    return EvaluationWorkerMediaHeaders(
        worker_identity=worker_identity,
        task_version=task_version,
    )


def require_worker_media_ticket(request: Request) -> str:
    ticket = _single_header(request, "X-Evaluation-Media-Ticket")
    if ticket is None or len(ticket) > 2048 or _MEDIA_TICKET.fullmatch(ticket) is None:
        raise ProblemError(
            status=404,
            code="evaluation_resource_not_found",
            detail="评价资源不存在",
        )
    return ticket


def _audit_request_id(
    request: Request,
    *,
    body_values: tuple[str, ...] = (),
) -> str:
    request_id = str(request.state.request_id)
    sensitive_values = [
        *request.headers.getlist("X-Evaluation-Lease-Token"),
        *request.headers.getlist("X-Evaluation-Media-Ticket"),
        *body_values,
    ]
    for authorization in _authorization_values(request):
        sensitive_values.append(authorization)
        if " " in authorization:
            sensitive_values.append(authorization.split(" ", 1)[1])
    if any(value and value in request_id for value in sensitive_values):
        return f"evaluation-worker-{uuid4()}"
    return request_id


def _result_body_values(input: SystemEvaluationResultInput) -> tuple[str, ...]:
    field_text = tuple(
        value for value in input.fields.values() if isinstance(value, str)
    )
    return (
        input.idempotency_key,
        input.grade,
        input.advice,
        *(item.summary for item in input.warnings),
        *(item.summary for item in input.components),
        *field_text,
    )


def _failure_body_values(input: SystemEvaluationFailureInput) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            input.idempotency_key,
            input.safe_error_summary,
            input.trace_id,
        )
        if value is not None
    )


def _authorization_values(request: Request) -> list[str]:
    return request.headers.getlist("Authorization")


def _service_call[T](call: Callable[[], T]) -> T | JSONResponse:
    try:
        return call()
    except (EvaluationLeaseInvalid, EvaluationResourceNotFound):
        return _private_resource_not_found()
    except EvaluationIdempotencyConflict as exc:
        raise ProblemError(
            status=409,
            code="evaluation_idempotency_conflict",
            detail="幂等请求与原请求不一致",
        ) from exc
    except EvaluationResultInvalid as exc:
        raise ProblemError(
            status=422,
            code="evaluation_result_invalid",
            detail="评价结果无效",
        ) from exc


async def _result_body(request: Request) -> SystemEvaluationResultInput:
    try:
        return SystemEvaluationResultInput.model_validate(await request.json())
    except (TypeError, ValueError) as exc:
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="Request validation failed",
            errors=[{"field": "body", "message": "评价结果请求无效"}],
        ) from exc


async def _failure_body(request: Request) -> SystemEvaluationFailureInput:
    try:
        return SystemEvaluationFailureInput.model_validate(await request.json())
    except (TypeError, ValueError) as exc:
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="Request validation failed",
            errors=[{"field": "body", "message": "评价失败请求无效"}],
        ) from exc


router = APIRouter(
    prefix="/integrations/evaluations/v2/worker",
    tags=["evaluation-integration-v2-worker"],
    dependencies=[Depends(require_evaluation_tool_worker_service)],
)


@router.post("/claims", response_model=EvaluationWorkerClaimEnvelope)
def claim_next(
    input: EvaluationWorkerClaimInput,
    request: Request,
    service: Annotated[
        EvaluationProcessingService,
        Depends(get_evaluation_processing_service),
    ],
) -> EvaluationWorkerClaimEnvelope:
    claim = service.claim_next(
        worker_identity=input.worker_identity,
        supported_adapters=set(input.supported_adapters),
        occurred_at=datetime.now(UTC),
        request_id=_audit_request_id(request),
    )
    if claim is None:
        return EvaluationWorkerClaimEnvelope(claim=None)
    return EvaluationWorkerClaimEnvelope(
        claim=EvaluationWorkerClaimResponse(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            lease_token=claim.lease_token,
            lease_expires_at=claim.lease_expires_at,
            adapter_id=claim.adapter_id,
            adapter_version=claim.adapter_version,
            scoring_rule_version=claim.scoring_rule_version,
            result_schema_version=claim.result_schema_version,
            required_fields=list(claim.required_fields),
            confidence_thresholds=claim.confidence_thresholds,
            media=[
                EvaluationWorkerClaimMediaResponse(
                    media_id=item.media_id,
                    position=item.position,
                    slot_key=item.slot_key,
                    mime_type=cast(
                        Literal["image/jpeg", "image/png", "image/webp"],
                        item.mime_type,
                    ),
                    size_bytes=item.size_bytes,
                    ticket=item.ticket,
                )
                for item in claim.media
            ],
        )
    )


@router.post(
    "/tasks/{task_id}/attempts/{attempt}/renew",
    response_model=EvaluationWorkerLeaseRenewalResponse,
)
def renew(
    task_id: UUID,
    attempt: int,
    request: Request,
    lease: Annotated[
        EvaluationWorkerLeaseHeaders,
        Depends(require_worker_lease_headers),
    ],
    service: Annotated[
        EvaluationProcessingService,
        Depends(get_evaluation_processing_service),
    ],
) -> EvaluationWorkerLeaseRenewalResponse | JSONResponse:
    record = _service_call(
        lambda: service.renew(
            task_id=task_id,
            attempt=attempt,
            task_version=lease.task_version,
            worker_identity=lease.worker_identity,
            lease_token=lease.lease_token,
            occurred_at=datetime.now(UTC),
            request_id=_audit_request_id(request),
        )
    )
    if isinstance(record, JSONResponse):
        return record
    renewal = record
    return EvaluationWorkerLeaseRenewalResponse(
        task_id=renewal.task_id,
        attempt=renewal.attempt,
        task_version=renewal.task_version,
        lease_expires_at=renewal.lease_expires_at,
    )


@router.get(
    "/tasks/{task_id}/attempts/{attempt}/media/{media_id}",
    response_class=FileResponse,
    response_model=None,
)
def read_media(
    task_id: UUID,
    attempt: int,
    media_id: UUID,
    lease: Annotated[
        EvaluationWorkerMediaHeaders,
        Depends(require_worker_media_headers),
    ],
    ticket: Annotated[str, Depends(require_worker_media_ticket)],
    service: Annotated[
        WorkerMediaTicketService,
        Depends(get_worker_media_ticket_service),
    ],
) -> FileResponse | JSONResponse:
    protected = _service_call(
        lambda: service.read_original(
            task_id=task_id,
            attempt=attempt,
            task_version=lease.task_version,
            media_id=media_id,
            worker_identity=lease.worker_identity,
            ticket=ticket,
            occurred_at=datetime.now(UTC),
        )
    )
    if isinstance(protected, JSONResponse):
        return protected
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


@router.post(
    "/tasks/{task_id}/attempts/{attempt}/results",
    response_model=EvaluationWorkerResultResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": SystemEvaluationResultInput.model_json_schema()
                }
            },
        }
    },
)
async def submit_result(
    task_id: UUID,
    attempt: int,
    request: Request,
    lease: Annotated[
        EvaluationWorkerLeaseHeaders,
        Depends(require_worker_lease_headers),
    ],
    service: Annotated[
        EvaluationProcessingService,
        Depends(get_evaluation_processing_service),
    ],
) -> EvaluationWorkerResultResponse | JSONResponse:
    occurred_at = datetime.now(UTC)
    lease_validation = _service_call(
        lambda: service.validate_result_lease(
            task_id=task_id,
            attempt=attempt,
            task_version=lease.task_version,
            worker_identity=lease.worker_identity,
            lease_token=lease.lease_token,
            occurred_at=occurred_at,
        )
    )
    if isinstance(lease_validation, JSONResponse):
        return lease_validation
    input = await _result_body(request)
    record = _service_call(
        lambda: service.submit_result(
            task_id=task_id,
            attempt=attempt,
            task_version=lease.task_version,
            worker_identity=lease.worker_identity,
            lease_token=lease.lease_token,
            input=input,
            occurred_at=occurred_at,
            request_id=_audit_request_id(
                request,
                body_values=_result_body_values(input),
            ),
        )
    )
    if isinstance(record, JSONResponse):
        return record
    result = record
    return EvaluationWorkerResultResponse(
        task_id=result.task_id,
        result_id=result.result_id,
        result_version=result.result_version,
        task_status="needs_review",
        task_version=result.task_version,
    )


@router.post(
    "/tasks/{task_id}/attempts/{attempt}/failures",
    response_model=EvaluationWorkerFailureResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": SystemEvaluationFailureInput.model_json_schema()
                }
            },
        }
    },
)
async def submit_failure(
    task_id: UUID,
    attempt: int,
    request: Request,
    lease: Annotated[
        EvaluationWorkerLeaseHeaders,
        Depends(require_worker_lease_headers),
    ],
    service: Annotated[
        EvaluationProcessingService,
        Depends(get_evaluation_processing_service),
    ],
) -> EvaluationWorkerFailureResponse | JSONResponse:
    occurred_at = datetime.now(UTC)
    lease_validation = _service_call(
        lambda: service.validate_failure_lease(
            task_id=task_id,
            attempt=attempt,
            task_version=lease.task_version,
            worker_identity=lease.worker_identity,
            lease_token=lease.lease_token,
            occurred_at=occurred_at,
        )
    )
    if isinstance(lease_validation, JSONResponse):
        return lease_validation
    input = await _failure_body(request)
    record = _service_call(
        lambda: service.submit_failure(
            task_id=task_id,
            attempt=attempt,
            task_version=lease.task_version,
            worker_identity=lease.worker_identity,
            lease_token=lease.lease_token,
            input=input,
            occurred_at=occurred_at,
            request_id=_audit_request_id(
                request,
                body_values=_failure_body_values(input),
            ),
        )
    )
    if isinstance(record, JSONResponse):
        return record
    failure = record
    return EvaluationWorkerFailureResponse(
        task_id=failure.task_id,
        attempt=failure.attempt,
        task_status=cast(Literal["queued", "manual_input"], failure.task_status),
        task_version=failure.task_version,
        retryable=failure.retryable,
        available_at=failure.available_at,
        failure_code=failure.failure_code,
    )
