from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, literal, select
from starlette.background import BackgroundTask

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.evaluations.access import EvaluationResource, EvaluationSqlResource
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.dependencies import (
    get_evaluation_context_service,
    get_evaluation_media_service,
    get_game_account_service,
)
from app.evaluations.errors import (
    EvaluationGameAccountError,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    GameAccountAlreadyUnbound,
    GameAccountLimitReached,
    GameIdAlreadyBound,
    MediaAssetNotFound,
)
from app.evaluations.game_account_service import GameAccountService
from app.evaluations.game_models import StudentGameAccount
from app.evaluations.media_service import EvaluationMediaService, ProtectedMediaFile
from app.evaluations.schemas import (
    EvaluationProjectDirectoryItem,
    EvaluationProjectDirectoryPage,
    GameAccountBind,
    GameAccountTarget,
    GameAccountUnbind,
    StudentGameAccountItem,
)
from app.teaching.models import GameProject

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


def require_evaluation_center(
    *,
    context: AuthContext,
    service: EvaluationContextService,
) -> None:
    if not service.center_allowed(context.account.id):
        raise ProblemError(
            status=403,
            code="evaluation_access_denied",
            detail="没有进入评价中心的权限",
        )


def _resource_not_found(exc: Exception | None = None) -> NoReturn:
    raise ProblemError(
        status=404,
        code="evaluation_resource_not_found",
        detail="评价资源不存在",
    ) from exc


def _media_not_found(exc: MediaAssetNotFound) -> NoReturn:
    raise ProblemError(
        status=404,
        code="media_asset_not_found",
        detail="媒体资源不存在",
    ) from exc


def _raise_game_error(
    exc: EvaluationGameAccountError,
    *,
    mutation_target_visible: bool = False,
) -> NoReturn:
    if isinstance(exc, EvaluationResourceNotFound):
        _resource_not_found(exc)
    if isinstance(exc, EvaluationOperationDenied):
        if mutation_target_visible:
            raise ProblemError(
                status=403,
                code="evaluation_operation_denied",
                detail="没有执行此评价操作的权限",
            ) from exc
        _resource_not_found(exc)
    if isinstance(exc, GameAccountLimitReached):
        error = (409, "game_account_limit_reached", "游戏账号数量已达上限")
    elif isinstance(exc, GameIdAlreadyBound):
        error = (409, "game_id_already_bound", "游戏账号已被绑定")
    elif isinstance(exc, GameAccountAlreadyUnbound):
        error = (409, "game_account_already_unbound", "游戏账号已经解绑")
    else:
        error = (422, exc.code, "游戏账号请求无效")
    raise ProblemError(status=error[0], code=error[1], detail=error[2]) from exc


def _account_item(
    service: GameAccountService,
    *,
    student_id: UUID,
    account_id: UUID,
    actor_user_id: UUID,
    occurred_at: datetime,
) -> StudentGameAccountItem:
    row = service.db.execute(
        select(StudentGameAccount, GameProject)
        .join(GameProject, GameProject.id == StudentGameAccount.project_id)
        .where(
            StudentGameAccount.id == account_id,
            StudentGameAccount.student_person_id == student_id,
        )
    ).one_or_none()
    if row is None:
        _resource_not_found()
    account, project = row
    resource = EvaluationResource(
        student_person_id=student_id,
        project_id=account.project_id,
    )
    if not all(
        service.access.decide(
            account_id=actor_user_id,
            permission=permission,
            resource=resource,
            occurred_at=occurred_at,
        ).allowed
        for permission in ("evaluation_center.access", "student_game_accounts.manage")
    ):
        _resource_not_found()
    return StudentGameAccountItem(
        id=account.id,
        project_id=project.id,
        project_name=project.name,
        game_id=service.cipher.decrypt(
            account.identifier_ciphertext,
            field_name="student_game_id",
        ),
        status="unbound" if account.unbound_at is not None else "active",
        valid_from=account.valid_from,
        unbound_at=account.unbound_at,
        unbind_reason=account.unbind_reason,
    )


@router.get("/game-account-targets", response_model=list[GameAccountTarget])
def list_game_account_targets(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
) -> list[GameAccountTarget]:
    require_evaluation_center(context=context, service=context_service)
    return context_service.game_account_targets(
        account_id=context.account.id,
        occurred_at=datetime.now(UTC),
    )


@router.get(
    "/students/{student_id}/game-account-projects",
    response_model=EvaluationProjectDirectoryPage,
)
def list_game_account_projects(
    student_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 100,
) -> EvaluationProjectDirectoryPage:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    if student_id not in {
        target.student_person_id
        for target in context_service.game_account_targets(
            account_id=context.account.id,
            occurred_at=occurred_at,
        )
    }:
        _resource_not_found()
    authorization = context_service.access.center_scoped_sql_context(
        account_id=context.account.id,
        permission="student_game_accounts.manage",
        occurred_at=occurred_at,
    )
    statement = select(GameProject).where(
        GameProject.is_active.is_(True),
        authorization.allowed(
            EvaluationSqlResource(
                student_person_id=literal(student_id),
                project_id=GameProject.id,
            )
        ),
    )
    total = context_service.db.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    projects = context_service.db.scalars(
        statement.order_by(GameProject.sort_order, GameProject.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return EvaluationProjectDirectoryPage(
        items=[
            EvaluationProjectDirectoryItem(
                id=project.id,
                name=project.name,
                is_active=project.is_active,
            )
            for project in projects
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/students/{student_id}/game-accounts",
    response_model=list[StudentGameAccountItem],
)
def list_game_accounts(
    student_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[GameAccountService, Depends(get_game_account_service)],
    include_unbound: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[StudentGameAccountItem]:
    require_evaluation_center(context=context, service=context_service)
    try:
        return service.list_game_accounts(
            student_id,
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
            include_unbound=include_unbound,
            page=page,
            page_size=page_size,
        )
    except EvaluationGameAccountError as exc:
        _raise_game_error(exc)


@router.post(
    "/students/{student_id}/game-accounts",
    response_model=StudentGameAccountItem,
    status_code=201,
)
def bind_game_account(
    student_id: UUID,
    input: GameAccountBind,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[GameAccountService, Depends(get_game_account_service)],
) -> StudentGameAccountItem:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    visible = context_service.target_is_visible(
        account_id=context.account.id,
        student_person_id=student_id,
        occurred_at=occurred_at,
    )
    try:
        account = service.bind_game_account(
            student_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            request_id=str(request.state.request_id),
        )
        return _account_item(
            service,
            student_id=student_id,
            account_id=account.id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
        )
    except EvaluationGameAccountError as exc:
        _raise_game_error(exc, mutation_target_visible=visible)


@router.post(
    "/students/{student_id}/game-accounts/{account_id}/unbind",
    response_model=StudentGameAccountItem,
)
def unbind_game_account(
    student_id: UUID,
    account_id: UUID,
    input: GameAccountUnbind,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[GameAccountService, Depends(get_game_account_service)],
) -> StudentGameAccountItem:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    visible = context_service.target_is_visible(
        account_id=context.account.id,
        student_person_id=student_id,
        occurred_at=occurred_at,
    )
    try:
        account = service.unbind_game_account(
            student_id,
            account_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            request_id=str(request.state.request_id),
        )
        return _account_item(
            service,
            student_id=student_id,
            account_id=account.id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
        )
    except EvaluationGameAccountError as exc:
        _raise_game_error(exc, mutation_target_visible=visible)


def _media_response(protected: ProtectedMediaFile) -> FileResponse:
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


@router.get("/media-assets/{media_id}/preview", response_class=FileResponse)
def read_media_preview(
    media_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[EvaluationMediaService, Depends(get_evaluation_media_service)],
) -> FileResponse:
    require_evaluation_center(context=context, service=context_service)
    try:
        protected = service.read_preview(
            media_id,
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
        )
    except MediaAssetNotFound as exc:
        _media_not_found(exc)
    return _media_response(protected)


@router.get("/media-assets/{media_id}/original", response_class=FileResponse)
def read_media_original(
    media_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[EvaluationMediaService, Depends(get_evaluation_media_service)],
) -> FileResponse:
    request_id = str(request.state.request_id)
    try:
        require_evaluation_center(context=context, service=context_service)
    except ProblemError:
        service.audit_original_denied(
            media_id,
            actor_user_id=context.account.id,
            request_id=request_id,
        )
        raise
    try:
        protected = service.read_original(
            media_id,
            actor_user_id=context.account.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
    except MediaAssetNotFound as exc:
        _media_not_found(exc)
    return _media_response(protected)
