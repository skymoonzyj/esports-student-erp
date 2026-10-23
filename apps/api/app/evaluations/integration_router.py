from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Request

from app.core.errors import ProblemError
from app.evaluations.bridge_service import EvaluationBridgeService
from app.evaluations.dependencies import (
    get_evaluation_bridge_service,
    require_evaluation_tool_service,
)
from app.evaluations.errors import (
    EvaluationAccessDenied,
    EvaluationBridgeError,
    EvaluationBridgeRequestConflict,
    EvaluationBridgeRequestExpired,
    EvaluationBridgeRequestNotFound,
    EvaluationDelegatedSessionInvalid,
    EvaluationTicketInvalid,
)
from app.evaluations.schemas import (
    EvaluationDelegatedSessionRequest,
    EvaluationIntegrationCapabilities,
    EvaluationLoginRequestCreate,
    EvaluationLoginRequestCreated,
    EvaluationTicketExchange,
    EvaluationToolSessionContext,
)

router = APIRouter(
    prefix="/integrations/evaluations/v1",
    tags=["evaluation-integration"],
    dependencies=[Depends(require_evaluation_tool_service)],
)


def _raise_integration_error(exc: EvaluationBridgeError) -> NoReturn:
    if isinstance(exc, EvaluationTicketInvalid):
        error = (401, "bridge_ticket_invalid", "连接票据无效，请重新连接")
    elif isinstance(exc, EvaluationDelegatedSessionInvalid):
        error = (401, "delegated_session_invalid", "ERP 登录已失效，请重新连接")
    elif isinstance(exc, EvaluationAccessDenied):
        error = (403, "evaluation_access_denied", "评价中心权限已失效")
    elif isinstance(exc, EvaluationBridgeRequestNotFound):
        error = (404, "bridge_request_not_found", "连接请求不存在")
    elif isinstance(exc, EvaluationBridgeRequestExpired):
        error = (410, "bridge_request_expired", "连接请求已过期")
    elif isinstance(exc, EvaluationBridgeRequestConflict):
        error = (409, "bridge_request_conflict", "连接请求状态冲突")
    else:
        error = (500, "evaluation_bridge_error", "评价连接暂时不可用")
    raise ProblemError(status=error[0], code=error[1], detail=error[2]) from exc


@router.get(
    "/capabilities",
    response_model=EvaluationIntegrationCapabilities,
)
def capabilities() -> EvaluationIntegrationCapabilities:
    return EvaluationIntegrationCapabilities(
        features=["auth_bridge", "page_shell"]
    )


@router.post(
    "/login-requests",
    response_model=EvaluationLoginRequestCreated,
    status_code=201,
)
def create_login_request(
    input: EvaluationLoginRequestCreate,
    service: Annotated[
        EvaluationBridgeService,
        Depends(get_evaluation_bridge_service),
    ],
) -> EvaluationLoginRequestCreated:
    return service.create_login_request(input)


@router.post(
    "/login-tickets/exchange",
    response_model=EvaluationToolSessionContext,
)
def exchange_login_ticket(
    input: EvaluationTicketExchange,
    request: Request,
    service: Annotated[
        EvaluationBridgeService,
        Depends(get_evaluation_bridge_service),
    ],
) -> EvaluationToolSessionContext:
    try:
        return service.exchange_ticket(
            input.ticket,
            request_id_for_audit=str(request.state.request_id),
        )
    except EvaluationBridgeError as exc:
        _raise_integration_error(exc)


@router.post(
    "/session-context",
    response_model=EvaluationToolSessionContext,
)
def delegated_session_context(
    input: EvaluationDelegatedSessionRequest,
    service: Annotated[
        EvaluationBridgeService,
        Depends(get_evaluation_bridge_service),
    ],
) -> EvaluationToolSessionContext:
    try:
        return service.session_context(input.session_id)
    except EvaluationBridgeError as exc:
        _raise_integration_error(exc)
