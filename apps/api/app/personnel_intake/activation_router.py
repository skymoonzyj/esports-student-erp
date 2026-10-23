from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.auth.dependencies import _secret_bytes
from app.core.client_ip import request_source_ip
from app.core.database import get_db
from app.core.errors import ProblemError
from app.personnel_intake.activation_schemas import (
    ActivationCompleted,
    ActivationCompleteInput,
    ActivationPreview,
    ActivationStart,
    ActivationStartInput,
    ActivationVerifyInput,
)
from app.personnel_intake.activation_security import require_activation_request
from app.personnel_intake.activation_service import AssistedActivationService
from app.registration.dependencies import (
    RegistrationSecretMaterial,
    get_registration_verification_service,
    require_registration_secret_material,
)
from app.registration.start_limiter import RegistrationStartRateLimited
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationRateLimited,
    VerificationServiceError,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordPolicyError

COOKIE_NAME = "erp_profile_activation"
COOKIE_PATH = "/api/v1/auth/profile-activation"
router = APIRouter(
    prefix="/auth/profile-activation",
    tags=["profile-activation"],
    dependencies=[Depends(require_activation_request)],
)


def get_activation_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    verification: Annotated[
        RegistrationVerificationService, Depends(get_registration_verification_service)
    ],
    material: Annotated[RegistrationSecretMaterial, Depends(require_registration_secret_material)],
) -> AssistedActivationService:
    settings = request.app.state.settings
    return AssistedActivationService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
        verification=verification,
        token_pepper=material.session_token_pepper,
    )


def _cookie(request: Request) -> str:
    values = []
    for header in request.headers.getlist("cookie"):
        for part in header.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == COOKIE_NAME:
                values.append(value)
    if len(values) != 1 or not values[0]:
        raise ProblemError(
            status=401, code="activation_session_invalid", detail="激活会话已失效，请重新申请"
        )
    return values[0]


def _problem(error: Exception) -> ProblemError:
    if isinstance(error, (RegistrationStartRateLimited, VerificationRateLimited)):
        return ProblemError(
            status=429, code="verification_rate_limited", detail="请求过于频繁，请稍后重试"
        )
    if isinstance(error, PasswordPolicyError):
        return ProblemError(
            status=422,
            code="password_policy_invalid",
            detail="密码须为 8–16 位，包含大写字母、小写字母和数字",
        )
    if getattr(error, "code", None) == "verification_busy":
        return ProblemError(
            status=409, code="activation_retry", detail="请求正在处理中，请稍后重试"
        )
    return ProblemError(
        status=400,
        code="verification_invalid_or_expired",
        detail="验证码或激活会话无效，请重新申请",
    )


@router.post("/start", response_model=ActivationStart)
def start(
    body: ActivationStartInput,
    request: Request,
    response: Response,
    service: Annotated[AssistedActivationService, Depends(get_activation_service)],
) -> ActivationStart:
    try:
        issued = service.start(
            body.email.get_secret_value(),
            request_source_ip(
                request, trusted_proxy_cidrs=request.app.state.settings.trusted_proxy_cidrs
            ),
        )
    except (RegistrationStartRateLimited, VerificationServiceError) as error:
        raise _problem(error) from error
    response.set_cookie(
        COOKIE_NAME,
        issued.session_token.get_secret_value(),
        max_age=max(
            0, min(600, int((issued.public.expires_at - datetime.now(UTC)).total_seconds()))
        ),
        secure=True,
        httponly=True,
        samesite="lax",
        path=COOKIE_PATH,
    )
    return issued.public


@router.post("/verify", response_model=ActivationPreview)
def verify(
    body: ActivationVerifyInput,
    request: Request,
    service: Annotated[AssistedActivationService, Depends(get_activation_service)],
) -> ActivationPreview:
    try:
        return service.verify(_cookie(request), body.code)
    except VerificationServiceError as error:
        raise _problem(error) from error


@router.post("/complete", response_model=ActivationCompleted)
def complete(
    body: ActivationCompleteInput,
    request: Request,
    response: Response,
    service: Annotated[AssistedActivationService, Depends(get_activation_service)],
) -> ActivationCompleted:
    try:
        result = service.finish(_cookie(request), body.code, body.password)
    except (VerificationServiceError, PasswordPolicyError) as error:
        raise _problem(error) from error
    response.delete_cookie(
        COOKIE_NAME, secure=True, httponly=True, samesite="lax", path=COOKIE_PATH
    )
    return result
