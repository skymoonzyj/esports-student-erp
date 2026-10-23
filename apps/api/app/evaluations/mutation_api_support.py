from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.core.errors import ProblemError
from app.evaluations.errors import (
    EvaluationAdapterCertificationConflict,
    EvaluationAdapterNotSupported,
    EvaluationIdempotencyConflict,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    EvaluationResultInvalid,
    EvaluationSelfReviewForbidden,
    EvaluationTaskStateConflict,
    EvaluationTaskVersionConflict,
    TeachingReportDraftVersionConflict,
    TeachingReportStateConflict,
    TeachingReportWindowClosed,
)
from app.evaluations.query_service import EvaluationQueryService


def private_not_found() -> JSONResponse:
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


def idempotency_key(request: Request) -> str:
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


def new_audit_request_id() -> str:
    # Mutation bodies contain protected free text. Substring inspection cannot prove
    # a caller-provided correlation ID is unrelated, so audits always get a server ID.
    return f"evaluation-mutation-{uuid4()}"


def json_request_body_openapi(model: type[BaseModel]) -> dict[str, object]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": model.model_json_schema(),
                }
            },
        }
    }


def idempotent_json_request_openapi(model: type[BaseModel]) -> dict[str, object]:
    contract = json_request_body_openapi(model)
    contract["parameters"] = [
        {
            "name": "Idempotency-Key",
            "in": "header",
            "required": True,
            "schema": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "pattern": r"^[^\s,]+$",
            },
        },
        {
            "name": "X-CSRF-Token",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "minLength": 1},
        },
    ]
    return contract


async def parse_input[InputModel: BaseModel](
    request: Request,
    model: type[InputModel],
) -> InputModel:
    try:
        return model.model_validate_json(await request.body())
    except (ValidationError, ValueError, TypeError):
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="请求数据校验失败",
        ) from None


def preauthorize_task(
    service: EvaluationQueryService,
    task_id: UUID,
    *,
    actor_user_id: UUID,
    permission: str,
    occurred_at: datetime,
) -> JSONResponse | None:
    try:
        service.require_action_task(
            task_id,
            actor_user_id=actor_user_id,
            permission=permission,
            occurred_at=occurred_at,
            for_update=False,
        )
    except EvaluationOperationDenied as exc:
        raise ProblemError(
            status=403,
            code="evaluation_operation_denied",
            detail="没有执行此评价操作的权限",
        ) from exc
    except EvaluationResourceNotFound:
        return private_not_found()
    return None


def mutation_call[Result](call: Callable[[], Result]) -> Result | JSONResponse:
    try:
        return call()
    except (EvaluationResourceNotFound, EvaluationOperationDenied):
        return private_not_found()
    except EvaluationIdempotencyConflict as exc:
        raise ProblemError(
            status=409,
            code="evaluation_idempotency_conflict",
            detail="幂等请求与原请求不一致",
        ) from exc
    except TeachingReportDraftVersionConflict as exc:
        raise ProblemError(
            status=409,
            code="evaluation_stale_version",
            detail="评语草稿版本已变化，请刷新后重试",
        ) from exc
    except TeachingReportStateConflict as exc:
        raise ProblemError(
            status=409,
            code="evaluation_invalid_transition",
            detail="评语状态已变化，请刷新后重试",
        ) from exc
    except TeachingReportWindowClosed as exc:
        raise ProblemError(
            status=409,
            code="evaluation_report_window_closed",
            detail="评语提交窗口已关闭",
        ) from exc
    except EvaluationTaskVersionConflict as exc:
        raise ProblemError(
            status=409,
            code="evaluation_stale_version",
            detail="任务版本已变化，请刷新后重试",
        ) from exc
    except EvaluationTaskStateConflict as exc:
        raise ProblemError(
            status=409,
            code="evaluation_invalid_transition",
            detail="任务状态已变化，请刷新后重试",
        ) from exc
    except EvaluationSelfReviewForbidden as exc:
        raise ProblemError(
            status=409,
            code="evaluation_review_conflict",
            detail="不能审核自己创建的评价",
        ) from exc
    except EvaluationAdapterCertificationConflict as exc:
        raise ProblemError(
            status=409,
            code="evaluation_adapter_certification_conflict",
            detail="适配器认证记录冲突",
        ) from exc
    except EvaluationAdapterNotSupported as exc:
        raise ProblemError(
            status=422,
            code="evaluation_adapter_not_supported",
            detail="评价适配器版本不受支持",
        ) from exc
    except EvaluationResultInvalid as exc:
        raise ProblemError(
            status=422,
            code="evaluation_result_invalid",
            detail="评价结果无效",
        ) from exc
