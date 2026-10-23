from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request, Response

from app.core.client_ip import request_source_ip
from app.core.errors import ProblemError
from app.personnel_intake.activation_security import require_activation_request
from app.personnel_intake.claims.dependencies import (
    COOKIE_NAME,
    COOKIE_PATH,
    get_profile_claim_service,
    require_claim_cookie,
)
from app.personnel_intake.claims.public_service import ProfileClaimService
from app.personnel_intake.claims.schemas import (
    ClaimPublicStatus,
    ClaimStart,
    ClaimStartInput,
    ClaimVerifyInput,
)
from app.registration.start_limiter import RegistrationStartRateLimited
from app.registration.verification_service import VerificationRateLimited, VerificationServiceError

router = APIRouter(
    prefix="/auth/profile-claims",
    tags=["profile-claims"],
    dependencies=[Depends(require_activation_request)],
)


def _problem(error: Exception) -> ProblemError:
    if isinstance(error, (RegistrationStartRateLimited, VerificationRateLimited)):
        return ProblemError(
            status=429,
            code="verification_rate_limited",
            detail="请求过于频繁，请稍后重试",
            retry_after_seconds=60,
        )
    if getattr(error, "code", None) == "verification_busy":
        return ProblemError(
            status=409, code="profile_claim_retry", detail="请求正在处理中，请稍后重试"
        )
    return ProblemError(
        status=400,
        code="verification_invalid_or_expired",
        detail="验证码或认领会话无效，请重新申请",
    )


@router.post("/start", response_model=ClaimStart)
def start(
    body: ClaimStartInput,
    request: Request,
    response: Response,
    service: Annotated[ProfileClaimService, Depends(get_profile_claim_service)],
) -> ClaimStart:
    try:
        issued = service.start(
            body,
            request_source_ip(
                request, trusted_proxy_cidrs=request.app.state.settings.trusted_proxy_cidrs
            ),
        )
    except (RegistrationStartRateLimited, VerificationServiceError) as error:
        raise _problem(error) from error
    response.set_cookie(
        COOKIE_NAME,
        issued.session_token.get_secret_value(),
        path=COOKIE_PATH,
        max_age=max(
            0, min(600, int((issued.public.expires_at - datetime.now(UTC)).total_seconds()))
        ),
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return issued.public


@router.post("/verify", response_model=ClaimPublicStatus)
def verify(
    body: ClaimVerifyInput,
    token: Annotated[str, Depends(require_claim_cookie)],
    service: Annotated[ProfileClaimService, Depends(get_profile_claim_service)],
) -> ClaimPublicStatus:
    try:
        return service.verify(token, body.code)
    except VerificationServiceError as error:
        raise _problem(error) from error


@router.post("/status", response_model=ClaimPublicStatus)
def status(
    body: Annotated[dict[str, object], Body()],
    token: Annotated[str, Depends(require_claim_cookie)],
    service: Annotated[ProfileClaimService, Depends(get_profile_claim_service)],
) -> ClaimPublicStatus:
    if body:
        raise ProblemError(status=422, code="validation_error", detail="状态查询不接受业务资料")
    try:
        return service.status(token)
    except VerificationServiceError as error:
        raise _problem(error) from error
