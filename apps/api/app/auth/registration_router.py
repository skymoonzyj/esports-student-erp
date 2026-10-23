from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.auth.dependencies import get_registration_service
from app.auth.registration import (
    RegistrationCodeInvalid,
    RegistrationCodeTooSoon,
    RegistrationConflict,
    RegistrationDeliveryError,
    RegistrationError,
    RegistrationExpired,
    RegistrationNotFound,
    RegistrationService,
)
from app.auth.schemas import (
    RegistrationResendInput,
    RegistrationStartInput,
    RegistrationStartResponse,
    RegistrationVerifyInput,
    RegistrationVerifyResponse,
)
from app.core.errors import ProblemError
from app.identity.normalization import IdentityValidationError
from app.identity.service import IdentityServiceError
from app.security.passwords import PasswordPolicyError

router = APIRouter(prefix="/auth/registration", tags=["registration"])


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"


def _raise_registration_error(exc: Exception) -> None:
    if isinstance(exc, IdentityValidationError):
        raise ProblemError(
            status=422,
            code=exc.code,
            detail=str(exc),
            errors=[{"field": exc.field, "message": str(exc)}],
        ) from exc
    if isinstance(exc, PasswordPolicyError):
        raise ProblemError(
            status=422,
            code="password_policy_failed",
            detail="密码需为 8–16 位，并同时包含大写、小写字母和数字",
        ) from exc
    if isinstance(exc, (RegistrationConflict, IdentityServiceError)):
        raise ProblemError(status=409, code=exc.code, detail="该资料已经有注册记录") from exc
    if isinstance(exc, RegistrationNotFound):
        raise ProblemError(status=404, code=exc.code, detail="注册申请不存在") from exc
    if isinstance(exc, RegistrationExpired):
        raise ProblemError(status=410, code=exc.code, detail="注册验证码已失效，请重新申请") from exc
    if isinstance(exc, RegistrationCodeTooSoon):
        raise ProblemError(status=429, code=exc.code, detail="验证码发送过于频繁，请稍后再试") from exc
    if isinstance(exc, RegistrationCodeInvalid):
        raise ProblemError(status=422, code=exc.code, detail="验证码不正确") from exc
    if isinstance(exc, RegistrationDeliveryError):
        raise ProblemError(status=503, code=exc.code, detail="验证码邮件暂时发送失败，请稍后重试") from exc
    if isinstance(exc, RegistrationError):
        raise ProblemError(status=409, code=exc.code, detail="注册申请无法完成") from exc
    raise exc


@router.post("/start", response_model=RegistrationStartResponse, status_code=202)
def start_registration(
    input: RegistrationStartInput,
    response: Response,
    service: Annotated[RegistrationService, Depends(get_registration_service)],
) -> RegistrationStartResponse:
    try:
        result = service.start(
            full_name=input.full_name,
            identity_number=input.identity_number,
            sex=input.sex,
            date_of_birth=input.date_of_birth,
            phone=input.phone,
            email=str(input.email),
            password=input.password,
            person_type=input.person_type,
            staff_type=input.staff_type,
        )
    except (IdentityValidationError, PasswordPolicyError, RegistrationError, IdentityServiceError) as exc:
        _raise_registration_error(exc)
    _no_store(response)
    return RegistrationStartResponse(
        registration_id=result.registration_id,
        expires_at=result.expires_at,
        message="验证码已发送到邮箱，请在有效期内完成验证",
    )


@router.post("/resend", response_model=RegistrationStartResponse, status_code=202)
def resend_registration_code(
    input: RegistrationResendInput,
    response: Response,
    service: Annotated[RegistrationService, Depends(get_registration_service)],
) -> RegistrationStartResponse:
    try:
        result = service.resend(input.registration_id)
    except RegistrationError as exc:
        _raise_registration_error(exc)
    _no_store(response)
    return RegistrationStartResponse(
        registration_id=result.registration_id,
        expires_at=result.expires_at,
        message="新的验证码已发送",
    )


@router.post("/verify", response_model=RegistrationVerifyResponse)
def verify_registration(
    input: RegistrationVerifyInput,
    response: Response,
    service: Annotated[RegistrationService, Depends(get_registration_service)],
) -> RegistrationVerifyResponse:
    try:
        result = service.verify(registration_id=input.registration_id, code=input.code)
    except (RegistrationError, IdentityServiceError) as exc:
        _raise_registration_error(exc)
    _no_store(response)
    message = (
        "邮箱已验证。未成年学员需绑定有效监护人后才能登录。"
        if result.requires_guardian
        else "邮箱已验证，账号已创建，可以登录。"
    )
    return RegistrationVerifyResponse(
        registration_id=result.registration_id,
        account_status=result.account_status,
        requires_guardian=result.requires_guardian,
        message=message,
    )
