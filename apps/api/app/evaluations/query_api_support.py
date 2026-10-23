from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from fastapi.responses import JSONResponse

from app.core.errors import ProblemError
from app.evaluations.errors import EvaluationOperationDenied, EvaluationResourceNotFound
from app.evaluations.query_service import (
    EvaluationMatchTargetPage,
    EvaluationTaskPage,
    EvaluationTaskProjection,
    EvaluationTaskWorkspace,
)
from app.evaluations.task_schemas import (
    EvaluationMatchTargetItem,
    EvaluationMatchTargetPageResponse,
    EvaluationTaskDetailResponse,
    EvaluationTaskFilterOptionsResponse,
    EvaluationTaskPageResponse,
    EvaluationTaskResponse,
    EvaluationTaskWorkspaceResponse,
)


def _private_not_found() -> JSONResponse:
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


def _operation_denied(exc: EvaluationOperationDenied) -> NoReturn:
    raise ProblemError(
        status=403,
        code="evaluation_operation_denied",
        detail="没有执行此评价操作的权限",
    ) from exc


def task_page_response(
    call: Callable[[], EvaluationTaskPage],
    *,
    operational: bool,
) -> EvaluationTaskPageResponse | JSONResponse:
    try:
        page = call()
    except EvaluationResourceNotFound:
        return _private_not_found()
    except EvaluationOperationDenied as exc:
        if operational:
            _operation_denied(exc)
        return _private_not_found()
    except ValueError as exc:
        raise ProblemError(
            status=422,
            code="evaluation_request_invalid",
            detail="评价请求无效",
        ) from exc
    return EvaluationTaskPageResponse(
        items=[
            EvaluationTaskResponse.model_validate(item, from_attributes=True) for item in page.items
        ],
        total=page.total,
        page=page.page,
        page_size=page.page_size,
    )


def task_detail_response(
    call: Callable[[], EvaluationTaskProjection],
    *,
    operational: bool,
) -> EvaluationTaskDetailResponse | JSONResponse:
    try:
        task = call()
    except EvaluationResourceNotFound:
        return _private_not_found()
    except EvaluationOperationDenied as exc:
        if operational:
            _operation_denied(exc)
        return _private_not_found()
    return EvaluationTaskDetailResponse(
        task=EvaluationTaskResponse.model_validate(task, from_attributes=True)
    )


def task_workspace_response(
    call: Callable[[], EvaluationTaskWorkspace],
    *,
    operational: bool,
) -> EvaluationTaskWorkspaceResponse | JSONResponse:
    try:
        workspace = call()
    except EvaluationResourceNotFound:
        return _private_not_found()
    except EvaluationOperationDenied as exc:
        if operational:
            _operation_denied(exc)
        return _private_not_found()
    return EvaluationTaskWorkspaceResponse.model_validate(
        workspace,
        from_attributes=True,
    )


def task_filter_options_response(
    call: Callable[[], object],
    *,
    operational: bool,
) -> EvaluationTaskFilterOptionsResponse | JSONResponse:
    try:
        options = call()
    except EvaluationResourceNotFound:
        return _private_not_found()
    except EvaluationOperationDenied as exc:
        if operational:
            _operation_denied(exc)
        return _private_not_found()
    return EvaluationTaskFilterOptionsResponse.model_validate(
        options,
        from_attributes=True,
    )


def match_target_page_response(
    call: Callable[[], EvaluationMatchTargetPage],
) -> EvaluationMatchTargetPageResponse | JSONResponse:
    try:
        page = call()
    except EvaluationResourceNotFound:
        return _private_not_found()
    except EvaluationOperationDenied as exc:
        _operation_denied(exc)
    except ValueError as exc:
        raise ProblemError(
            status=422,
            code="evaluation_request_invalid",
            detail="评价请求无效",
        ) from exc
    return EvaluationMatchTargetPageResponse(
        items=[
            EvaluationMatchTargetItem.model_validate(item, from_attributes=True)
            for item in page.items
        ],
        total=page.total,
        page=page.page,
        page_size=page.page_size,
    )
