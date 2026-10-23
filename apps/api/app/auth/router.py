from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.auth.dependencies import (
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_PATH,
    expire_legacy_session_cookie,
    get_auth_service,
    require_session,
    require_standard_account,
    set_api_session_cookie,
)
from app.auth.identifiers import normalize_login_identifier
from app.auth.schemas import (
    AccountIdentifierChallengeRequest,
    AccountIdentifiersResponse,
    AccountIdentifierVerifyRequest,
    AuthUser,
    ChangePasswordRequest,
    CsrfResponse,
    LoginRequest,
    LoginResponse,
    PasswordResetChallengeRequest,
    PasswordResetCompleteRequest,
    PasswordResetCompleteResponse,
    VerificationChallengeResponse,
)
from app.auth.service import (
    AccountIdentifierUnavailable,
    AccountTemporarilyLocked,
    AuthContext,
    AuthService,
    InvalidCredentials,
    LoginRateLimited,
)
from app.core.client_ip import request_source_ip
from app.core.errors import ProblemError
from app.identity.normalization import IdentityValidationError
from app.registration.dependencies import get_registration_verification_service
from app.registration.identifiers import normalize_registration_identifier
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationInvalidOrExpired,
    VerificationRateLimited,
    VerificationTargetMismatch,
)
from app.security.passwords import PasswordPolicyError

router = APIRouter(prefix="/auth", tags=["authentication"])


def _prevent_storage(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"


def _client_ip(request: Request) -> str:
    return request_source_ip(
        request,
        trusted_proxy_cidrs=request.app.state.settings.trusted_proxy_cidrs,
    )


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _mask_identifier(kind: str, value: str) -> str:
    if kind == "phone":
        return f"{value[:3]}****{value[-4:]}"
    local, domain = value.split("@", 1)
    return f"{local[:1]}***@{domain}"


def _action_unavailable() -> None:
    raise ProblemError(
        status=404,
        code="registration_action_unavailable",
        detail="账号操作不可用，请重新登录或稍后重试",
    )


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
    except AccountTemporarilyLocked as exc:
        raise ProblemError(
            status=423,
            code="account_locked",
            detail=(
                "账号因连续多次登录失败已临时锁定，请稍后重试，"
                "或使用“忘记密码”重置密码"
            ),
            retry_after_seconds=exc.retry_after_seconds,
        ) from exc
    except LoginRateLimited as exc:
        raise ProblemError(
            status=429,
            code="login_rate_limited",
            detail="登录尝试过于频繁，请稍后重试",
            retry_after_seconds=exc.retry_after_seconds,
        ) from exc
    except InvalidCredentials as exc:
        raise ProblemError(
            status=401,
            code="invalid_credentials",
            detail="手机号、邮箱或密码错误",
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


@router.post(
    "/password-reset/challenges",
    response_model=VerificationChallengeResponse,
    status_code=201,
)
def issue_password_reset_challenge(
    input: PasswordResetChallengeRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    verification: Annotated[
        RegistrationVerificationService,
        Depends(get_registration_verification_service),
    ],
) -> VerificationChallengeResponse:
    try:
        normalized = normalize_login_identifier(input.identifier)
        issued = service.issue_password_reset(
            identifier=input.identifier,
            request_source=_client_ip(request),
            verification=verification,
        )
    except IdentityValidationError:
        _action_unavailable()
    except VerificationRateLimited as exc:
        raise ProblemError(
            status=429,
            code=exc.code,
            detail="验证码请求过于频繁，请稍后重试",
        ) from exc
    _prevent_storage(response)
    return VerificationChallengeResponse(
        challenge_id=issued.challenge_id,
        expires_at=issued.expires_at,
        retry_after_seconds=60,
        masked_destination=_mask_identifier(normalized.kind, normalized.value),
        request_id=_request_id(request),
    )


@router.post(
    "/password-reset/complete",
    response_model=PasswordResetCompleteResponse,
)
def complete_password_reset(
    input: PasswordResetCompleteRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    verification: Annotated[
        RegistrationVerificationService,
        Depends(get_registration_verification_service),
    ],
) -> PasswordResetCompleteResponse:
    try:
        service.complete_password_reset(
            challenge_id=input.challenge_id,
            code=input.code,
            new_password=input.new_password.get_secret_value(),
            request_id=_request_id(request),
            verification=verification,
        )
    except (VerificationInvalidOrExpired, VerificationTargetMismatch) as exc:
        raise ProblemError(
            status=422,
            code="verification_invalid_or_expired",
            detail="验证码无效或已过期",
        ) from exc
    except PasswordPolicyError as exc:
        raise ProblemError(
            status=422,
            code="password_policy_failed",
            detail="新密码需为 8–16 位，并同时包含大写、小写字母和数字",
        ) from exc
    _prevent_storage(response)
    return PasswordResetCompleteResponse(request_id=_request_id(request))


@router.get("/identifiers", response_model=AccountIdentifiersResponse)
def list_account_identifiers(
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AccountIdentifiersResponse:
    try:
        items = service.list_identifiers(context)
    except AccountIdentifierUnavailable:
        _action_unavailable()
    _prevent_storage(response)
    return AccountIdentifiersResponse(items=items, request_id=_request_id(request))


@router.post(
    "/identifiers/challenges",
    response_model=VerificationChallengeResponse,
    status_code=201,
)
def issue_account_identifier_challenge(
    input: AccountIdentifierChallengeRequest,
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    verification: Annotated[
        RegistrationVerificationService,
        Depends(get_registration_verification_service),
    ],
) -> VerificationChallengeResponse:
    try:
        normalized = normalize_registration_identifier(
            input.identifier_kind,
            input.identifier,
        )
        issued = service.issue_identifier_addition(
            context,
            identifier_kind=input.identifier_kind,
            identifier=input.identifier,
            current_password=input.current_password.get_secret_value(),
            request_source=_client_ip(request),
            verification=verification,
        )
    except InvalidCredentials as exc:
        raise ProblemError(
            status=401,
            code="invalid_credentials",
            detail="当前密码错误",
        ) from exc
    except VerificationRateLimited as exc:
        raise ProblemError(
            status=429,
            code=exc.code,
            detail="验证码请求过于频繁，请稍后重试",
        ) from exc
    except (AccountIdentifierUnavailable, IdentityValidationError, ValueError):
        _action_unavailable()
    _prevent_storage(response)
    return VerificationChallengeResponse(
        challenge_id=issued.challenge_id,
        expires_at=issued.expires_at,
        retry_after_seconds=60,
        masked_destination=_mask_identifier(normalized.kind, normalized.value),
        request_id=_request_id(request),
    )


@router.post("/identifiers/verify", response_model=AccountIdentifiersResponse)
def verify_account_identifier(
    input: AccountIdentifierVerifyRequest,
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    verification: Annotated[
        RegistrationVerificationService,
        Depends(get_registration_verification_service),
    ],
) -> AccountIdentifiersResponse:
    try:
        items = service.verify_identifier_addition(
            context,
            challenge_id=input.challenge_id,
            code=input.code,
            identifier_kind=input.identifier_kind,
            identifier=input.identifier,
            current_password=input.current_password.get_secret_value(),
            request_id=_request_id(request),
            verification=verification,
        )
    except InvalidCredentials as exc:
        raise ProblemError(
            status=401,
            code="invalid_credentials",
            detail="当前密码错误",
        ) from exc
    except VerificationInvalidOrExpired as exc:
        raise ProblemError(
            status=422,
            code=exc.code,
            detail="验证码无效或已过期",
        ) from exc
    except (
        AccountIdentifierUnavailable,
        IdentityValidationError,
        ValueError,
        VerificationTargetMismatch,
    ):
        _action_unavailable()
    _prevent_storage(response)
    return AccountIdentifiersResponse(items=items, request_id=_request_id(request))
