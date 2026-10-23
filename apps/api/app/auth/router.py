from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.auth.dependencies import (
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_PATH,
    expire_legacy_session_cookie,
    get_auth_service,
    require_session,
    set_api_session_cookie,
)
from app.auth.schemas import (
    AuthUser,
    ChangePasswordRequest,
    CsrfResponse,
    LoginRequest,
    LoginResponse,
)
from app.auth.service import (
    AuthContext,
    AuthService,
    InvalidCredentials,
)
from app.core.errors import ProblemError
from app.security.passwords import PasswordPolicyError

router = APIRouter(prefix="/auth", tags=["authentication"])


def _prevent_storage(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"


def _client_ip(request: Request) -> str | None:
    return request.headers.get("X-Real-IP") or (request.client.host if request.client else None)


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    expire_legacy_session_cookie(response, request)
    set_api_session_cookie(response, request, token)


@router.post("/login", response_model=LoginResponse)
def login(
    input: LoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> LoginResponse:
    try:
        result = service.login(
            identifier=input.identifier,
            password=input.password,
            ip_address=_client_ip(request),
            request_id=str(request.state.request_id),
        )
    except InvalidCredentials as exc:
        raise ProblemError(
            status=401,
            code="invalid_credentials",
            detail="账号或密码错误",
        ) from exc
    _set_session_cookie(response, request, result.session_token)
    _prevent_storage(response)
    return LoginResponse(
        user=service.user(result.context),
        csrf_token=result.csrf_token,
    )


@router.get("/me", response_model=AuthUser)
def me(
    response: Response,
    context: Annotated[AuthContext, Depends(require_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthUser:
    _prevent_storage(response)
    return service.user(context)


@router.get("/csrf", response_model=CsrfResponse)
def csrf(
    response: Response,
    context: Annotated[AuthContext, Depends(require_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> CsrfResponse:
    _prevent_storage(response)
    return CsrfResponse(csrf_token=service.rotate_csrf(context))


@router.post("/change-password", response_model=LoginResponse)
def change_password(
    input: ChangePasswordRequest,
    response: Response,
    context: Annotated[AuthContext, Depends(require_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> LoginResponse:
    try:
        csrf_token = service.change_password(
            context,
            current_password=input.current_password,
            new_password=input.new_password,
        )
    except InvalidCredentials as exc:
        raise ProblemError(
            status=401,
            code="invalid_credentials",
            detail="当前密码错误",
        ) from exc
    except PasswordPolicyError as exc:
        raise ProblemError(
            status=422,
            code="password_policy_failed",
            detail="新密码需为 8–16 位，并同时包含大写、小写字母和数字",
        ) from exc
    _prevent_storage(response)
    return LoginResponse(user=service.user(context), csrf_token=csrf_token)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    context: Annotated[AuthContext, Depends(require_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    service.logout(context)
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path=SESSION_COOKIE_PATH,
        secure=request.app.state.settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    expire_legacy_session_cookie(response, request)
    _prevent_storage(response)
    return response
