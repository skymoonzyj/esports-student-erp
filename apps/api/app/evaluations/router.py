from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.evaluations.bridge_service import EvaluationBridgeService
from app.evaluations.config import EvaluationBridgeConfig
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.dependencies import (
    get_evaluation_bridge_service,
    get_evaluation_context_service,
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
from app.evaluations.portal_router import require_evaluation_center
from app.evaluations.schemas import (
    EvaluationCenterContextResponse,
    EvaluationTicketCreate,
    EvaluationTicketIssued,
)
from app.rbac.service import RbacService

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


def _require_evaluation_access(db: Session, account_id: UUID) -> None:
    allowed = (
        RbacService(db)
        .decision_context(account_id)
        .decide("evaluation_center.access", {"global"})
        .allowed
    )
    if not allowed:
        raise ProblemError(
            status=403,
            code="evaluation_access_denied",
            detail="没有进入评价中心的权限",
        )


def _raise_evaluation_error(exc: EvaluationBridgeError) -> NoReturn:
    if isinstance(exc, EvaluationBridgeRequestNotFound):
        error = (404, "bridge_request_not_found", "连接请求不存在")
    elif isinstance(exc, EvaluationBridgeRequestExpired):
        error = (410, "bridge_request_expired", "连接请求已过期，请重新打开评价页面")
    elif isinstance(exc, EvaluationBridgeRequestConflict):
        error = (409, "bridge_request_conflict", "连接请求已被处理，请重新打开评价页面")
    elif isinstance(exc, EvaluationAccessDenied):
        error = (403, "evaluation_access_denied", "没有进入评价中心的权限")
    elif isinstance(exc, EvaluationDelegatedSessionInvalid):
        error = (401, "delegated_session_invalid", "登录已失效，请重新登录")
    elif isinstance(exc, EvaluationTicketInvalid):
        error = (401, "bridge_ticket_invalid", "连接票据无效，请重新连接")
    else:
        error = (500, "evaluation_bridge_error", "评价连接暂时不可用")
    raise ProblemError(status=error[0], code=error[1], detail=error[2]) from exc


@router.get("/context", response_model=EvaluationCenterContextResponse)
def get_evaluation_context(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
) -> EvaluationCenterContextResponse:
    require_evaluation_center(context=context, service=context_service)
    capabilities = context_service.capabilities(
        account_id=context.account.id,
        occurred_at=datetime.now(UTC),
    )
    config = EvaluationBridgeConfig.from_settings(request.app.state.settings)
    return EvaluationCenterContextResponse(
        embedded_start_url=config.start_url("embedded"),
        standalone_start_url=config.start_url("standalone"),
        game_accounts_available=bool(capabilities.targets),
        admin_available=capabilities.admin_available,
        admin_sections=capabilities.admin_sections,
    )


@router.post(
    "/bridge/tickets",
    response_model=EvaluationTicketIssued,
    status_code=201,
)
def issue_evaluation_ticket(
    input: EvaluationTicketCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        EvaluationBridgeService,
        Depends(get_evaluation_bridge_service),
    ],
) -> EvaluationTicketIssued:
    _require_evaluation_access(db, context.account.id)
    try:
        return service.issue_ticket(
            request_id=input.request_id,
            context=context,
            request_id_for_audit=str(request.state.request_id),
        )
    except EvaluationBridgeError as exc:
        _raise_evaluation_error(exc)
